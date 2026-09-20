"""A person reviews extracted fields: corrections are validated in code, kept reversible and survive re-processing."""

import pytest

from app.core.config import settings
from app.services import job_runner
from tests.helpers import upload, upload_and_process

PASSWORD = "correct horse battery"
EXPIRY_QUOTE = "expires on December 31, 2028"


def field(detail, key):
    return next(f for f in detail["fields"] if f["field_key"] == key)


async def review(client, cid, fid, **body):
    return await client.post(f"/api/contracts/{cid}/fields/{fid}/review", json=body)


async def detail_of(client, cid):
    return (await client.get(f"/api/contracts/{cid}")).json()


# ─── correcting a value ───────────────────────────────────────────────────────

async def test_a_corrected_date_updates_the_field_the_version_and_the_deadlines(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    assert exp["display_value"] == "2028-12-31"
    r = await review(client, d["id"], exp["id"], action="correct", value="2029-06-30", note="Amended by email")
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["value"] == {"date": "2029-06-30"} and f["display_value"] == "2029-06-30"
    assert f["status"] == "needs_review" and f["page"] is None and f["source_quote"] is None   # nothing shown to back it up
    ur = f["notes"]["user_review"]
    assert ur["action"] == "corrected" and ur["note"] == "Amended by email"
    assert ur["original"]["display_value"] == "2028-12-31" and ur["original"]["status"] == "verified"

    after = await detail_of(client, d["id"])
    assert after["expiry_date"] == "2029-06-30"
    sched = (await client.get("/api/deadlines?days_ahead=1825")).json()
    assert [x["deadline_date"] for x in sched if x["deadline_type"] == "EXPIRY"] == ["2029-06-30"]
    renewal = [x["deadline_date"] for x in sched if x["deadline_type"] == "RENEWAL_NOTICE"]
    assert renewal and all(x > "2029-01-01" for x in renewal)               # the notice deadline moved with the expiry


async def test_a_correction_elsewhere_does_not_resurrect_alerts_you_already_read(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    alerts = (await client.get("/api/alerts")).json()
    assert alerts
    victim = alerts[0]
    assert (await client.patch(f"/api/alerts/{victim['id']}/acknowledge")).status_code == 200
    pay = field(d, "payment_terms")
    assert (await review(client, d["id"], pay["id"], action="correct", value="Net 45")).status_code == 200      # unrelated to dates
    again = (await client.get("/api/alerts?unacknowledged_only=false")).json()
    same = [a for a in again if a["deadline_date"] == victim["deadline_date"] and a["lead_days"] == victim["lead_days"]
            and a["contract_id"] == victim["contract_id"]]
    assert same and all(a["acknowledged"] for a in same)
    assert len(again) == len(alerts)                                    # no duplicates either


async def test_a_quote_that_exists_and_contains_the_value_makes_it_verified_with_a_derived_source(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    r = await review(client, d["id"], exp["id"], action="correct", value="December 31, 2028", quote=EXPIRY_QUOTE)
    assert r.status_code == 200
    f = r.json()
    assert f["status"] == "verified" and f["page"] and f["section"] and "December 31, 2028" in f["source_quote"]


async def test_a_quote_not_in_the_document_or_not_containing_the_value_is_refused(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    bad = await review(client, d["id"], exp["id"], action="correct", value="2029-06-30", quote="The agreement ends in June 2029, we promise")
    assert bad.status_code == 422 and "not found in the document" in bad.json()["detail"]
    wrong = await review(client, d["id"], exp["id"], action="correct", value="2029-06-30", quote=EXPIRY_QUOTE)
    assert wrong.status_code == 422 and "does not contain that date" in wrong.json()["detail"]
    assert field(await detail_of(client, d["id"]), "expiration_date")["display_value"] == "2028-12-31"     # untouched


@pytest.mark.parametrize("value,why", [
    ("03/04/2029", "ambiguous"),
    ("not a date", "clear date"),
    ("1850-01-01", "plausible"),
    ("2025-01-01", "before"),           # ends before the effective date (2026-01-15)
])
async def test_bad_dates_are_rejected_and_change_nothing(client, llm, long_pdf, value, why):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    r = await review(client, d["id"], exp["id"], action="correct", value=value)
    assert r.status_code == 422
    assert field(await detail_of(client, d["id"]), "expiration_date")["display_value"] == "2028-12-31"


@pytest.mark.parametrize("raw,days,display", [("45 days", 45, "45 days"), ("2 weeks", 14, "2 weeks"), ("3 months", 90, "3 months"), ("1 year", 365, "1 year")])
async def test_notice_periods_are_parsed_into_days(client, llm, long_pdf, raw, days, display):
    d = await upload_and_process(client, long_pdf)
    f = field(d, "termination_notice_period")
    r = await review(client, d["id"], f["id"], action="correct", value=raw)
    assert r.status_code == 200 and r.json()["value"]["days"] == days and r.json()["display_value"] == display


@pytest.mark.parametrize("raw", ["soon", "-5 days", "0 days", "9999 days", ""])
async def test_bad_notice_periods_are_rejected(client, llm, long_pdf, raw):
    d = await upload_and_process(client, long_pdf)
    f = field(d, "termination_notice_period")
    assert (await review(client, d["id"], f["id"], action="correct", value=raw)).status_code == 422


async def test_yes_no_and_text_fields(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    ar = field(d, "auto_renew")
    assert (await review(client, d["id"], ar["id"], action="correct", value="no")).json()["display_value"] == "No"
    assert (await review(client, d["id"], ar["id"], action="correct", value="maybe")).status_code == 422
    pay = field(d, "payment_terms")
    r = await review(client, d["id"], pay["id"], action="correct", value="  Net 45,   invoiced monthly ")
    assert r.json()["value"] == {"text": "Net 45, invoiced monthly"}


# ─── confirm, not in contract, revert ─────────────────────────────────────────

async def test_confirming_a_verified_value_keeps_it_verified_and_marks_it_reviewed(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    r = await review(client, d["id"], exp["id"], action="confirm")
    assert r.status_code == 200 and r.json()["status"] == "verified"
    assert r.json()["notes"]["user_review"]["action"] == "confirmed"


async def test_confirm_needs_a_value(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    absent = next((f for f in d["fields"] if f["status"] == "not_found"), None)
    if absent is None:
        pytest.skip("this document has every field")
    assert (await review(client, d["id"], absent["id"], action="confirm")).status_code == 422


async def test_saying_a_value_is_not_in_the_contract_records_not_found_and_quiets_its_flag(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    nt = field(d, "termination_notice_period")
    r = await review(client, d["id"], nt["id"], action="not_in_contract")
    assert r.status_code == 200 and r.json()["status"] == "not_found" and r.json()["value"] is None
    flags = (await client.get(f"/api/contracts/{d['id']}/flags")).json()
    assert not [x for x in flags if x["code"] == "MISSING_FIELD" and "termination notice" in (x.get("label") or "").lower()]


async def test_revert_restores_what_the_ai_found(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    assert (await review(client, d["id"], exp["id"], action="revert")).status_code == 422       # nothing to revert yet
    await review(client, d["id"], exp["id"], action="correct", value="2029-06-30")
    r = await review(client, d["id"], exp["id"], action="revert")
    assert r.status_code == 200
    f = r.json()
    assert f["display_value"] == "2028-12-31" and f["status"] == "verified" and "user_review" not in (f["notes"] or {})
    assert (await detail_of(client, d["id"]))["expiry_date"] == "2028-12-31"


async def test_a_reviewed_field_no_longer_raises_a_low_confidence_flag(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    await review(client, d["id"], exp["id"], action="correct", value="2029-06-30")          # needs_review, but a person set it
    flags = (await client.get(f"/api/contracts/{d['id']}/flags")).json()
    assert not [x for x in flags if x["code"] in ("LOW_CONFIDENCE", "QUOTE_NOT_FOUND") and "expiration" in (x.get("label") or "").lower()]


# ─── survives re-processing ───────────────────────────────────────────────────

async def test_a_correction_survives_reprocessing_and_can_still_be_reverted(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    await review(client, d["id"], exp["id"], action="correct", value="2029-06-30")
    assert (await client.post(f"/api/contracts/{d['id']}/reprocess")).status_code == 202
    await job_runner.wait_idle()
    d2 = await detail_of(client, d["id"])
    f = field(d2, "expiration_date")
    assert f["display_value"] == "2029-06-30" and f["notes"]["user_review"]["action"] == "corrected"
    assert d2["expiry_date"] == "2029-06-30"
    assert f["notes"]["user_review"]["original"]["display_value"] == "2028-12-31"            # this run's AI answer
    back = await review(client, d["id"], f["id"], action="revert")
    assert back.json()["display_value"] == "2028-12-31"


# ─── access ───────────────────────────────────────────────────────────────────

async def test_review_is_owner_only(client, new_client, monkeypatch, llm, long_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    cid = (await upload(a, long_pdf)).json()["id"]
    await job_runner.wait_idle()
    exp = field(await detail_of(a, cid), "expiration_date")
    body = {"action": "correct", "value": "2029-06-30"}
    assert (await b.post(f"/api/contracts/{cid}/fields/{exp['id']}/review", json=body)).status_code == 404
    assert (await anon.post(f"/api/contracts/{cid}/fields/{exp['id']}/review", json=body)).status_code == 401
    assert field(await detail_of(a, cid), "expiration_date")["display_value"] == "2028-12-31"


async def test_unknown_actions_and_fields_are_rejected(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = field(d, "expiration_date")
    assert (await review(client, d["id"], exp["id"], action="delete")).status_code == 422
    assert (await review(client, d["id"], "00000000-0000-0000-0000-000000000000", action="confirm")).status_code == 404
