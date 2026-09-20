"""Phase 2: the unified data model — versions, documents, soft delete, alerts, isolation."""

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.services import job_runner
from tests.helpers import upload, upload_and_process, upload_version

PASSWORD = "correct horse battery"


def make_pdf(tmp_path, text_body: str, name="v.pdf") -> bytes:
    from sample_data.generate import write_pdf
    p = tmp_path / name
    write_pdf(p, text_body)
    return p.read_bytes()


def amended_text() -> str:
    """The sample contract with a 60-day termination notice, an earlier expiry and a new fee."""
    from sample_data.generate import long_contract_text
    t = long_contract_text()
    t = t.replace("ninety (90) days written notice", "sixty (60) days written notice")
    t = t.replace("December 31, 2028", "June 30, 2028")
    t = t.replace("$12,500", "$14,000")
    return t


# ─── the new model ────────────────────────────────────────────────────────────

async def test_upload_creates_contract_with_version_one_and_a_document(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf, "MSA.pdf")
    assert d["version_count"] == 1 and len(d["versions"]) == 1
    v1 = d["versions"][0]
    assert v1["version_number"] == 1 and v1["label"] == "v1" and v1["filename"] == "MSA.pdf"
    assert d["current_version_id"] == v1["id"]
    assert len(d["documents"]) == 1 and d["documents"][0]["kind"] == "MAIN"
    assert d["documents"][0]["version_id"] == v1["id"]


async def test_every_extracted_item_carries_its_contract_version_id(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    vid = d["current_version_id"]
    for group in ("fields", "parties", "clauses", "obligations", "deadlines"):
        assert d[group], f"expected some {group}"
        assert all(item["contract_version_id"] == vid for item in d[group]), group


async def test_responses_never_expose_storage_keys_or_raw_text(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    blob = str(d)
    for forbidden in ("storage_key", "storage_path", "raw_text", "sha256"):
        assert forbidden not in blob


async def test_upload_second_version_keeps_v1_data_and_becomes_current(client, llm, long_pdf, tmp_path):
    d1 = await upload_and_process(client, long_pdf, "msa_v1.pdf")
    cid, v1_id = d1["id"], d1["current_version_id"]

    v2 = await upload_version(client, cid, make_pdf(tmp_path, amended_text()), "msa_v2.pdf", label="v2 - renegotiated")
    assert v2["version_number"] == 2 and v2["label"] == "v2 - renegotiated"

    d = (await client.get(f"/api/contracts/{cid}")).json()
    assert d["version_count"] == 2 and d["current_version_id"] == v2["id"]
    assert [v["label"] for v in d["versions"]] == ["v1", "v2 - renegotiated"]
    f = {x["field_key"]: x for x in d["fields"]}
    assert f["expiration_date"]["display_value"] == "2028-06-30"
    assert f["termination_notice_period"]["value"]["days"] == 60

    old = (await client.get(f"/api/contracts/{cid}?version_id={v1_id}")).json()
    fo = {x["field_key"]: x for x in old["fields"]}
    assert fo["expiration_date"]["display_value"] == "2028-12-31"       # v1 is untouched
    assert fo["termination_notice_period"]["value"]["days"] == 90
    assert all(x["contract_version_id"] == v1_id for x in old["fields"])


async def test_versions_do_not_bleed_into_each_other(client, llm, long_pdf, tmp_path):
    d1 = await upload_and_process(client, long_pdf)
    cid, v1 = d1["id"], d1["current_version_id"]
    v2 = (await upload_version(client, cid, make_pdf(tmp_path, amended_text())))["id"]

    for path, key in (("obligations", "contract_version_id"), ("clauses", "contract_version_id"),
                      ("deadlines", "contract_version_id"), ("fields", "contract_version_id")):
        a = (await client.get(f"/api/contracts/{cid}/{path}?version_id={v1}")).json()
        b = (await client.get(f"/api/contracts/{cid}/{path}?version_id={v2}")).json()
        assert a and b, path
        assert {x[key] for x in a} == {v1} and {x[key] for x in b} == {v2}, path

    # The dashboard's deadline feed uses only the CURRENT version, so it doesn't double count.
    dl = (await client.get("/api/deadlines?days_ahead=1825")).json()
    assert {x["contract_version_id"] for x in dl} == {v2}


async def test_current_version_can_be_left_unchanged_when_uploading(client, llm, long_pdf, tmp_path):
    d1 = await upload_and_process(client, long_pdf)
    v = await upload_version(client, d1["id"], make_pdf(tmp_path, amended_text()), make_current="false")
    d = (await client.get(f"/api/contracts/{d1['id']}")).json()
    assert d["current_version_id"] == d1["current_version_id"] != v["id"]


async def test_version_id_from_another_contract_is_rejected(client, llm, long_pdf, tmp_path):
    a = await upload_and_process(client, long_pdf, "a.pdf")
    b = await upload_and_process(client, long_pdf, "b.pdf")
    r = await client.get(f"/api/contracts/{a['id']}?version_id={b['current_version_id']}")
    assert r.status_code == 404


async def test_related_document_kind_is_recorded(client, llm, long_pdf, tmp_path):
    d1 = await upload_and_process(client, long_pdf)
    await upload_version(client, d1["id"], make_pdf(tmp_path, "AMENDMENT No. 1\nThis amendment changes the fee."),
                         "amend.pdf", kind="AMENDMENT", label="Amendment 1")
    docs = (await client.get(f"/api/contracts/{d1['id']}/documents")).json()
    assert sorted(x["kind"] for x in docs) == ["AMENDMENT", "MAIN"]


async def test_each_versions_file_is_served_separately(client, llm, long_pdf, tmp_path):
    d1 = await upload_and_process(client, long_pdf)
    v2_pdf = make_pdf(tmp_path, amended_text())
    v2 = await upload_version(client, d1["id"], v2_pdf)
    r1 = await client.get(f"/api/contracts/{d1['id']}/file?version_id={d1['current_version_id']}")
    r2 = await client.get(f"/api/contracts/{d1['id']}/file?version_id={v2['id']}")
    assert r1.content == long_pdf and r2.content == v2_pdf and r1.content != r2.content


async def test_chat_answers_from_the_requested_version(client, llm, long_pdf, tmp_path):
    d1 = await upload_and_process(client, long_pdf)
    v2 = await upload_version(client, d1["id"], make_pdf(tmp_path, amended_text()))
    a = (await client.post(f"/api/contracts/{d1['id']}/chat?version_id={v2['id']}",
                           json={"content": "Can we terminate early?"})).json()
    assert a["contract_version_id"] == v2["id"] and a["answer_status"] == "answered"
    assert "sixty" in a["citations"][0]["snippet"].lower()


# ─── soft delete ──────────────────────────────────────────────────────────────

async def test_delete_is_soft_and_can_be_undone(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    cid = d["id"]
    assert (await client.delete(f"/api/contracts/{cid}")).status_code == 204

    assert (await client.get(f"/api/contracts/{cid}")).status_code == 404
    assert (await client.get("/api/contracts")).json() == []
    assert (await client.get("/api/deadlines?days_ahead=1825")).json() == []          # hidden from the feeds too
    assert (await client.get("/api/alerts")).json() == []
    assert len((await client.get("/api/contracts?include_deleted=true")).json()) == 1

    restored = await client.post(f"/api/contracts/{cid}/restore")
    assert restored.status_code == 200 and restored.json()["deleted_at"] is None
    assert (await client.get(f"/api/contracts/{cid}")).status_code == 200
    assert len((await client.get("/api/contracts")).json()) == 1
    assert (await client.get(f"/api/contracts/{cid}")).json()["fields"]              # data survived


async def test_permanent_delete_removes_rows_and_every_file(client, llm, long_pdf, tmp_path):
    d = await upload_and_process(client, long_pdf)
    await upload_version(client, d["id"], make_pdf(tmp_path, amended_text()))
    storage = settings.absolute_storage_path
    assert len(list(storage.glob("*.pdf"))) == 2

    assert (await client.delete(f"/api/contracts/{d['id']}?permanent=true")).status_code == 204
    assert list(storage.glob("*.pdf")) == []
    assert (await client.get(f"/api/contracts/{d['id']}?include_deleted=true")).status_code == 404

    from app.db.database import engine
    async with engine.connect() as conn:
        for table in ("contract_versions", "documents", "extracted_fields", "deadlines", "alerts", "contract_chunks"):
            n = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
            assert n == 0, f"{table} still has {n} rows"


async def test_soft_deleted_contract_cannot_be_changed(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    ob = d["obligations"][0]["id"]
    await client.delete(f"/api/contracts/{d['id']}")
    assert (await client.patch(f"/api/obligations/{ob}", json={"status": "COMPLETED"})).status_code == 404
    assert (await client.post(f"/api/contracts/{d['id']}/chat", json={"content": "hi there"})).status_code == 404


# ─── alerts ───────────────────────────────────────────────────────────────────

async def test_alerts_are_created_from_extracted_deadlines_and_can_be_acknowledged(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    alerts = (await client.get("/api/alerts")).json()
    assert alerts, "expected alerts for the future deadlines"
    assert {a["lead_days"] for a in alerts} <= {30, 14, 7} or all(a["lead_days"] >= 0 for a in alerts)
    assert all(a["status"] == "PENDING" and a["acknowledged"] is False for a in alerts)
    assert all(a["contract_title"] and a["deadline_date"] for a in alerts)

    first = alerts[0]["id"]
    assert (await client.patch(f"/api/alerts/{first}/acknowledge")).status_code == 200
    remaining = (await client.get("/api/alerts")).json()
    assert first not in {a["id"] for a in remaining}
    everything = (await client.get("/api/alerts?unacknowledged_only=false")).json()
    assert next(a for a in everything if a["id"] == first)["acknowledged"] is True


async def test_legacy_reminders_urls_still_work(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    old = (await client.get("/api/reminders")).json()
    assert old and {"remind_at", "acknowledged", "message", "days_until"} <= set(old[0])
    assert (await client.patch(f"/api/reminders/{old[0]['id']}/acknowledge")).status_code == 200


async def test_alerts_are_not_duplicated_by_reprocessing(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    before = len((await client.get("/api/alerts?unacknowledged_only=false")).json())
    for _ in range(2):
        await client.post(f"/api/contracts/{d['id']}/reprocess")
        await job_runner.wait_idle()
    after = len((await client.get("/api/alerts?unacknowledged_only=false")).json())
    assert before == after > 0


async def test_custom_lead_times_from_user_settings_are_used(client, llm, long_pdf):
    from app.db.database import AsyncSessionLocal
    from app.models.models import AlertSetting, User
    from sqlalchemy import select
    d0 = await upload_and_process(client, long_pdf)            # creates the demo user
    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(User))).scalars().first()
        db.add(AlertSetting(user_id=u.id, lead_days=[365, 90]))
        await db.commit()
    await client.post(f"/api/contracts/{d0['id']}/reprocess")
    await job_runner.wait_idle()
    from datetime import date
    alerts = (await client.get("/api/alerts?unacknowledged_only=false")).json()
    assert alerts, "expected alerts"
    # A deadline nearer than every lead time gets ONE alert firing today (so it is not missed); those are
    # excluded here. Every other alert must use the user's own lead times, never the server defaults.
    scheduled = [a for a in alerts if a["fire_on"] != date.today().isoformat()]
    leads = {a["lead_days"] for a in scheduled}
    assert leads <= {365, 90}, f"only the user's lead times may be used, got {leads}"
    assert 90 in leads and 365 in leads
    assert leads.isdisjoint({30, 14, 7}), "server defaults must not be applied when the user set their own"


# ─── ownership across the new endpoints ───────────────────────────────────────

async def test_new_version_endpoints_are_owner_only(client, new_client, monkeypatch, llm, long_pdf, tmp_path):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, email in ((a, "a@x.com"), (b, "b@x.com")):
        r = await c.post("/api/auth/register", json={"email": email, "name": "T", "password": PASSWORD})
        assert r.status_code == 201
    cid = (await upload(a, long_pdf, "a.pdf")).json()["id"]
    await job_runner.wait_idle()
    vid = (await a.get(f"/api/contracts/{cid}")).json()["current_version_id"]

    pdf = make_pdf(tmp_path, amended_text())
    files = {"file": ("v2.pdf", pdf, "application/pdf")}
    assert (await b.post(f"/api/contracts/{cid}/versions", files=files)).status_code == 404
    assert (await anon.post(f"/api/contracts/{cid}/versions", files=files)).status_code == 401
    for path in (f"/api/contracts/{cid}/versions", f"/api/contracts/{cid}/documents", f"/api/contracts/{cid}/flags",
                 f"/api/contracts/{cid}?version_id={vid}", f"/api/contracts/{cid}/obligations?version_id={vid}",
                 f"/api/contracts/{cid}/file?version_id={vid}", f"/api/contracts/{cid}/text?version_id={vid}"):
        assert (await b.get(path)).status_code == 404, path
        assert (await anon.get(path)).status_code == 401, path
    assert (await b.post(f"/api/contracts/{cid}/restore")).status_code == 404
    assert (await b.patch(f"/api/contracts/{cid}", json={"title": "hijacked"})).status_code == 404
    assert (await b.delete(f"/api/contracts/{cid}?permanent=true")).status_code == 404
    assert (await a.get(f"/api/contracts/{cid}")).json()["title"] != "hijacked"
    assert len((await a.get(f"/api/contracts/{cid}/versions")).json()) == 1


async def test_rename_and_counterparty_update(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    r = await client.patch(f"/api/contracts/{d['id']}", json={"title": "Northwind MSA (signed)", "counterparty": "Northwind Analytics"})
    assert r.status_code == 200 and r.json()["title"] == "Northwind MSA (signed)"
    assert r.json()["counterparty"] == "Northwind Analytics"
    assert (await client.patch(f"/api/contracts/{d['id']}", json={"title": ""})).status_code == 422
