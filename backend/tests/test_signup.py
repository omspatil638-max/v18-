"""First-run sign-up (name, phone, email), email verification, and the morning briefing."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.services import alert_service, job_runner
from tests.helpers import upload, upload_and_process

PASSWORD = "correct horse battery"
GOOD = {"name": "Asha Rao", "email": "asha@example.com", "phone": "+91 98765 43210", "password": PASSWORD,
        "accept_terms": True, "timezone": "Asia/Kolkata"}
FAR = date.today() + timedelta(days=2000)


@pytest.fixture(autouse=True)
def auto_mode(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "auto")


@pytest.fixture
def smtp(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "alerts@contractlens.test")
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://app.test")
    alert_service.set_sender(sent.append)
    yield sent
    alert_service.set_sender(None)


async def signup(client, **over):
    return await client.post("/api/auth/signup", json={**GOOD, **over})


def code_from(msg) -> str:
    return re.search(r"\b(\d{6})\b", msg.get_content()).group(1)


# ─── the first screen ─────────────────────────────────────────────────────────

async def test_a_fresh_install_is_in_setup_until_someone_signs_up(client):
    assert (await client.get("/api/auth/config")).json() == {"mode": "setup", "registration_open": True, "email_verification_available": False}
    assert (await client.get("/api/contracts")).status_code == 200            # the local data still works while setting up
    assert (await client.post("/api/auth/login", json={"email": "a@x.com", "password": PASSWORD})).status_code == 400
    r = await signup(client)
    assert r.status_code == 201
    assert (await client.get("/api/auth/config")).json()["mode"] == "login"


async def test_demo_mode_has_no_accounts(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "demo")
    assert (await client.get("/api/auth/config")).json()["mode"] == "demo"
    assert (await signup(client)).status_code == 400


async def test_the_first_account_takes_over_the_data_already_stored_locally(client, new_client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)                            # made in setup state, as the demo user
    r = await signup(client)
    body = r.json()
    assert r.status_code == 201 and body["claimed_existing_data"] is True
    u = body["user"]
    assert u["name"] == "Asha Rao" and u["email"] == "asha@example.com" and u["phone"] == "+919876543210"
    assert u["email_verified"] is False and u["onboarded"] is False
    mine = (await client.get("/api/contracts")).json()
    assert [c["id"] for c in mine] == [d["id"]]                               # nothing was orphaned
    assert (await client.get("/api/auth/me")).json()["email"] == "asha@example.com"
    other = new_client()
    assert (await other.get("/api/contracts")).status_code == 401              # signed-out visitors are locked out now
    assert (await other.post("/api/auth/login", json={"email": "asha@example.com", "password": PASSWORD})).status_code == 200
    assert len((await other.get("/api/contracts")).json()) == 1


async def test_the_next_person_gets_their_own_empty_account(client, new_client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    await signup(client)
    b = new_client()
    r = await b.post("/api/auth/signup", json={**GOOD, "name": "Ben", "email": "ben@example.com", "phone": "020 7946 0018"})
    assert r.status_code == 201 and r.json()["claimed_existing_data"] is False
    assert (await b.get("/api/contracts")).json() == []
    assert len((await client.get("/api/contracts")).json()) == 1


async def test_registration_can_be_closed_but_never_blocks_the_first_account(client, new_client, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_REGISTRATION", False)
    assert (await signup(client)).status_code == 201
    r = await new_client().post("/api/auth/signup", json={**GOOD, "email": "b@example.com"})
    assert r.status_code == 403


# ─── validation ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("over", [
    {"accept_terms": False}, {"name": "   "}, {"name": ""}, {"email": "not-an-email"}, {"email": "a@b"}, {"password": "short"},
    {"phone": ""}, {"phone": "abc"}, {"phone": "12345"}, {"phone": "+1234567890123456"}, {"phone": "5555555555"}, {"phone": "12-34-ab-5678"},
])
async def test_bad_sign_ups_are_refused_and_create_nothing(client, over):
    r = await signup(client, **over)
    assert r.status_code == 422, over
    assert (await client.get("/api/auth/config")).json()["mode"] == "setup"


@pytest.mark.parametrize("raw,stored", [
    ("+1 (415) 555-0134", "+14155550134"), ("415-555-0134", "4155550134"), ("+44 20 7946 0018", "+442079460018"),
    ("  98765.43210 ", "9876543210"),
])
async def test_phone_numbers_are_cleaned_up(client, raw, stored):
    assert (await signup(client, phone=raw)).json()["user"]["phone"] == stored


async def test_a_duplicate_email_is_refused(client, new_client):
    await signup(client)
    r = await new_client().post("/api/auth/signup", json={**GOOD, "name": "Someone Else", "email": "ASHA@example.com"})
    assert r.status_code == 409


async def test_the_password_is_never_returned_or_stored_in_clear(client):
    r = await signup(client)
    assert PASSWORD not in r.text and "password" not in r.json()["user"]
    from sqlalchemy import select
    from app.db.database import AsyncSessionLocal
    from app.models.models import User
    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(User).where(User.email == "asha@example.com"))).scalar_one()
        assert u.password_hash and PASSWORD not in u.password_hash and u.consented_at is not None


# ─── email verification ───────────────────────────────────────────────────────

async def test_without_email_sending_the_sign_up_says_verification_is_unavailable(client):
    r = await signup(client)
    assert r.json()["email_verification"] == "unavailable"
    assert (await client.post("/api/auth/resend-verification")).status_code == 503
    assert (await client.post("/api/auth/verify-email", json={"code": "123456"})).status_code == 422


async def test_a_code_is_sent_to_the_address_and_proves_it(client, smtp):
    r = await signup(client)
    assert r.json()["email_verification"] == "sent" and len(smtp) == 1
    assert smtp[0]["To"] == "asha@example.com" and "verification code" in smtp[0]["Subject"].lower()
    code = code_from(smtp[0])
    bad = await client.post("/api/auth/verify-email", json={"code": "000000" if code != "000000" else "111111"})
    assert bad.status_code == 422 and "attempt(s) left" in bad.json()["detail"]
    ok = await client.post("/api/auth/verify-email", json={"code": code})
    assert ok.status_code == 200 and ok.json()["email_verified"] is True
    st = (await client.get("/api/alerts/settings")).json()
    assert st["email_to"] == "asha@example.com" and st["email_enabled"] is True and st["briefing_enabled"] is True
    assert st["timezone"] == "Asia/Kolkata" and st["email_verified"] is True
    assert (await client.post("/api/auth/verify-email", json={"code": "999999"})).status_code == 200   # already verified


async def test_the_code_is_never_written_to_the_logs(client, smtp, caplog):
    with caplog.at_level("DEBUG"):
        await signup(client)
        await client.post("/api/auth/verify-email", json={"code": "123456"})
    code = code_from(smtp[0])
    assert code not in caplog.text


async def test_too_many_wrong_codes_lock_the_code(client, smtp):
    await signup(client)
    code = code_from(smtp[0])
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(5):
        assert (await client.post("/api/auth/verify-email", json={"code": wrong})).status_code == 422
    r = await client.post("/api/auth/verify-email", json={"code": code})
    assert r.status_code == 422 and "Too many wrong codes" in r.json()["detail"]


async def test_an_expired_code_is_refused_and_a_new_one_replaces_the_old(client, smtp, monkeypatch):
    await signup(client)
    first = code_from(smtp[0])
    from sqlalchemy import update
    from app.db.database import AsyncSessionLocal
    from app.models.models import EmailVerification
    async with AsyncSessionLocal() as db:
        await db.execute(update(EmailVerification).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                                                          created_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        await db.commit()
    assert "expired" in (await client.post("/api/auth/verify-email", json={"code": first})).json()["detail"]
    assert (await client.post("/api/auth/resend-verification")).status_code == 200
    second = code_from(smtp[1])
    assert (await client.post("/api/auth/verify-email", json={"code": first if first != second else "000001"})).status_code == 422
    assert (await client.post("/api/auth/verify-email", json={"code": second})).status_code == 200


async def test_codes_cannot_be_requested_in_a_flood(client, smtp):
    await signup(client)
    r = await client.post("/api/auth/resend-verification")
    assert r.status_code == 429 and "just sent" in r.json()["detail"]


async def test_a_failed_send_is_reported_not_hidden(client, smtp):
    alert_service.set_sender(lambda m: (_ for _ in ()).throw(ConnectionRefusedError("down")))
    assert (await signup(client)).json()["email_verification"] == "failed"


async def test_only_a_verified_address_is_ever_emailed(client, smtp, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    await signup(client)
    smtp.clear()
    await client.put("/api/alerts/settings", json={"email_to": "asha@example.com", "email_enabled": True})
    r = await alert_service.run_alert_cycle(today=FAR)
    assert r["users_emailed"] == 0 and smtp == []                              # unverified: nothing goes out
    await client.post("/api/auth/resend-verification") if False else None


# ─── profile and onboarding ───────────────────────────────────────────────────

async def test_profile_can_be_changed_and_onboarding_completed(client):
    await signup(client)
    me = (await client.get("/api/auth/me")).json()
    assert me["onboarded"] is False
    r = await client.put("/api/auth/profile", json={"name": "  Asha  B Rao ", "phone": "(415) 555-0134"})
    assert r.status_code == 200 and r.json()["name"] == "Asha B Rao" and r.json()["phone"] == "4155550134"
    assert (await client.put("/api/auth/profile", json={"phone": "nope"})).status_code == 422
    assert (await client.put("/api/auth/profile", json={"name": "  "})).status_code == 422
    assert (await client.post("/api/auth/onboarding/complete")).json()["onboarded"] is True


async def test_accounts_that_never_signed_up_count_as_onboarded(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "demo")
    assert (await client.get("/api/auth/me")).json()["onboarded"] is True


# ─── sample contract ──────────────────────────────────────────────────────────

async def test_a_sample_contract_can_be_loaded_and_is_clearly_labelled(client, llm):
    await signup(client)
    r = await client.post("/api/contracts/sample")
    assert r.status_code == 202 and "sample" in r.json()["title"].lower()
    await job_runner.wait_idle()
    d = (await client.get(f"/api/contracts/{r.json()['id']}")).json()
    assert d["status"] == "READY" and any(f["status"] == "verified" for f in d["fields"])


# ─── morning briefing ─────────────────────────────────────────────────────────

def at(hour, minute=0, day=15):
    return datetime(2030, 1, day, hour, minute, tzinfo=timezone.utc)


async def setup_briefing(client, monkeypatch, **extra):
    monkeypatch.setattr(settings, "AUTH_MODE", "demo")                        # a legacy account: no verification needed
    body = {"email_to": "me@example.com", "email_enabled": True, "briefing_enabled": True, "briefing_hour": 8, "timezone": "UTC", **extra}
    r = await client.put("/api/alerts/settings", json=body)
    assert r.status_code == 200, r.text


async def test_the_briefing_email_goes_out_once_a_day_at_the_chosen_hour(client, smtp, llm, long_pdf, monkeypatch):
    await upload_and_process(client, long_pdf)
    await setup_briefing(client, monkeypatch)
    assert (await alert_service.run_alert_cycle(today=FAR, now=at(7, 0)))["users_emailed"] == 0          # before 08:00: wait
    r = await alert_service.run_alert_cycle(today=FAR, now=at(8, 30))
    assert r["users_emailed"] == 1 and "briefing" in smtp[0]["Subject"].lower() and "Sent late" not in smtp[0].get_content()
    assert (await alert_service.run_alert_cycle(today=FAR, now=at(9, 0)))["users_emailed"] == 0          # once per day
    assert (await alert_service.run_alert_cycle(today=FAR, now=at(8, 30, day=16)))["users_emailed"] == 1  # next morning: a reminder
    assert len(smtp) == 2 and "reminder" in smtp[1]["Subject"].lower() or "briefing" in smtp[1]["Subject"].lower()


async def test_a_late_briefing_says_why(client, smtp, llm, long_pdf, monkeypatch):
    await upload_and_process(client, long_pdf)
    await setup_briefing(client, monkeypatch)
    await alert_service.run_alert_cycle(today=FAR, now=at(13, 0))                # the app was off at 08:00
    body = smtp[0].get_content()
    assert "Sent late" in body and "08:00" in body and "Nothing was lost" in body


async def test_the_briefing_hour_follows_the_users_time_zone(client, smtp, llm, long_pdf, monkeypatch):
    await upload_and_process(client, long_pdf)
    await setup_briefing(client, monkeypatch, timezone="Asia/Kolkata")            # UTC+5:30
    assert (await alert_service.run_alert_cycle(today=FAR, now=at(2, 0)))["users_emailed"] == 0          # 07:30 in India
    assert (await alert_service.run_alert_cycle(today=FAR, now=at(3, 0)))["users_emailed"] == 1          # 08:30 in India


async def test_pausing_stops_every_channel_until_the_date_and_resuming_restores_it(client, smtp, llm, long_pdf, monkeypatch):
    await upload_and_process(client, long_pdf)
    await setup_briefing(client, monkeypatch, pause_days=3)
    st = (await client.get("/api/alerts/settings")).json()
    assert st["paused_until"] == (date.today() + timedelta(days=3)).isoformat()
    assert (await alert_service.run_alert_cycle(today=FAR, now=datetime.now(timezone.utc).replace(hour=12)))["users_emailed"] == 0
    await client.put("/api/alerts/settings", json={"pause_days": 0})
    assert (await client.get("/api/alerts/settings")).json()["paused_until"] is None
    assert (await alert_service.run_alert_cycle(today=FAR, now=datetime.now(timezone.utc).replace(hour=12)))["users_emailed"] == 1


async def test_briefing_settings_are_validated(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "demo")
    for bad in ({"briefing_hour": 24}, {"briefing_hour": -1}, {"timezone": "Mars/Base"}, {"pause_days": 91}, {"pause_days": -1}):
        assert (await client.put("/api/alerts/settings", json=bad)).status_code == 422, bad
    ok = (await client.put("/api/alerts/settings", json={"briefing_hour": 6, "timezone": "Europe/London"})).json()
    assert ok["briefing_hour"] == 6 and ok["timezone"] == "Europe/London" and ok["briefing_enabled"] is False


async def test_the_preview_shows_the_next_briefing_without_sending_anything(client, smtp, llm, long_pdf, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "demo")
    empty = (await client.get("/api/alerts/briefing/preview")).json()
    assert empty["count"] == 0 and empty["would_send"] is False and "never sends an empty briefing" in empty["reason"]
    await upload_and_process(client, long_pdf)
    monkeypatch.setattr(alert_service, "date", type("D", (date,), {"today": classmethod(lambda cls: FAR)}))
    await client.put("/api/alerts/settings", json={"email_to": "me@example.com", "email_enabled": True})
    p = (await client.get("/api/alerts/briefing/preview")).json()
    assert p["count"] >= 3 and p["would_send"] is True and "Renewal notice deadline" in p["body"] and "not legal advice" in p["body"].lower()
    assert p["items"][0]["days_left"] is not None and smtp == []                # nothing was sent
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    assert "not set up" in (await client.get("/api/alerts/briefing/preview")).json()["reason"]


async def test_the_briefing_never_goes_to_an_unverified_account_address_but_does_after_verifying(client, smtp, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    await signup(client)
    code = code_from(smtp[0])
    await client.post("/api/auth/verify-email", json={"code": code})
    smtp.clear()
    st = (await client.get("/api/alerts/settings")).json()
    assert st["briefing_enabled"] is True and st["email_enabled"] is True
    r = await alert_service.run_alert_cycle(today=FAR, now=datetime(2030, 1, 15, 4, 0, tzinfo=timezone.utc))    # 09:30 in Kolkata
    assert r["users_emailed"] == 1 and smtp[0]["To"] == "asha@example.com"
