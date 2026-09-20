"""Phase 4: 'needs human review' flags — rules, queue API, decisions, isolation."""

import pytest

from app.core.config import settings
from app.services import job_runner
from app.services.flag_service import _sentence_around, vague_wording_flags
from app.services.text_index import DocIndex
from tests.helpers import upload, upload_and_process, upload_version

PASSWORD = "correct horse battery"


def make_pdf(tmp_path, body: str, name="x.pdf") -> bytes:
    from sample_data.generate import write_pdf
    p = tmp_path / name
    write_pdf(p, body)
    return p.read_bytes()


async def flags_of(client, cid, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = await client.get(f"/api/contracts/{cid}/flags" + (f"?{q}" if q else ""))
    assert r.status_code == 200, r.text
    return r.json()


def by_code(flags):
    out = {}
    for f in flags:
        out.setdefault(f["code"], []).append(f)
    return out


RISKY = (
    "SERVICES AGREEMENT\n\nThis agreement is between Alpha Ltd and Beta Inc.\n\n"
    "1. Term\nThis agreement starts on January 1, 2026.\n\n"
    "2. Liability\nThe Provider's liability under this agreement shall be unlimited.\n\n"
    "3. Termination\nThe Provider may terminate this agreement at any time without cause.\n\n"
    "4. Fees\nCustomer shall pay an early termination fee equal to the remaining fees.\n\n"
    "5. Work\nProvider will use best efforts and may act in its sole discretion. Fees are as agreed.\n"
)


# ─── the sample contract ──────────────────────────────────────────────────────

async def test_sample_contract_flags_auto_renewal_with_its_notice_period(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    ar = by_code(await flags_of(client, d["id"]))["AUTO_RENEWAL"]
    assert len(ar) == 1 and ar[0]["severity"] == "MEDIUM"
    assert "60 days" in ar[0]["reason"] and "renews automatically" in ar[0]["reason"]
    assert ar[0]["source_page"] >= 4 and "renews automatically" in ar[0]["source_quote"]


async def test_sample_contract_flags_vague_wording_but_not_missing_fields_or_liability(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    codes = by_code(await flags_of(client, d["id"]))
    assert "VAGUE_WORDING" in codes
    labels = {f["target_label"] for f in codes["VAGUE_WORDING"]}
    assert "reasonable efforts" in labels and "no fixed deadline" in labels
    assert "MISSING_FIELD" not in codes                     # everything required was found
    assert "UNCAPPED_LIABILITY" not in codes                # the contract caps liability at 12 months of fees
    assert "UNUSUAL_TERMINATION" not in codes               # mutual, 90 days: ordinary


async def test_every_flag_has_a_plain_reason_and_a_quote_that_really_is_in_the_document(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    text = (await client.get(f"/api/contracts/{d['id']}/text")).json()
    doc = DocIndex([(p["page"], p["text"]) for p in text["pages"]])
    flags = await flags_of(client, d["id"])
    assert flags
    for f in flags:
        assert f["reason"].strip() and len(f["reason"]) > 20
        if f["source_quote"]:
            assert doc.find(f["source_quote"]) is not None, f["source_quote"]      # a real passage, never invented
            assert f["source_page"] is not None
    assert len(flags) < 25                                   # a queue, not a flood


# ─── text rules (work without an LLM) ─────────────────────────────────────────

async def test_risky_terms_are_flagged_from_the_text_alone(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, RISKY))
    codes = by_code(await flags_of(client, d["id"]))

    unc = codes["UNCAPPED_LIABILITY"][0]
    assert unc["severity"] == "HIGH" and "unlimited" in unc["source_quote"].lower()

    reasons = [f["reason"] for f in codes["UNUSUAL_TERMINATION"]]
    assert any("Only one party (the Provider)" in r for r in reasons)
    assert any("early termination" in r.lower() or "fee" in r.lower() for r in reasons)
    one_sided = next(f for f in codes["UNUSUAL_TERMINATION"] if "Only one party" in f["reason"])
    assert one_sided["severity"] == "HIGH" and "at any time" in one_sided["source_quote"]

    vague = {f["target_label"] for f in codes["VAGUE_WORDING"]}
    assert {"best efforts", "at its discretion", "as agreed / to be determined"} <= vague


async def test_missing_end_date_is_a_high_flag_when_extraction_completed(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, RISKY))
    missing = by_code(await flags_of(client, d["id"]))["MISSING_FIELD"]
    exp = next(f for f in missing if f["target_label"] == "Expiration date")
    assert exp["severity"] == "HIGH" and "No end date" in exp["reason"]


async def test_absent_liability_clause_is_flagged(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, "NOTE\n\n1. Scope\nThe provider will deliver a monthly progress report to the client office.\n"))
    unc = by_code(await flags_of(client, d["id"]))["UNCAPPED_LIABILITY"][0]
    assert unc["severity"] == "MEDIUM" and "No limitation-of-liability clause" in unc["reason"]
    assert unc["source_quote"] is None                       # there is nothing to quote: honest, not invented


async def test_a_mutual_termination_right_is_not_flagged(client, llm, tmp_path):
    body = ("AGREEMENT\n\n1. Liability\nTotal aggregate liability shall not exceed the fees paid.\n\n"
            "2. Termination\nEither party may terminate this agreement at any time on thirty (30) days notice.\n")
    d = await upload_and_process(client, make_pdf(tmp_path, body))
    assert "UNUSUAL_TERMINATION" not in by_code(await flags_of(client, d["id"]))


async def test_ordinary_words_do_not_trigger_vague_wording(client, llm, tmp_path):
    body = "AGREEMENT\n\n1. Fees\nThe fee is reasonable and material. Payment is due within thirty (30) days of invoice.\n"
    d = await upload_and_process(client, make_pdf(tmp_path, body))
    assert "VAGUE_WORDING" not in by_code(await flags_of(client, d["id"]))


def test_vague_detection_and_sentence_window_units():
    doc = DocIndex([(1, "1. Work\nThe supplier shall use reasonable endeavours to deliver. It must also report promptly.\n")])
    labels = {f.target_label for f in vague_wording_flags(doc)}
    assert labels == {"reasonable efforts", "no fixed deadline"}
    s = _sentence_around("First one. The supplier may act in its sole discretion here. Last one.", 30, 58)
    assert s == "The supplier may act in its sole discretion here."


# ─── extraction-driven rules ──────────────────────────────────────────────────

async def test_conflicting_dates_raise_high_date_conflict_flags(client, llm, tmp_path):
    from sample_data.generate import long_contract_text
    text = long_contract_text().replace("January 15, 2026", "March 1, 2031")
    d = await upload_and_process(client, make_pdf(tmp_path, text))
    conflicts = by_code(await flags_of(client, d["id"]))["DATE_CONFLICT"]
    assert len(conflicts) == 2 and all(f["severity"] == "HIGH" for f in conflicts)
    assert all("before" in f["reason"] for f in conflicts)


async def test_unverifiable_quote_raises_quote_not_found(client, llm, long_pdf):
    llm.transform_quote = lambda q: q.replace("December 31, 2028", "December 31, 2031")
    d = await upload_and_process(client, long_pdf)
    q = by_code(await flags_of(client, d["id"]))["QUOTE_NOT_FOUND"]
    exp = next(f for f in q if f["target_label"] == "Expiration date")
    assert exp["severity"] == "HIGH" and "could not be found in the document" in exp["reason"]
    assert exp["source_page"] is None                        # no location claimed for an unverified quote


async def test_without_an_llm_the_text_rules_still_run_and_the_gap_is_stated(client, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, RISKY))              # LLM_PROVIDER=none
    codes = by_code(await flags_of(client, d["id"]))
    inc = codes["EXTRACTION_INCOMPLETE"][0]
    assert inc["severity"] == "MEDIUM" and "did not run" in inc["reason"]
    assert "UNCAPPED_LIABILITY" in codes and "VAGUE_WORDING" in codes
    assert "MISSING_FIELD" not in codes                      # cannot claim absence when nothing was extracted


async def test_partial_extraction_is_a_high_flag(client, llm, long_pdf):
    llm.fail_calls = {1}
    d = await upload_and_process(client, long_pdf)
    inc = by_code(await flags_of(client, d["id"]))["EXTRACTION_INCOMPLETE"][0]
    assert inc["severity"] == "HIGH" and "Only part of this document" in inc["reason"]


# ─── amendments / new versions ────────────────────────────────────────────────

def amended_text() -> str:
    from sample_data.generate import long_contract_text
    return (long_contract_text().replace("ninety (90) days written notice", "sixty (60) days written notice")
            .replace("December 31, 2028", "June 30, 2028").replace("$12,500", "$14,000"))


async def test_a_new_version_flags_what_changed_from_the_previous_one(client, llm, long_pdf, tmp_path):
    d1 = await upload_and_process(client, long_pdf)
    v2 = await upload_version(client, d1["id"], make_pdf(tmp_path, amended_text()), label="v2")
    ch = by_code(await flags_of(client, d1["id"], version_id=v2["id"]))["AMENDMENT_CHANGES_TERM"]
    reasons = " | ".join(f["reason"] for f in ch)
    assert "changed from 90 days to 60 days" in reasons or ("90 days" in reasons and "60 days" in reasons)
    assert "2028-12-31" in reasons and "2028-06-30" in reasons
    assert "12,500" in reasons and "14,000" in reasons
    assert all(f["severity"] == "HIGH" for f in ch if f["target_label"] in
               {"Termination notice period", "Expiration date", "Payment terms"})
    assert "AMENDMENT_CHANGES_TERM" not in by_code(await flags_of(client, d1["id"], version_id=d1["current_version_id"]))


async def test_an_upload_marked_as_amendment_is_flagged_even_without_field_changes(client, llm, long_pdf):
    d1 = await upload_and_process(client, long_pdf)
    v2 = await upload_version(client, d1["id"], long_pdf, kind="AMENDMENT", label="Amendment 1")
    ch = by_code(await flags_of(client, d1["id"], version_id=v2["id"]))["AMENDMENT_CHANGES_TERM"]
    assert any(f["severity"] == "LOW" and "amendment" in f["reason"].lower() for f in ch)


# ─── the queue API ────────────────────────────────────────────────────────────

async def test_queue_lists_open_flags_most_severe_first_with_contract_context(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, RISKY), "risky.pdf")
    q = (await client.get("/api/flags")).json()
    assert q and all(f["status"] == "OPEN" for f in q)
    ranks = [{"HIGH": 0, "MEDIUM": 1, "LOW": 2}[f["severity"]] for f in q]
    assert ranks == sorted(ranks), "HIGH must come before MEDIUM before LOW"
    assert all(f["contract_title"] and f["version_label"] == "v1" and f["contract_id"] == d["id"] for f in q)

    high = (await client.get("/api/flags?severity=HIGH")).json()
    assert high and all(f["severity"] == "HIGH" for f in high) and len(high) < len(q)
    assert (await client.get("/api/flags?status=BOGUS")).status_code == 422


async def test_summary_counts_match_the_queue_and_contract_badges(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, RISKY))
    q = (await client.get("/api/flags")).json()
    s = (await client.get("/api/flags/summary")).json()
    assert s["open"] == len(q)
    assert s["high"] == sum(f["severity"] == "HIGH" for f in q) and s["medium"] == sum(f["severity"] == "MEDIUM" for f in q)
    assert s["open"] == s["high"] + s["medium"] + s["low"]

    detail = (await client.get(f"/api/contracts/{d['id']}")).json()
    listing = (await client.get("/api/contracts")).json()[0]
    assert detail["open_flag_count"] == listing["open_flag_count"] == s["open"]
    assert detail["high_flag_count"] == listing["high_flag_count"] == s["high"]
    assert len(detail["flags"]) == s["open"]


async def test_resolving_and_dismissing_moves_a_flag_out_of_the_open_queue_and_reopen_restores_it(client, llm, tmp_path):
    await upload_and_process(client, make_pdf(tmp_path, RISKY))
    q = (await client.get("/api/flags")).json()
    a, b = q[0], q[1]

    r = await client.patch(f"/api/flags/{a['id']}", json={"status": "RESOLVED"})
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED" and r.json()["resolved_at"]
    assert (await client.patch(f"/api/flags/{b['id']}", json={"status": "DISMISSED"})).status_code == 200

    open_ids = {f["id"] for f in (await client.get("/api/flags")).json()}
    assert a["id"] not in open_ids and b["id"] not in open_ids
    assert {f["id"] for f in (await client.get("/api/flags?status=RESOLVED")).json()} == {a["id"]}
    assert {f["id"] for f in (await client.get("/api/flags?status=DISMISSED")).json()} == {b["id"]}
    assert len((await client.get("/api/flags?status=ALL")).json()) == len(q)
    assert (await client.get("/api/flags/summary")).json()["open"] == len(q) - 2

    back = await client.patch(f"/api/flags/{a['id']}", json={"status": "OPEN"})
    assert back.json()["status"] == "OPEN" and back.json()["resolved_at"] is None
    assert (await client.get("/api/flags/summary")).json()["open"] == len(q) - 1


async def test_a_persons_decision_survives_reprocessing(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, RISKY))
    q = (await client.get("/api/flags")).json()
    target = next(f for f in q if f["code"] == "UNCAPPED_LIABILITY")
    await client.patch(f"/api/flags/{target['id']}", json={"status": "DISMISSED"})

    await client.post(f"/api/contracts/{d['id']}/reprocess")
    await job_runner.wait_idle()

    open_after = (await client.get("/api/flags")).json()
    assert not any(f["code"] == "UNCAPPED_LIABILITY" for f in open_after)      # still dismissed
    dismissed = (await client.get("/api/flags?status=DISMISSED")).json()
    assert [f["code"] for f in dismissed] == ["UNCAPPED_LIABILITY"]
    assert len(open_after) + 1 == len(q)                                         # nothing else changed or duplicated


async def test_reprocessing_does_not_duplicate_flags(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    before = len(await flags_of(client, d["id"]))
    for _ in range(2):
        await client.post(f"/api/contracts/{d['id']}/reprocess")
        await job_runner.wait_idle()
    assert len(await flags_of(client, d["id"])) == before > 0


async def test_queue_ignores_deleted_contracts_and_superseded_versions(client, llm, long_pdf, tmp_path):
    d1 = await upload_and_process(client, long_pdf)
    n_v1 = len((await client.get("/api/flags")).json())
    await upload_version(client, d1["id"], make_pdf(tmp_path, amended_text()))
    q = (await client.get("/api/flags")).json()
    assert {f["version_label"] for f in q} == {"v2"}                # v1's flags are history, not work to do
    assert len(q) != n_v1 or any(f["code"] == "AMENDMENT_CHANGES_TERM" for f in q)

    await client.delete(f"/api/contracts/{d1['id']}")
    assert (await client.get("/api/flags")).json() == []
    assert (await client.get("/api/flags/summary")).json() == {"open": 0, "high": 0, "medium": 0, "low": 0}
    await client.post(f"/api/contracts/{d1['id']}/restore")
    assert (await client.get("/api/flags")).json()


async def test_per_contract_status_filter(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, RISKY))
    first = (await flags_of(client, d["id"]))[0]
    await client.patch(f"/api/flags/{first['id']}", json={"status": "RESOLVED"})
    assert [f["id"] for f in await flags_of(client, d["id"], status="RESOLVED")] == [first["id"]]
    assert first["id"] not in {f["id"] for f in await flags_of(client, d["id"], status="OPEN")}
    assert first["id"] in {f["id"] for f in await flags_of(client, d["id"])}


# ─── ownership ────────────────────────────────────────────────────────────────

async def test_flags_are_private_to_their_owner(client, new_client, monkeypatch, llm, tmp_path):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, email in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": email, "name": "T", "password": PASSWORD})).status_code == 201

    cid = (await upload(a, make_pdf(tmp_path, RISKY), "a.pdf")).json()["id"]
    await job_runner.wait_idle()
    flag = (await a.get("/api/flags")).json()[0]

    assert (await b.get("/api/flags")).json() == []
    assert (await b.get("/api/flags/summary")).json()["open"] == 0
    assert (await b.get(f"/api/contracts/{cid}/flags")).status_code == 404
    assert (await b.patch(f"/api/flags/{flag['id']}", json={"status": "RESOLVED"})).status_code == 404
    assert (await anon.get("/api/flags")).status_code == 401
    assert (await anon.patch(f"/api/flags/{flag['id']}", json={"status": "RESOLVED"})).status_code == 401
    assert (await a.get("/api/flags")).json()[0]["status"] == "OPEN"          # B's attempt changed nothing
