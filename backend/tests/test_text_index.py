from app.services.text_index import (
    DocIndex, build_sections, chunk_sections, normalize, pages_from_raw_text,
)

PAGES = [
    (1, "CLOUD SERVICES AGREEMENT\n\nThis Agreement is entered into as of January 1, 2026 by and between\n"
        "Alpha Cloud Systems Inc. (\"Provider\") and Omega Retail LLC (\"Customer\").\n\n"
        "1. Payment Terms\nCustomer shall pay Provider $8,500 on the 1st of each\ncalendar month.\n"),
    (2, "9.2 Termination for Convenience\nEither party may terminate this Agreement for any reason by giving\n"
        "sixty (60) days’ written notice to the other party. The termi-\nnation takes effect at the\nend of the notice period.\n\n"
        "10. Confidentiality\nEach party shall keep the other’s information confidential.\n"),
]


def test_normalize_collapses_whitespace_case_quotes_and_dashes():
    assert normalize("  Hello\n\tWORLD ’s – x ") == "hello world 's - x"


def test_quote_found_across_line_breaks_and_wraps_reports_page_and_section():
    idx = DocIndex(PAGES)
    m = idx.find("Customer shall pay Provider $8,500 on the 1st of each calendar month.")
    assert m is not None and m.kind == "exact"
    assert m.page_start == 1
    assert m.section == "Section 1 — Payment Terms"


def test_quote_matches_despite_curly_apostrophe_and_hyphenated_line_wrap():
    idx = DocIndex(PAGES)
    m = idx.find("sixty (60) days' written notice to the other party. The termination takes effect")
    assert m is not None
    assert m.page_start == 2
    assert m.section == "Section 9.2 — Termination for Convenience"


def test_invented_quote_is_not_found():
    idx = DocIndex(PAGES)
    assert idx.find("Customer shall pay Provider $9,999 on the 1st of each calendar month.") is None


def test_too_short_quote_is_rejected_as_evidence():
    idx = DocIndex(PAGES)
    assert idx.find("Provider") is None
    assert idx.find("") is None
    assert idx.find(None) is None


def test_loose_match_tolerates_punctuation_differences_only():
    idx = DocIndex(PAGES)
    m = idx.find("Either party may terminate this Agreement for any reason by giving sixty 60 days written notice")
    assert m is not None and m.kind == "loose"


def test_contains_for_literals_like_party_names():
    idx = DocIndex(PAGES)
    assert idx.contains("alpha cloud systems inc.")
    assert not idx.contains("Beta Corp")


def test_sections_are_detected_with_preamble_and_labels():
    labels = [s.label for s in build_sections(PAGES)]
    assert labels[0] == "Cloud Services Agreement"
    assert "Section 1 — Payment Terms" in labels
    assert "Section 10 — Confidentiality" in labels


def test_document_without_headings_falls_back_to_pages():
    secs = build_sections([(1, "just some text here.\nmore text."), (2, "another page of text.")])
    assert [s.label for s in secs] == ["Page 1", "Page 2"]


def test_chunking_never_drops_text_and_splits_long_sections():
    long_body = "\n".join(f"line {i} of a very long clause about payment obligations" for i in range(400))
    pages = [(1, "1. Payment Terms\n" + long_body), (2, "2. Term\nShort section text here.")]
    secs = build_sections(pages)
    chunks = chunk_sections(secs, max_chars=2000, overlap=200)
    assert len(chunks) > 5
    assert all(len(c.text) <= 2100 for c in chunks)
    joined = "\n".join(c.text for c in chunks)
    for i in range(400):
        assert f"line {i} of" in joined
    assert "Short section text here." in joined


def test_pack_false_keeps_sections_separate():
    secs = build_sections(PAGES)
    packed = chunk_sections(secs, max_chars=10_000, pack=True)
    unpacked = chunk_sections(secs, max_chars=10_000, pack=False)
    assert len(packed) == 1
    assert len(unpacked) == len(secs)


def test_pages_from_raw_text_roundtrip():
    raw = "--- PAGE 1 ---\nfirst page\n\n--- PAGE 2 ---\nsecond page"
    assert pages_from_raw_text(raw) == [(1, "first page"), (2, "second page")]
    assert pages_from_raw_text(None) == []


# ─── models abbreviate quotes with an ellipsis: verify each fragment, in order ─

ELLIDED_PAGES = [
    (1, "1. Fees\nCustomer will pay Vendor a service fee of INR 60,000 per month, payable in advance. "
        "Vendor will issue an invoice by the second business day of each month.\n\n"
        "2. Term\nThis agreement continues for twelve months and then ends unless renewed.\n"),
]


def test_ellipsis_quote_is_verified_when_every_fragment_is_present_in_order():
    idx = DocIndex(ELLIDED_PAGES)
    m = idx.find("Customer will pay Vendor a service fee of INR 60,000 per month... Vendor will issue an invoice by the second business day")
    assert m is not None and m.kind == "elided" and m.page_start == 1
    assert "payable in advance" in m.matched_text         # the REAL text between the fragments, not the model's version


def test_unicode_ellipsis_and_leading_or_trailing_ellipsis_are_tolerated():
    idx = DocIndex(ELLIDED_PAGES)
    assert idx.find("\u2026service fee of INR 60,000 per month, payable in advance\u2026") is not None
    assert idx.find("Customer will pay Vendor a service fee of INR 60,000 per month\u2026 Vendor will issue an invoice") is not None
    assert idx.find("Customer will pay Vendor a service fee of INR 60,000 per month...") is not None


def test_ellipsis_quote_with_a_missing_fragment_is_rejected():
    idx = DocIndex(ELLIDED_PAGES)
    assert idx.find("Customer will pay Vendor a service fee of INR 60,000 per month... Vendor will refund every invoice immediately") is None


def test_ellipsis_fragments_out_of_document_order_are_rejected():
    idx = DocIndex(ELLIDED_PAGES)
    assert idx.find("This agreement continues for twelve months... Customer will pay Vendor a service fee of INR 60,000") is None


def test_ellipsis_cannot_stitch_together_distant_passages():
    filler = "Unrelated boilerplate sentence about notices. " * 60          # ~2,700 chars
    idx = DocIndex([(1, "Customer will pay Vendor a service fee of INR 60,000 per month.\n" + filler +
                        "Vendor will issue an invoice by the second business day.\n")])
    assert idx.find("Customer will pay Vendor a service fee of INR 60,000 per month... Vendor will issue an invoice by the second business day") is None


def test_a_quote_made_only_of_tiny_fragments_is_not_evidence():
    idx = DocIndex(ELLIDED_PAGES)
    assert idx.find("fee... pay... month") is None
