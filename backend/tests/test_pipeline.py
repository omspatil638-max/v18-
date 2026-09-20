import json

from app.services import job_runner
from tests.helpers import upload, upload_and_process


def _fields(detail):
    return {f["field_key"]: f for f in detail["fields"]}


async def test_upload_returns_202_before_processing_then_job_completes(client, llm, long_pdf):
    r = await upload(client, long_pdf, "MSA.pdf")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] in ("PENDING", "PROCESSING")      # not blocked on the LLM
    assert body["progress"] < 100
    assert "raw_text" not in body and "storage_path" not in body   # no server paths / bulk text leaked

    await job_runner.wait_idle()
    st = (await client.get(f"/api/contracts/{body['id']}/status")).json()
    assert st["status"] == "READY" and st["progress"] == 100 and st["extraction_status"] == "complete"


async def test_full_document_is_processed_not_just_first_12000_chars(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    assert len(llm.excerpts) > 1, "a 14k-character contract must be split into several chunks"
    assert sum(len(e) for e in llm.excerpts) >= 14000
    assert d["extraction_meta"]["chunks_total"] == len(llm.excerpts)

    f = _fields(d)
    exp = f["expiration_date"]                               # stated ~char 13,500, beyond any 12k cutoff
    assert exp["status"] == "verified" and exp["display_value"] == "2028-12-31"
    assert exp["page"] >= 4 and "Term and Renewal" in exp["section"]
    assert 0.8 <= exp["confidence"] <= 1.0
    assert f["auto_renew"]["value"] == {"bool": True}
    assert f["renewal_notice_period"]["value"]["days"] == 60
    assert f["termination_notice_period"]["value"]["days"] == 90
    assert f["effective_date"]["page"] == 1 and f["effective_date"]["status"] == "verified"
    assert f["payment_terms"]["status"] == "verified"


async def test_extracted_items_are_source_linked_and_verified(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    assert {p["name"] for p in d["parties"]} == {"Northwind Analytics Inc.", "Contoso Retail LLC"}
    assert all(p["status"] == "verified" and p["source_page"] == 1 and p["source_quote"] for p in d["parties"])

    audit = next(o for o in d["obligations"] if "security audit" in o["action"])
    assert audit["responsible_party"] == "Customer" and audit["due_date"] == "2027-03-31"
    assert audit["verification_status"] == "verified" and audit["source_page"] >= 4 and audit["source_quote"]
    quarterly = next(o for o in d["obligations"] if "quarterly" in o["action"])
    assert quarterly["due_computed"] is True                 # computed in code from "15 days after each quarter", not the model's word
    assert "due_date" in quarterly and quarterly["due_date"][5:] in ("01-15", "04-15", "07-15", "10-15")

    assert {c["clause_type"] for c in d["clauses"]} == {"PAYMENT", "TERMINATION"}
    assert all(c["status"] == "verified" and c["source_page"] for c in d["clauses"])

    by_type = {}
    for x in d["deadlines"]:
        by_type.setdefault(x["deadline_type"], []).append(x)
    exp = by_type["EXPIRY"][0]
    assert exp["deadline_date"] == "2028-12-31" and exp["source_quote"] and exp["source_page"] >= 4
    assert any(o["deadline_date"] == "2027-03-31" for o in by_type["OBLIGATION"])          # the audit report date

    # The renewal deadline is COMPUTED in code from the stated notice period, never invented:
    # expiration 2028-12-31 minus the 60 days the contract states = 2028-11-01.
    renewal = by_type["RENEWAL_NOTICE"]
    assert len(renewal) == 1 and renewal[0]["deadline_date"] == "2028-11-01"
    assert "2028-12-31 minus 60 days" in renewal[0]["basis"] and "renews automatically" in renewal[0]["basis"]
    assert renewal[0]["source_page"] >= 4 and "sixty (60) days" in renewal[0]["source_quote"]


async def test_quote_that_is_not_in_the_document_is_needs_review_with_low_confidence(client, llm, long_pdf):
    llm.transform_quote = lambda q: q.replace("December 31, 2028", "December 31, 2031")
    d = await upload_and_process(client, long_pdf)
    exp = _fields(d)["expiration_date"]
    assert exp["status"] == "needs_review"
    assert exp["confidence"] <= 0.40
    assert exp["page"] is None and exp["section"] is None    # no location is claimed for an unverifiable quote


async def test_absent_information_is_not_found_not_invented(client, llm, tiny_pdf):
    d = await upload_and_process(client, tiny_pdf, "note.pdf")
    assert d["extraction_status"] == "complete"
    assert all(f["status"] == "not_found" and f["value"] is None and f["confidence"] is None for f in d["fields"])
    assert d["parties"] == [] and d["obligations"] == [] and d["clauses"] == [] and d["deadlines"] == []
    assert d["effective_date"] is None and d["expiry_date"] is None and d["renewal_terms"] is None


async def test_no_llm_configured_yields_extraction_unavailable_and_no_fabricated_values(client, long_pdf):
    d = await upload_and_process(client, long_pdf)            # LLM_PROVIDER=none
    assert d["status"] == "READY"                             # the document itself is still usable
    assert d["extraction_status"] == "unavailable" and "LLM" in d["extraction_error"]
    assert len(d["fields"]) == 8
    assert all(f["status"] == "extraction_unavailable" and f["value"] is None and f["confidence"] is None
               for f in d["fields"])
    assert d["parties"] == [] and d["obligations"] == [] and d["clauses"] == [] and d["deadlines"] == []
    blob = json.dumps(d)
    for fabricated in ("Party A", "Party B", "Auto-renews annually", "Licensor", "0.95", "specified schedule"):
        assert fabricated not in blob


async def test_llm_failure_on_every_chunk_is_unavailable_not_a_guess(client, llm, long_pdf):
    from app.services.llm_service import LLMError
    llm.raise_always = LLMError("bad_json", "simulated failure")
    d = await upload_and_process(client, long_pdf)
    assert d["extraction_status"] == "unavailable"
    assert all(f["status"] == "extraction_unavailable" for f in d["fields"])
    assert d["parties"] == [] and d["deadlines"] == []


async def test_daily_quota_exhaustion_is_reported_as_unavailable(client, llm, long_pdf):
    from app.services.llm_service import LLMUnavailable
    llm.raise_always = LLMUnavailable("rate_limited", "The LLM provider's rate/quota limit was reached.")
    d = await upload_and_process(client, long_pdf)
    assert d["extraction_status"] == "unavailable" and "quota" in d["extraction_error"]


async def test_partial_failure_is_flagged_and_missing_fields_are_not_called_not_found(client, llm, long_pdf):
    llm.fail_calls = {1}                                      # the first chunk (effective date, parties, payment) fails
    d = await upload_and_process(client, long_pdf)
    assert d["extraction_status"] == "partial" and d["extraction_error"]
    f = _fields(d)
    assert f["effective_date"]["status"] == "extraction_unavailable"   # cannot claim "not in the document"
    assert f["expiration_date"]["status"] == "verified"
    assert d["parties"] == []


async def test_scanned_pdf_without_text_layer_is_unsupported(client, llm, blank_pdf):
    d = await upload_and_process(client, blank_pdf, "scan.pdf")
    assert d["status"] == "UNSUPPORTED" and d["extraction_status"] == "unsupported"
    assert d["extraction_error"] and d["fields"] == []
    assert llm.calls == []                                     # no LLM call is wasted on it


async def test_reprocess_after_configuring_llm_replaces_data_without_duplicates(client, monkeypatch, long_pdf):
    d = await upload_and_process(client, long_pdf)
    assert d["extraction_status"] == "unavailable"

    from app.core.config import settings
    from app.services.llm_service import set_fake_handler
    from tests.fake_llm import ContractReadingLLM
    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr(settings, "LLM_TPM_LIMIT", 10 ** 9)
    monkeypatch.setattr(settings, "LLM_RPM_LIMIT", 10 ** 6)
    set_fake_handler(ContractReadingLLM())
    try:
        r = await client.post(f"/api/contracts/{d['id']}/reprocess")
        assert r.status_code == 202
        await job_runner.wait_idle()
        first = (await client.get(f"/api/contracts/{d['id']}")).json()
        for _ in range(2):                                     # a second reprocess must not duplicate rows either
            await client.post(f"/api/contracts/{d['id']}/reprocess")
            await job_runner.wait_idle()
    finally:
        set_fake_handler(None)

    d2 = (await client.get(f"/api/contracts/{d['id']}")).json()
    assert d2["extraction_status"] == "complete"
    assert len(d2["fields"]) == 8 and len(d2["parties"]) == 2
    assert len(d2["deadlines"]) == len(first["deadlines"]) > 2          # stable across re-runs, never accumulating
    assert len(d2["obligations"]) == len(first["obligations"])


async def test_recover_incomplete_requeues_interrupted_jobs(client, llm, long_pdf):
    r = await upload(client, long_pdf)
    await job_runner.wait_idle()
    cid = r.json()["id"]

    from app.db.database import AsyncSessionLocal
    from app.models.models import ContractVersion
    from app.models.models import ContractStatus
    vid = (await client.get(f"/api/contracts/{cid}/status")).json()["current_version_id"]
    async with AsyncSessionLocal() as db:                      # simulate a crash mid-job
        v = await db.get(ContractVersion, __import__("uuid").UUID(vid))
        v.status, v.progress, v.extraction_status = ContractStatus.PROCESSING, 40, "running"
        await db.commit()

    assert await job_runner.recover_incomplete() == 1
    await job_runner.wait_idle()
    st = (await client.get(f"/api/contracts/{cid}/status")).json()
    assert st["status"] == "READY" and st["extraction_status"] == "complete"


async def test_output_truncation_splits_the_chunk_and_still_completes(client, llm, long_pdf):
    llm.truncate_over = 4000                                   # any excerpt over 4k chars "overflows" the model's output
    d = await upload_and_process(client, long_pdf)
    assert d["extraction_status"] == "complete", d["extraction_error"]
    assert d["extraction_meta"]["chunks_failed"] == []
    assert len(llm.excerpts) > d["extraction_meta"]["chunks_total"]       # halves were sent
    assert max(len(e) for e in llm.excerpts) <= 4000
