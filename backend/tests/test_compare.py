"""Phase 6: version comparison. The demo pair (v1 -> v2) differs in exactly three key terms."""

import pytest

from app.core.config import settings
from app.services import job_runner
from app.services.compare_service import (
    changed_sentences, impact_is_grounded, match_sections, word_diff,
)
from app.services.text_index import DocIndex
from tests.helpers import upload, upload_and_process, upload_version

PASSWORD = "correct horse battery"


def demo_pdfs(tmp_path):
    from sample_data.generate import demo_v1_text, demo_v2_text, write_pdf
    write_pdf(tmp_path / "v1.pdf", demo_v1_text())
    write_pdf(tmp_path / "v2.pdf", demo_v2_text())
    return (tmp_path / "v1.pdf").read_bytes(), (tmp_path / "v2.pdf").read_bytes()


async def two_versions(client, tmp_path, label="v2"):
    v1, v2 = demo_pdfs(tmp_path)
    d = await upload_and_process(client, v1, "northwind_v1.pdf")
    ver = await upload_version(client, d["id"], v2, "northwind_v2.pdf", label=label)
    return d["id"], d["current_version_id"], ver["id"]


async def compare(client, cid, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = await client.get(f"/api/contracts/{cid}/compare" + (f"?{q}" if q else ""))
    assert r.status_code == 200, r.text
    return r.json()


def key_changes(cmp):
    return {c["category"]: c for c in cmp["changes"] if c["category"] != "section"}


# ─── pure logic ───────────────────────────────────────────────────────────────

def secs(text):
    return DocIndex([(1, text)]).sections


A = "1. Fees\nCustomer pays 10,000 monthly.\n\n2. Term\nThe term is two years.\n\n3. Law\nDelaware law applies here.\n"
B = "1. Fees\nCustomer pays 12,000 monthly.\n\n2. Term\nThe term is two years.\n\n4. Support\nProvider gives email support.\n"


def test_sections_are_matched_by_heading_and_labelled():
    kinds = {(m.old.heading if m.old else None, m.new.heading if m.new else None): m.kind.value for m in match_sections(secs(A), secs(B))}
    assert kinds[("Fees", "Fees")] == "MODIFIED"
    assert kinds[("Term", "Term")] == "UNCHANGED"
    assert kinds[("Law", None)] == "REMOVED"
    assert kinds[(None, "Support")] == "ADDED"


def test_renumbered_sections_still_match_by_heading():
    old = secs("1. Termination\nEither party may terminate on thirty days notice.\n")
    new = secs("9. Termination\nEither party may terminate on thirty days notice.\n")
    m = match_sections(old, new)
    assert len(m) == 1 and m[0].kind.value == "UNCHANGED"


def test_a_renamed_heading_is_still_matched_by_similar_text():
    old = secs("1. Payment\nCustomer shall pay the monthly fee within thirty days of the invoice date each month.\n")
    new = secs("1. Fees and Charges\nCustomer shall pay the monthly fee within forty days of the invoice date each month.\n")
    m = match_sections(old, new)
    assert len(m) == 1 and m[0].old is not None and m[0].new is not None and m[0].kind.value == "MODIFIED"


def test_removed_sections_keep_their_place_in_document_order():
    order = [(m.old.heading if m.old else m.new.heading, m.kind.value) for m in match_sections(secs(A), secs(B))]
    assert [h for h, _ in order] == ["Fees", "Term", "Law", "Support"]


def test_changed_sentences_and_word_diff():
    removed, added = changed_sentences("Alpha stays. Fee is 10,000 monthly.", "Alpha stays. Fee is 12,000 monthly. New line here.")
    assert removed == ["Fee is 10,000 monthly."] and added == ["Fee is 12,000 monthly.", "New line here."]
    seg = word_diff("fee is 10,000 monthly", "fee is 12,000 monthly")
    assert [(s["op"], s["text"]) for s in seg] == [("equal", "fee is"), ("delete", "10,000"), ("insert", "12,000"), ("equal", "monthly")]


@pytest.mark.parametrize("impact,ok", [
    ("A longer notice period (60 days instead of 30 days) means more advance planning.", True),
    ("The fee rises from 10,000 to 12,000 per month.", True),
    ("This could cost an extra 45% each year.", False),                    # invented number
    ("Notice moves to 90 days.", False),
    ("It means ending the contract needs more advance planning.", True),   # no numbers at all is fine
    ("", False),
    ("x" * 400, False),
])
def test_impact_text_is_accepted_only_if_every_number_is_in_the_change(impact, ok):
    assert impact_is_grounded(impact, "30 days", "60 days", "fee 10,000", "fee 12,000") is ok


# ─── the demo: exactly three key changes ──────────────────────────────────────

async def test_v1_to_v2_reports_exactly_the_three_deliberate_key_changes(client, llm, tmp_path):
    cid, v1, v2 = await two_versions(client, tmp_path)
    cmp = await compare(client, cid)
    assert cmp["status"] == "ready" and cmp["from_version"]["id"] == v1 and cmp["to_version"]["id"] == v2

    material = {c["category"]: c for c in cmp["changes"] if c["category"] != "section" and c["is_material"]}
    assert set(material) == {"termination_notice_period", "auto_renew", "payment_terms"}, set(material)
    assert cmp["summary"]["key_changes"] == 3

    n = material["termination_notice_period"]
    assert (n["old_value"], n["new_value"]) == ("30 days", "60 days") and n["change_type"] == "MODIFIED"
    assert n["old_section"] and "Termination" in n["old_section"] and "Termination" in n["new_section"]
    assert n["old_page"] and n["new_page"] and "thirty (30) days" in n["old_quote"] and "sixty (60) days" in n["new_quote"]

    f = material["payment_terms"]
    assert (f["old_value"], f["new_value"]) == ("10,000", "12,000")
    assert "$10,000" in f["old_quote"] and "$12,000" in f["new_quote"]

    r = material["auto_renew"]
    assert r["change_type"] == "ADDED" and r["old_value"] is None and r["new_value"] == "Yes"
    assert "renews automatically" in r["new_quote"]


async def test_unchanged_terms_are_not_reported_as_changes(client, llm, tmp_path):
    cid, _, _ = await two_versions(client, tmp_path)
    cmp = await compare(client, cid)
    cats = {c["category"] for c in cmp["changes"]}
    assert not {"effective_date", "expiration_date", "party"} & cats
    assert all(c["change_type"] != "UNCHANGED" for c in cmp["changes"])
    assert cmp["summary"]["unchanged"] >= 4                                # the identical clauses are counted


async def test_clause_by_clause_labels_and_document_order(client, llm, tmp_path):
    cid, _, _ = await two_versions(client, tmp_path)
    cmp = await compare(client, cid, include_unchanged=True)
    sections = [c for c in cmp["changes"] if c["category"] == "section"]
    by_label = {c["label"].split("—")[-1].strip(): c["change_type"] for c in sections}
    assert by_label["Fees and Payment"] == by_label["Term"] == by_label["Termination"] == "MODIFIED"
    assert by_label["Confidentiality"] == by_label["Data Protection"] == by_label["Governing Law"] == "UNCHANGED"
    fees = next(c for c in sections if "Fees" in c["label"])
    assert "$10,000" in fees["old_value"] and "$12,000" in fees["new_value"]
    assert fees["is_material"] is True                                     # a fee clause is a key clause
    assert cmp["summary"]["modified"] >= 3


async def test_changes_are_ordered_key_terms_first_then_clauses(client, llm, tmp_path):
    cid, _, _ = await two_versions(client, tmp_path)
    cats = [c["category"] == "section" for c in (await compare(client, cid))["changes"]]
    assert cats == sorted(cats)                                            # all False (key terms) before True (clauses)


# ─── impact explanations ──────────────────────────────────────────────────────

async def test_material_changes_get_a_grounded_impact_sentence(client, llm, tmp_path):
    cid, _, _ = await two_versions(client, tmp_path)
    cmp = await compare(client, cid)
    assert cmp["impact_status"] == "complete"
    material = [c for c in cmp["changes"] if c["is_material"]]
    assert material and all(c["impact_text"] for c in material)
    n = key_changes(cmp)["termination_notice_period"]
    assert "30 days" in n["impact_text"] and "60 days" in n["impact_text"]
    assert all(c["impact_text"] is None for c in cmp["changes"] if not c["is_material"])
    assert len(llm.impact_prompts) == 1                                    # one call for the whole comparison


async def test_an_impact_that_invents_a_number_is_discarded(client, llm, tmp_path):
    llm.impact_mode = "ungrounded"
    cid, _, _ = await two_versions(client, tmp_path)
    cmp = await compare(client, cid)
    assert all(c["impact_text"] is None for c in cmp["changes"])           # "45%" is in neither version
    assert cmp["impact_status"] == "unavailable"


async def test_partial_impacts_are_reported_as_partial(client, llm, tmp_path):
    llm.impact_mode = "partial"
    cid, _, _ = await two_versions(client, tmp_path)
    cmp = await compare(client, cid)
    assert cmp["impact_status"] == "partial" and sum(1 for c in cmp["changes"] if c["impact_text"]) == 1


async def test_without_an_llm_changes_are_still_found_and_impact_is_honestly_unavailable(client, tmp_path):
    v1, v2 = demo_pdfs(tmp_path)                                           # LLM_PROVIDER=none: no fields, only text
    d = await upload_and_process(client, v1)
    await upload_version(client, d["id"], v2)
    cmp = await compare(client, d["id"])
    assert cmp["status"] == "ready" and cmp["impact_status"] in ("none", "unavailable")
    assert any(c["category"] == "section" and c["change_type"] == "MODIFIED" for c in cmp["changes"])   # text diff needs no LLM
    assert all(c["impact_text"] is None for c in cmp["changes"])
    assert any("could not be compared" in n for n in cmp["notes"])          # says why key terms were skipped, not silence


# ─── side by side ─────────────────────────────────────────────────────────────

async def test_aligned_sections_carry_word_level_diffs(client, llm, tmp_path):
    cid, _, _ = await two_versions(client, tmp_path)
    r = (await client.get(f"/api/contracts/{cid}/compare/sections")).json()
    fees = next(s for s in r["sections"] if s["old"] and "Fees" in s["old"]["label"])
    assert fees["change_type"] == "MODIFIED" and fees["is_material"] is True
    ops = {(seg["op"], seg["text"]) for seg in fees["diff"]}
    assert ("delete", "$10,000,") in ops and ("insert", "$12,000,") in ops
    same = next(s for s in r["sections"] if s["old"] and "Governing Law" in s["old"]["label"])
    assert same["change_type"] == "UNCHANGED" and same["diff"] == [] and same["similarity"] == 1.0
    assert all(s["old"] or s["new"] for s in r["sections"])


# ─── choosing versions, re-running, states ────────────────────────────────────

async def test_comparison_defaults_to_current_versus_previous_and_can_be_pinned(client, llm, tmp_path):
    cid, v1, v2 = await two_versions(client, tmp_path)
    v1_pdf, v2_pdf = demo_pdfs(tmp_path)
    v3 = (await upload_version(client, cid, v2_pdf, label="v3"))["id"]
    d = await compare(client, cid)
    assert d["from_version"]["id"] == v2 and d["to_version"]["id"] == v3                     # latest vs previous
    assert d["summary"]["key_changes"] == 0                                # v2 and v3 are the same document

    # A pair nobody has compared yet is computed on first look: the response says "running" and the
    # client polls until it is ready (no manual button needed).
    first = await compare(client, cid, from_version_id=v1, to_version_id=v3)
    assert first["status"] == "running" and first["changes"] == []
    await job_runner.wait_idle()
    pinned = await compare(client, cid, from_version_id=v1, to_version_id=v3)
    assert pinned["status"] == "ready" and pinned["summary"]["key_changes"] == 3


async def test_single_version_has_nothing_to_compare(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    r = await client.get(f"/api/contracts/{d['id']}/compare")
    assert r.status_code == 422 and "no earlier version" in r.json()["detail"]


async def test_same_version_and_foreign_version_are_rejected(client, llm, tmp_path, long_pdf):
    cid, v1, v2 = await two_versions(client, tmp_path)
    assert (await client.get(f"/api/contracts/{cid}/compare?from_version_id={v2}&to_version_id={v2}")).status_code == 422
    other = await upload_and_process(client, long_pdf)
    assert (await client.get(f"/api/contracts/{cid}/compare?from_version_id={other['current_version_id']}")).status_code == 404


async def test_rerun_recomputes_without_duplicating_changes(client, llm, tmp_path):
    cid, _, _ = await two_versions(client, tmp_path)
    first = await compare(client, cid)
    for _ in range(2):
        r = await client.post(f"/api/contracts/{cid}/compare")
        assert r.status_code == 202 and r.json()["status"] == "running"
        await job_runner.wait_idle()
    again = await compare(client, cid)
    assert again["status"] == "ready" and len(again["changes"]) == len(first["changes"])
    assert again["summary"] == first["summary"]


async def test_rerun_after_configuring_an_llm_adds_the_impact_text(client, tmp_path, monkeypatch):
    v1, v2 = demo_pdfs(tmp_path)
    d = await upload_and_process(client, v1)
    await upload_version(client, d["id"], v2)
    assert all(c["impact_text"] is None for c in (await compare(client, d["id"]))["changes"])
    from app.services.llm_service import set_fake_handler
    from tests.fake_llm import ContractReadingLLM
    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr(settings, "LLM_TPM_LIMIT", 10 ** 9)
    monkeypatch.setattr(settings, "LLM_RPM_LIMIT", 10 ** 6)
    set_fake_handler(ContractReadingLLM())
    try:
        for vid in [x["id"] for x in (await client.get(f"/api/contracts/{d['id']}/versions")).json()]:
            await client.post(f"/api/contracts/{d['id']}/reprocess?version_id={vid}")
            await job_runner.wait_idle()
        await client.post(f"/api/contracts/{d['id']}/compare")
        await job_runner.wait_idle()
    finally:
        set_fake_handler(None)
    cmp = await compare(client, d["id"])
    assert cmp["impact_status"] == "complete" and cmp["summary"]["key_changes"] == 3


async def test_a_new_version_is_compared_automatically_when_it_finishes(client, llm, tmp_path):
    cid, v1, v2 = await two_versions(client, tmp_path)                     # no explicit compare call anywhere
    cmp = await compare(client, cid)
    assert cmp["status"] == "ready" and cmp["summary"]["key_changes"] == 3


async def test_comparison_states_before_processing_finishes(client, llm, tmp_path, monkeypatch):
    v1, v2 = demo_pdfs(tmp_path)
    d = await upload_and_process(client, v1)
    monkeypatch.setattr(job_runner, "enqueue", lambda *_a, **_k: None)      # keep v2 PENDING
    r = await client.post(f"/api/contracts/{d['id']}/versions", files={"file": ("v2.pdf", v2, "application/pdf")})
    assert r.status_code == 202
    cmp = await compare(client, d["id"])
    assert cmp["status"] == "versions_not_ready" and cmp["changes"] == []
    assert (await client.post(f"/api/contracts/{d['id']}/compare")).status_code == 409


# ─── ownership ────────────────────────────────────────────────────────────────

async def test_comparison_is_owner_only(client, new_client, monkeypatch, llm, tmp_path):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    v1, v2 = demo_pdfs(tmp_path)
    cid = (await upload(a, v1)).json()["id"]
    await job_runner.wait_idle()
    await upload_version(a, cid, v2)
    assert (await a.get(f"/api/contracts/{cid}/compare")).status_code == 200
    for path in (f"/api/contracts/{cid}/compare", f"/api/contracts/{cid}/compare/sections"):
        assert (await b.get(path)).status_code == 404 and (await anon.get(path)).status_code == 401
    assert (await b.post(f"/api/contracts/{cid}/compare")).status_code == 404
    assert (await anon.post(f"/api/contracts/{cid}/compare")).status_code == 401
