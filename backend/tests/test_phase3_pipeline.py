"""Phase 3: extraction hardening, exercised through the whole pipeline."""

from tests.helpers import upload_and_process


async def test_role_labels_returned_as_parties_are_dropped(client, llm, long_pdf):
    """The live model returned 'Provider' / 'Customer' as parties from later sections."""
    llm.noisy_parties = True
    d = await upload_and_process(client, long_pdf)
    names = {p["name"] for p in d["parties"]}
    assert names == {"Northwind Analytics Inc.", "Contoso Retail LLC"}, names
    assert all(p["status"] == "verified" for p in d["parties"])


async def test_obligation_timing_is_classified_in_code(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    audit = next(o for o in d["obligations"] if "security audit" in o["action"])
    assert audit["due_rule_type"] == "fixed" and audit["due_date"] == "2027-03-31"

    quarterly = next(o for o in d["obligations"] if "quarterly" in o["action"])
    assert quarterly["due_rule_type"] == "recurring" and quarterly["recurrence"] == "QUARTERLY"
    # A recurring rule now yields its NEXT occurrence, computed in code: 15 days after a calendar quarter end.
    assert quarterly["due_computed"] is True and quarterly["due_date"]
    assert quarterly["due_date"][5:] in ("01-15", "04-15", "07-15", "10-15"), quarterly["due_date"]
    assert "occurrences" in quarterly["due_basis"]
    assert all(o["due_rule_type"] in ("fixed", "relative", "recurring", "none") for o in d["obligations"])


async def test_term_ending_before_it_starts_is_flagged_not_trusted(client, llm, tmp_path):
    """Both dates are really in the document (so each has a valid quote) but they contradict each other."""
    from sample_data.generate import long_contract_text, write_pdf
    text = long_contract_text().replace("January 15, 2026", "March 1, 2031")     # starts AFTER it expires (2028-12-31)
    write_pdf(tmp_path / "bad.pdf", text)
    d = await upload_and_process(client, (tmp_path / "bad.pdf").read_bytes())
    f = {x["field_key"]: x for x in d["fields"]}
    assert f["effective_date"]["display_value"] == "2031-03-01" and f["expiration_date"]["display_value"] == "2028-12-31"
    for key in ("effective_date", "expiration_date"):
        assert f[key]["status"] == "needs_review", key
        assert "before" in f[key]["notes"]["validation"]
        assert f[key]["confidence"] <= 0.5


async def test_a_date_that_is_not_in_its_quote_is_not_verified(client, llm, long_pdf):
    llm.effective_override = "2030-01-01"                     # the quoted passage says January 15, 2026
    d = await upload_and_process(client, long_pdf)
    eff = {x["field_key"]: x for x in d["fields"]}["effective_date"]
    assert eff["status"] == "needs_review" and "does not appear in the quoted passage" in eff["notes"]["validation"]


async def test_explicit_date_wins_over_an_incidental_recurrence_word(client, llm, long_pdf):
    """'an ANNUAL security audit report by March 31, 2027' has a fixed date; 'annual' is only a hint."""
    d = await upload_and_process(client, long_pdf)
    audit = next(o for o in d["obligations"] if "security audit" in o["action"])
    assert audit["due_rule_type"] == "fixed" and audit["due_date"] == "2027-03-31"


async def test_a_consistent_term_stays_verified(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    f = {x["field_key"]: x for x in d["fields"]}
    assert f["effective_date"]["status"] == "verified" and f["expiration_date"]["status"] == "verified"
    assert "validation" not in (f["effective_date"]["notes"] or {})


async def test_abbreviated_quotes_with_an_ellipsis_still_verify_and_store_the_real_text(client, llm, long_pdf):
    """The real model shortens quotes ("...") constantly; that must not make true facts look unverified."""
    llm.transform_quote = lambda q: q[: len(q) // 3] + " ... " + q[-(len(q) // 3):] if len(q) > 60 else q
    d = await upload_and_process(client, long_pdf)
    f = {x["field_key"]: x for x in d["fields"]}
    assert f["renewal_notice_period"]["status"] == "verified", f["renewal_notice_period"]
    assert f["termination_notice_period"]["status"] == "verified"
    stored = f["renewal_notice_period"]["source_quote"]
    assert "renews automatically for successive one (1) year terms" in stored      # the document's own words, not the stub
    assert "..." not in stored and "\u2026" not in stored
