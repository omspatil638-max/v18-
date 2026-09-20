"""Phase 5: timeline, dashboard deadline feed, renewal deadline, alerts, email and .ics export."""

import re
from datetime import date, timedelta

import pytest

from app.core.config import settings
from app.services import alert_service, job_runner
from app.services.ics_service import build_ics
from tests.helpers import upload, upload_and_process

PASSWORD = "correct horse battery"


def make_pdf(tmp_path, body: str) -> bytes:
    from sample_data.generate import write_pdf
    p = tmp_path / "x.pdf"
    write_pdf(p, body)
    return p.read_bytes()


# ─── the obligation timeline ──────────────────────────────────────────────────

async def test_schedule_lists_dated_items_with_party_basis_and_source(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    s = (await client.get(f"/api/contracts/{d['id']}/schedule")).json()
    assert s["contract_version_id"] == d["current_version_id"]
    kinds = {e["kind"] for e in s["entries"]}
    assert {"contract_date", "renewal_notice", "obligation", "occurrence"} <= kinds
    dates = [e["due_on"] for e in s["entries"]]
    assert dates == sorted(dates)

    audit = next(e for e in s["entries"] if e["party"] == "Customer" and e["due_on"] == "2027-03-31")
    assert audit["rule_type"] == "fixed" and "security audit" in audit["action"] and audit["source_page"] >= 4
    rn = next(e for e in s["entries"] if e["kind"] == "renewal_notice")
    assert rn["due_on"] == "2028-11-01" and rn["party"] is None and "minus 60 days" in rn["basis"]

    q = [e for e in s["entries"] if e["party"] == "Provider" and e["recurrence"] == "QUARTERLY"]
    assert len(q) >= 3 and all(e["kind"] in ("obligation", "occurrence") for e in q)
    assert all(e["basis"] and "quarter" in e["basis"] for e in q)


async def test_obligations_whose_timing_cannot_be_dated_are_listed_not_dropped(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    s = (await client.get(f"/api/contracts/{d['id']}/schedule")).json()
    dated_ids = {e["obligation_id"] for e in s["entries"] if e["obligation_id"]}
    undated_ids = {u["obligation_id"] for u in s["undated"]}
    assert dated_ids.isdisjoint(undated_ids)
    assert len(dated_ids) + len(undated_ids) == len(d["obligations"])         # nothing silently disappears
    assert all(u["due_rule"] and u["source_page"] for u in s["undated"][:3])


async def test_relative_due_date_is_computed_from_the_effective_date(client, llm, tmp_path):
    body = ("SERVICES AGREEMENT\n\nThis agreement is between Alpha Ltd and Beta Inc. It is entered into as of January 15, 2026.\n\n"
            "1. Kickoff\nCustomer shall deliver its onboarding data within thirty (30) days of the effective date.\n")
    orig = llm._extract

    def with_relative(user):
        out = orig(user)
        if "onboarding data" in user:
            out["obligations"].append({
                "responsible_party": "Customer", "action": "Deliver its onboarding data",
                "due_rule": "within thirty (30) days of the effective date", "due_date": None,
                "source_quote": "Customer shall deliver its onboarding data within thirty (30) days of the effective date.",
                "confidence": 0.9})
        return out

    llm._extract = with_relative
    d = await upload_and_process(client, make_pdf(tmp_path, body))
    ob = next(o for o in d["obligations"] if "onboarding" in o["action"])
    assert ob["due_rule_type"] == "relative" and ob["due_computed"] is True
    assert ob["due_date"] == "2026-02-14" and "30 days after the effective date (2026-01-15)" == ob["due_basis"]


async def test_event_based_rule_is_shown_without_an_invented_date_and_says_why(client, llm, tmp_path):
    body = ("SERVICES AGREEMENT\n\nThis agreement is between Alpha Ltd and Beta Inc. It is entered into as of January 15, 2026.\n\n"
            "1. Payment\nCustomer shall pay each invoice within thirty (30) days of receipt of the invoice.\n")
    orig = llm._extract

    def with_invoice(user):
        out = orig(user)
        if "each invoice" in user:
            out["obligations"].append({
                "responsible_party": "Customer", "action": "Pay each invoice",
                "due_rule": "within thirty (30) days of receipt of the invoice", "due_date": None,
                "source_quote": "Customer shall pay each invoice within thirty (30) days of receipt of the invoice.",
                "confidence": 0.9})
        return out

    llm._extract = with_invoice
    d = await upload_and_process(client, make_pdf(tmp_path, body))
    ob = next(o for o in d["obligations"] if "invoice" in o["action"])
    assert ob["due_date"] is None and ob["due_computed"] is False
    assert "receipt of the invoice" in ob["due_reason"] and "no calendar date can be worked out" in ob["due_reason"]
    s = (await client.get(f"/api/contracts/{d['id']}/schedule")).json()
    assert any(u["action"] == "Pay each invoice" and "invoice" in u["reason"] for u in s["undated"])


async def test_schedule_is_owner_only(client, new_client, monkeypatch, llm, long_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    cid = (await upload(a, long_pdf)).json()["id"]
    await job_runner.wait_idle()
    for path in (f"/api/contracts/{cid}/schedule", f"/api/contracts/{cid}/calendar.ics"):
        assert (await b.get(path)).status_code == 404 and (await anon.get(path)).status_code == 401
    assert (await b.get("/api/deadlines")).json() == [] and (await b.get("/api/calendar.ics")).text.count("BEGIN:VEVENT") == 0


# ─── the dashboard feed ───────────────────────────────────────────────────────

async def test_deadline_feed_supports_30_60_90_windows_and_context(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    today = date.today()
    by = {n: (await client.get(f"/api/deadlines?days_ahead={n}")).json() for n in (30, 60, 90, 365)}
    for n, rows in by.items():
        assert all(0 <= r["days_until"] <= n for r in rows), n
        assert all(r["contract_title"] and r["is_overdue"] is False for r in rows)
    assert len(by[30]) <= len(by[60]) <= len(by[90]) <= len(by[365])
    assert [r["deadline_date"] for r in by[365]] == sorted(r["deadline_date"] for r in by[365])
    assert any(r["responsible_party"] == "Provider" for r in by[365])


async def test_overdue_deadlines_are_flagged_and_can_be_included(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    from app.db.database import AsyncSessionLocal
    from app.models.models import Deadline
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        dl = (await db.execute(select(Deadline).where(Deadline.deadline_type == "OBLIGATION"))).scalars().first()
        dl.deadline_date = date.today() - timedelta(days=5)
        oid = dl.id
        await db.commit()
    assert oid not in {r["id"] for r in (await client.get("/api/deadlines")).json()}
    late = (await client.get("/api/deadlines?include_overdue=true")).json()
    row = next(r for r in late if r["id"] == str(oid))
    assert row["is_overdue"] is True and row["days_until"] == -5


async def test_completing_an_obligation_removes_its_deadlines_and_alerts(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    audit = next(o for o in d["obligations"] if "security audit" in o["action"])
    before_dl = {r["id"] for r in (await client.get("/api/deadlines?days_ahead=1000")).json()}
    before_al = {a["id"] for a in (await client.get("/api/alerts")).json()}
    await client.patch(f"/api/obligations/{audit['id']}", json={"status": "COMPLETED"})
    after_dl = (await client.get("/api/deadlines?days_ahead=1000")).json()
    assert not any(r["obligation_status"] == "COMPLETED" for r in after_dl)
    assert len(after_dl) < len(before_dl)
    assert len((await client.get("/api/alerts")).json()) < len(before_al)


async def test_obligation_overdue_flag_is_derived_at_read_time(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    from app.db.database import AsyncSessionLocal
    from app.models.models import Obligation
    import uuid
    async with AsyncSessionLocal() as db:
        ob = await db.get(Obligation, uuid.UUID(d["obligations"][0]["id"]))
        ob.due_date = date.today() - timedelta(days=3)
        await db.commit()
    obs = (await client.get(f"/api/contracts/{d['id']}/obligations")).json()
    row = next(o for o in obs if o["id"] == d["obligations"][0]["id"])
    assert row["is_overdue"] is True
    await client.patch(f"/api/obligations/{row['id']}", json={"status": "COMPLETED"})
    obs = (await client.get(f"/api/contracts/{d['id']}/obligations")).json()
    assert next(o for o in obs if o["id"] == row["id"])["is_overdue"] is False


# ─── alerts and their settings ────────────────────────────────────────────────

async def test_alert_settings_roundtrip_and_validation(client):
    s = (await client.get("/api/alerts/settings")).json()
    assert s["lead_days"] == [30, 14, 7] and s["email_enabled"] is False and s["smtp_configured"] is False
    r = await client.put("/api/alerts/settings", json={"lead_days": [3, 60, 14, 60]})
    assert r.status_code == 200 and r.json()["lead_days"] == [60, 14, 3]
    assert (await client.put("/api/alerts/settings", json={"lead_days": [0]})).status_code == 422
    assert (await client.put("/api/alerts/settings", json={"lead_days": [400]})).status_code == 422
    assert (await client.put("/api/alerts/settings", json={"lead_days": [1, 2, 3, 4, 5, 6, 7]})).status_code == 422
    assert (await client.put("/api/alerts/settings", json={"email_to": "not-an-email"})).status_code == 422
    assert (await client.put("/api/alerts/settings", json={"email_enabled": True})).status_code == 422       # needs an address


async def test_changing_lead_times_rebuilds_pending_alerts_and_keeps_acknowledged_ones(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    alerts = (await client.get("/api/alerts?unacknowledged_only=false")).json()
    keep = alerts[0]["id"]
    await client.patch(f"/api/alerts/{keep}/acknowledge")

    await client.put("/api/alerts/settings", json={"lead_days": [100, 5]})
    now = (await client.get("/api/alerts?unacknowledged_only=false")).json()
    scheduled = [a for a in now if a["status"] == "PENDING" and a["fire_on"] != date.today().isoformat()]
    assert scheduled and {a["lead_days"] for a in scheduled} <= {100, 5}
    kept = next(a for a in now if a["id"] == keep)
    assert kept["status"] == "ACKNOWLEDGED"                               # a person's decision is not undone
    ids = [(a["deadline_id"], a["lead_days"]) for a in now]
    assert len(ids) == len(set(ids)), "no duplicate alerts per (deadline, lead time)"


async def test_due_only_returns_alerts_whose_lead_time_has_arrived(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    due = (await client.get("/api/alerts?due_only=true")).json()
    assert all(a["fire_on"] <= date.today().isoformat() for a in due)
    every = (await client.get("/api/alerts")).json()
    assert len(due) <= len(every)


# ─── email (optional) ─────────────────────────────────────────────────────────

@pytest.fixture
def smtp(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "alerts@contractlens.test")
    alert_service.set_sender(sent.append)
    yield sent
    alert_service.set_sender(None)


async def test_email_not_configured_is_reported_honestly(client):
    await client.put("/api/alerts/settings", json={"email_to": "me@example.com"})
    r = await client.post("/api/alerts/test-email")
    assert r.status_code == 503 and "not configured" in r.json()["detail"]
    assert (await alert_service.run_alert_cycle()) == {
        "users_emailed": 0, "alerts_sent": 0, "reminders_sent": 0, "failed": 0, "skipped_no_smtp": 0,
        "users_pushed": 0, "push_sent": 0, "push_failed": 0}


async def test_test_email_is_sent_when_smtp_is_configured(client, smtp):
    await client.put("/api/alerts/settings", json={"email_to": "me@example.com"})
    r = await client.post("/api/alerts/test-email")
    assert r.status_code == 200 and len(smtp) == 1
    assert smtp[0]["To"] == "me@example.com" and "not legal advice" in smtp[0].get_content().lower()


async def test_due_alerts_are_emailed_once_as_a_single_digest(client, llm, long_pdf, smtp):
    await upload_and_process(client, long_pdf)
    await client.put("/api/alerts/settings", json={"email_to": "me@example.com", "email_enabled": True})
    far = date.today() + timedelta(days=2000)                      # everything is "due" by then
    r1 = await alert_service.run_alert_cycle(today=far)
    assert r1["users_emailed"] == 1 and r1["alerts_sent"] >= 3 and r1["failed"] == 0
    assert len(smtp) == 1
    body = smtp[0].get_content()
    assert "deadline(s) need attention" in body
    assert "Renewal notice deadline" in body and "2028-11-01" in body and "Source: page" in body
    assert "not legal advice" in body.lower()
    assert body.count("Renewal notice deadline") == 1              # one line per deadline, not per alert

    r2 = await alert_service.run_alert_cycle(today=far)            # idempotent: nothing is sent twice
    assert r2["alerts_sent"] == 0 and len(smtp) == 1
    statuses = {a["status"] for a in (await client.get("/api/alerts?unacknowledged_only=false")).json()}
    assert "SENT" in statuses


async def test_email_is_not_sent_unless_the_user_turned_it_on(client, llm, long_pdf, smtp):
    await upload_and_process(client, long_pdf)
    await client.put("/api/alerts/settings", json={"email_to": "me@example.com"})           # address saved, email off
    r = await alert_service.run_alert_cycle(today=date.today() + timedelta(days=2000))
    assert r["alerts_sent"] == 0 and smtp == []


async def test_a_failed_send_is_retried_next_cycle_and_leaks_no_credentials(client, llm, long_pdf, monkeypatch, caplog):
    await upload_and_process(client, long_pdf)
    await client.put("/api/alerts/settings", json={"email_to": "me@example.com", "email_enabled": True})
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "alerts@contractlens.test")
    from pydantic import SecretStr
    monkeypatch.setattr(settings, "SMTP_PASSWORD", SecretStr("hunter2-super-secret"))

    def boom(_msg):
        raise ConnectionRefusedError("connect failed for hunter2-super-secret")

    alert_service.set_sender(boom)
    far = date.today() + timedelta(days=2000)
    r = await alert_service.run_alert_cycle(today=far)
    assert r["failed"] == 1 and r["alerts_sent"] == 0
    assert "hunter2" not in caplog.text
    sent = []
    alert_service.set_sender(sent.append)
    r2 = await alert_service.run_alert_cycle(today=far)
    assert r2["alerts_sent"] > 0 and len(sent) == 1
    alert_service.set_sender(None)


async def test_the_smtp_password_never_appears_in_settings_output(monkeypatch):
    from pydantic import SecretStr
    monkeypatch.setattr(settings, "SMTP_PASSWORD", SecretStr("hunter2-super-secret"))
    assert "hunter2" not in repr(settings) and "hunter2" not in settings.model_dump_json()


# ─── .ics calendar export ─────────────────────────────────────────────────────

def unfold(text: str) -> str:
    return re.sub(r"\r\n[ \t]", "", text)


async def test_contract_ics_is_a_valid_calendar_with_reminders_and_sources(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf, "MSA.pdf")
    r = await client.get(f"/api/contracts/{d['id']}/calendar.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert 'attachment; filename="' in r.headers["content-disposition"] and r.headers["content-disposition"].endswith('.ics"')
    body = unfold(r.text)
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert body.count("BEGIN:VEVENT") == body.count("END:VEVENT") >= 3
    assert "DTSTART;VALUE=DATE:20281101" in body                          # the renewal notice deadline
    assert "TRIGGER:-P30D" in body and "TRIGGER:-P7D" in body             # one reminder per configured lead time
    assert "Source: page" in body and "AI-assisted" in body
    assert all(len(line.encode()) <= 75 for line in r.text.split("\r\n"))     # folded per RFC 5545


async def test_all_deadlines_ics_and_custom_reminder_lead_times(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    await client.put("/api/alerts/settings", json={"lead_days": [45, 3]})
    body = unfold((await client.get("/api/calendar.ics")).text)
    assert "TRIGGER:-P45D" in body and "TRIGGER:-P3D" in body and "TRIGGER:-P30D" not in body


async def test_ics_escaping_and_uid_stability():
    ev = {"id": "abc", "date": date(2027, 3, 31), "title": "Acme, Inc; \"Q1\" report", "description": "line1\nline2, with; punctuation\\"}
    a, b = build_ics([ev]), build_ics([ev])
    assert "SUMMARY:Acme\\, Inc\\; \"Q1\" report" in a
    assert "line1\\nline2\\, with\\; punctuation\\\\" in unfold(a)
    uid = lambda s: re.search(r"UID:(\S+)", s).group(1)
    assert uid(a) == uid(b) and uid(a).endswith("@contractlens")
    assert "DTEND;VALUE=DATE:20270401" in a                                # all-day event ends the next day


async def test_soft_deleted_contract_is_absent_from_calendar_and_feed(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await client.delete(f"/api/contracts/{d['id']}")
    assert (await client.get("/api/calendar.ics")).text.count("BEGIN:VEVENT") == 0
    assert (await client.get("/api/deadlines?days_ahead=1800")).json() == []
    assert (await client.get(f"/api/contracts/{d['id']}/calendar.ics")).status_code == 404
