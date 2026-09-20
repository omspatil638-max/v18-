"""Phase 7: the one-page stakeholder summary — sourced line by line, and never invented."""

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.models import ExtractedField
from app.services import job_runner
from app.services.summary_service import overview_is_grounded
from app.services.text_index import DocIndex
from tests.helpers import upload, upload_and_process, upload_version

PASSWORD = "correct horse battery"
VALID_STATUS = {"verified", "needs_review", "not_found", "extraction_unavailable"}


async def summary(client, cid, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = await client.get(f"/api/contracts/{cid}/summary" + (f"?{q}" if q else ""))
    assert r.status_code == 200, r.text
    return r.json()


def sec(s, key):
    return next(x for x in s["sections"] if x["key"] == key)["lines"]


def texts(lines):
    return " || ".join(l["text"] for l in lines)


# ─── content ──────────────────────────────────────────────────────────────────

async def test_summary_covers_parties_term_money_dates_obligations_and_review(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    s = await summary(client, d["id"])
    assert s["status"] == "ready" and s["title"] == d["title"] and s["version_label"] == "v1"
    assert [x["key"] for x in s["sections"]] == ["parties", "term", "money", "dates", "obligations", "review"]

    parties = texts(sec(s, "parties"))
    assert "Northwind Analytics Inc. (Provider)" in parties and "Contoso Retail LLC (Customer)" in parties
    term = texts(sec(s, "term"))
    assert "Effective 15 Jan 2026" in term and "Expires 31 Dec 2028" in term
    assert "Renews automatically; notice of 60 days is needed to stop it" in term
    assert "Termination notice: 90 days" in term
    assert "$12,500" in texts(sec(s, "money"))
    dates = texts(sec(s, "dates"))
    assert "Renewal notice deadline" in dates and "1 Nov 2028" in dates

    obs = sec(s, "obligations")
    assert 1 <= len(obs) <= 6 and all(o["kind"] == "obligation" for o in obs)
    assert any("security audit" in o["text"] and "31 Mar 2027" in o["text"] for o in obs)
    review = sec(s, "review")
    assert review and all(l["kind"] == "flag" and l["severity"] in ("HIGH", "MEDIUM") for l in review)


async def test_every_line_with_a_page_has_a_quote_that_really_is_in_the_document(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    s = await summary(client, d["id"])
    text = (await client.get(f"/api/contracts/{d['id']}/text")).json()
    doc = DocIndex([(p["page"], p["text"]) for p in text["pages"]])
    checked = 0
    for section in s["sections"]:
        for l in section["lines"]:
            assert l["status"] in VALID_STATUS and l["text"].strip()
            if l["page"] is not None:
                assert l["quote"] and doc.find(l["quote"]) is not None, (l["text"], l["quote"])
                checked += 1
    assert checked >= 10


async def test_summary_fits_one_page(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    s = await summary(client, d["id"])
    assert s["word_count"] <= 450
    assert all(len(l["text"]) <= 220 for x in s["sections"] for l in x["lines"])
    assert s["disclaimer"].startswith("AI-assisted summary, not legal advice")
    assert len(sec(s, "parties")) <= 4 and len(sec(s, "dates")) <= 5 and len(sec(s, "review")) <= 5


async def test_unverified_facts_are_shown_as_such_not_dropped_or_upgraded(client, llm, long_pdf):
    llm.transform_quote = lambda q: q.replace("December 31, 2028", "December 31, 2031")
    d = await upload_and_process(client, long_pdf)
    line = next(l for l in sec(await summary(client, d["id"]), "term") if l["text"].startswith("Expires"))
    assert line["status"] == "needs_review" and line["page"] is None


async def test_missing_information_is_stated_not_invented(client, llm, tmp_path):
    from sample_data.generate import write_pdf
    write_pdf(tmp_path / "n.pdf", "NOTE\n\n1. Scope\nThe provider will deliver a monthly progress report to the client office.\n")
    d = await upload_and_process(client, (tmp_path / "n.pdf").read_bytes())
    s = await summary(client, d["id"])
    term = sec(s, "term")
    assert any(l["text"] == "Expiration date: not found in the document" and l["status"] == "not_found" for l in term)
    assert sec(s, "money")[0]["text"] == "Payment terms: not found in the document"
    assert sec(s, "parties")[0]["text"] == "Parties: not found in the document"


# ─── honesty when extraction did not run ──────────────────────────────────────

async def test_without_an_llm_the_summary_says_so_and_still_lists_automatic_checks(client, long_pdf):
    d = await upload_and_process(client, long_pdf)
    s = await summary(client, d["id"])
    assert s["status"] == "unavailable" and s["overview"] is None and s["overview_status"] == "unavailable"
    assert any("did not run" in n for n in s["notes"])
    assert all(l["status"] in ("extraction_unavailable", "needs_review") for x in s["sections"] for l in x["lines"])
    assert sec(s, "review")                                      # text-based flags exist without any AI


async def test_partial_analysis_is_called_out(client, llm, long_pdf):
    llm.fail_calls = {1}
    d = await upload_and_process(client, long_pdf)
    s = await summary(client, d["id"])
    assert s["status"] == "partial" and any("part of this document" in n for n in s["notes"])


# ─── the optional AI overview ─────────────────────────────────────────────────

async def test_overview_is_written_from_the_facts_and_is_grounded(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    s = await summary(client, d["id"])
    assert s["overview_status"] == "ai"
    assert "Northwind Analytics Inc." in s["overview"] and "31 Dec 2028" in s["overview"]
    assert len(llm.overview_prompts) == 1 and "<facts>" in llm.overview_prompts[0]


@pytest.mark.parametrize("mode", ["ungrounded", "invented_name"])
async def test_an_overview_containing_an_invented_number_or_name_is_discarded(client, llm, long_pdf, mode):
    llm.overview_mode = mode
    d = await upload_and_process(client, long_pdf)
    s = await summary(client, d["id"])
    assert s["overview"] is None and s["overview_status"] == "unavailable"
    assert s["sections"]                                         # the sourced body is unaffected


async def test_a_stale_overview_is_not_shown_after_the_facts_change(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    assert (await summary(client, d["id"]))["overview"]
    async with AsyncSessionLocal() as db:
        f = (await db.execute(select(ExtractedField).where(ExtractedField.field_key == "payment_terms"))).scalars().first()
        f.display_value = "Monthly fee of $99,999."
        await db.commit()
    s = await summary(client, d["id"])
    assert s["overview"] is None                                 # written from different facts: dropped, not shown
    assert "$99,999" in texts(sec(s, "money"))                   # while the live body reflects the change


async def test_overview_can_be_regenerated_after_an_llm_is_configured(client, long_pdf, monkeypatch):
    d = await upload_and_process(client, long_pdf)               # no LLM: no overview
    assert (await summary(client, d["id"]))["overview"] is None
    from app.services.llm_service import set_fake_handler
    from tests.fake_llm import ContractReadingLLM
    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr(settings, "LLM_TPM_LIMIT", 10 ** 9)
    monkeypatch.setattr(settings, "LLM_RPM_LIMIT", 10 ** 6)
    set_fake_handler(ContractReadingLLM())
    try:
        # facts must exist for an overview: re-run extraction first, then ask for the overview
        await client.post(f"/api/contracts/{d['id']}/reprocess")
        await job_runner.wait_idle()
        assert (await client.post(f"/api/contracts/{d['id']}/summary")).status_code == 202
        await job_runner.wait_idle()
    finally:
        set_fake_handler(None)
    assert (await summary(client, d["id"]))["overview"]


@pytest.mark.parametrize("text,ok", [
    ("An agreement between Northwind Analytics Inc. and Contoso Retail LLC, effective 15 Jan 2026.", True),
    ("A contract expiring 31 Dec 2028.", True),
    ("It carries a 45% discount.", False),                                     # number not in the facts
    ("Negotiated by Zephyr Holdings.", False),                                 # name not in the facts
    ("", False),
    ("word " * 80, False),                                                     # too long
])
def test_overview_grounding_rule(text, ok):
    facts = "Parties: Northwind Analytics Inc. (Provider) | Contoso Retail LLC (Customer)\nTerm: Effective 15 Jan 2026 | Expires 31 Dec 2028"
    assert overview_is_grounded(text, facts) is ok


# ─── always live ──────────────────────────────────────────────────────────────

async def test_resolving_a_flag_updates_the_summary_immediately(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    before = sec(await summary(client, d["id"]), "review")
    flag_id = before[0]["ref_id"]
    await client.patch(f"/api/flags/{flag_id}", json={"status": "RESOLVED"})
    after = sec(await summary(client, d["id"]), "review")
    assert flag_id not in {l["ref_id"] for l in after}


async def test_summary_follows_the_requested_version(client, llm, long_pdf, tmp_path):
    from sample_data.generate import long_contract_text, write_pdf
    write_pdf(tmp_path / "v2.pdf", long_contract_text().replace("$12,500", "$14,000"))
    d = await upload_and_process(client, long_pdf)
    v2 = await upload_version(client, d["id"], (tmp_path / "v2.pdf").read_bytes(), label="v2")
    cur = await summary(client, d["id"])
    old = await summary(client, d["id"], version_id=d["current_version_id"])
    assert cur["version_label"] == "v2" and "$14,000" in texts(sec(cur, "money"))
    assert old["version_label"] == "v1" and "$12,500" in texts(sec(old, "money"))
    assert cur["contract_version_id"] == v2["id"]


# ─── export ───────────────────────────────────────────────────────────────────

async def test_markdown_export_carries_sources_flags_and_the_disclaimer(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf, "MSA.pdf")
    r = await client.get(f"/api/contracts/{d['id']}/summary.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"].endswith('-summary.md"')
    md = r.text
    assert md.startswith(f"# {d['title']}") and "## Parties" in md and "## Needs human review" in md
    assert "(source: page 1" in md and "not legal advice" in md.lower()
    assert md.rstrip().endswith("_AI-assisted summary, not legal advice. Every line links to its source in the contract; check the source before relying on it._")


async def test_markdown_marks_unverified_lines(client, llm, long_pdf):
    llm.transform_quote = lambda q: q.replace("December 31, 2028", "December 31, 2031")
    d = await upload_and_process(client, long_pdf)
    md = (await client.get(f"/api/contracts/{d['id']}/summary.md")).text
    assert "Expires 31 Dec 2028 **[needs review]**" in md


# ─── ownership ────────────────────────────────────────────────────────────────

async def test_summary_is_owner_only(client, new_client, monkeypatch, llm, long_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    cid = (await upload(a, long_pdf)).json()["id"]
    await job_runner.wait_idle()
    for path in (f"/api/contracts/{cid}/summary", f"/api/contracts/{cid}/summary.md"):
        assert (await a.get(path)).status_code == 200
        assert (await b.get(path)).status_code == 404 and (await anon.get(path)).status_code == 401
    assert (await b.post(f"/api/contracts/{cid}/summary")).status_code == 404
