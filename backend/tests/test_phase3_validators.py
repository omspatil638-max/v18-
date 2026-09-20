"""Phase 3: code-side validation — party names, term dates, due-rule classification."""

from datetime import date

import pytest

from app.services import validators as v


# ─── party names: roles are how a contract REFERS to a party, not who it is ────

@pytest.mark.parametrize("name", [
    "Provider", "the Provider", "PROVIDER", "Customer", "the Customer", '"Customer"', "Receiving Party",
    "Disclosing Party", "Licensor", "the Licensee", "Client", "Vendor", "Either Party", "Party A", "Company",
])
def test_role_labels_are_not_entities(name):
    assert v.is_role_label(name) and not v.looks_like_entity(name)


@pytest.mark.parametrize("name", [
    "Northwind Analytics Inc.", "Contoso Retail LLC", "Provider Services Ltd", "Acme Company Inc",
    "Customer Success Partners LLP", "Tata Consultancy Services Limited", "Müller GmbH", "Jane Q. Smith",
])
def test_real_names_are_kept_even_when_they_contain_a_role_word(name):
    assert v.looks_like_entity(name) and not v.is_role_label(name)


@pytest.mark.parametrize("name", ["", "   ", None, "A", "x" * 200])
def test_empty_or_absurd_names_are_not_entities(name):
    assert not v.looks_like_entity(name)


def test_normalize_party_name_strips_articles_and_punctuation():
    assert v.normalize_party_name('  "The Northwind Analytics Inc.",  ') == "Northwind Analytics Inc."


# ─── term dates ────────────────────────────────────────────────────────────────

def test_term_must_end_after_it_starts():
    ok, msg = v.term_dates_consistent(date(2026, 1, 15), date(2028, 12, 31))
    assert ok and msg is None
    ok, msg = v.term_dates_consistent(date(2028, 12, 31), date(2026, 1, 15))
    assert not ok and "before" in msg
    ok, msg = v.term_dates_consistent(date(2026, 1, 1), date(2026, 1, 1))
    assert not ok and "same" in msg


def test_absurdly_long_term_is_rejected():
    ok, msg = v.term_dates_consistent(date(2026, 1, 1), date(2099, 1, 1))
    assert not ok and "50 years" in msg


def test_missing_date_is_not_an_inconsistency():
    assert v.term_dates_consistent(None, date(2028, 1, 1)) == (True, None)
    assert v.term_dates_consistent(date(2026, 1, 1), None) == (True, None)


def test_implausible_dates():
    assert v.date_is_plausible(date(2026, 6, 1))
    assert not v.date_is_plausible(date(1850, 1, 1))
    assert not v.date_is_plausible(date(2500, 1, 1))
    assert not v.date_is_plausible(None)


# ─── due rules: classified in code, never invented ────────────────────────────

def test_fixed_date_rule():
    t, rule = v.classify_due_rule("by March 31, 2027", "Customer shall deliver the report by March 31, 2027.")
    assert t == "fixed" and rule == {"date": "2027-03-31"}


@pytest.mark.parametrize("text,expected", [
    ("within 30 days of the effective date", (30, "days", "after")),
    ("within sixty (60) days of receipt of the invoice", (60, "days", "after")),
    ("within 2 weeks following termination", (2, "weeks", "after")),
])
def test_relative_rules(text, expected):
    t, rule = v.classify_due_rule(text)
    assert t == "relative"
    assert (rule["value"], rule["unit"], rule["direction"]) == expected


def test_relative_rule_captures_its_anchor():
    _, rule = v.classify_due_rule("within 30 days of the effective date")
    assert "effective date" in rule["anchor"]


def test_prior_to_rules_point_backwards():
    t, rule = v.classify_due_rule("at least sixty (60) days before the end of the then-current term")
    assert t == "relative" and rule["direction"] == "before" and rule["value"] == 60 and rule["unit"] == "days"


@pytest.mark.parametrize("text,recurrence", [
    ("on the 1st of each calendar month", "MONTHLY"),
    ("quarterly", "QUARTERLY"),
    ("annually", "ANNUALLY"),
    ("every year", "ANNUALLY"),
    ("weekly", "WEEKLY"),
])
def test_recurring_rules(text, recurrence):
    t, rule = v.classify_due_rule(text)
    assert t == "recurring" and rule["recurrence"] == recurrence


def test_recurring_rule_keeps_its_offset():
    t, rule = v.classify_due_rule("within 15 days after the end of each calendar quarter")
    assert t == "recurring" and rule["recurrence"] == "QUARTERLY"
    assert rule["offset_value"] == 15 and rule["offset_unit"] == "days"


@pytest.mark.parametrize("text", ["", "not specified", "as agreed by the parties", "promptly", None])
def test_no_timing_stated_is_none_not_a_guess(text):
    assert v.classify_due_rule(text) == ("none", {})


def test_ambiguous_numeric_date_is_not_treated_as_fixed():
    t, _ = v.classify_due_rule("by 03/04/2027")
    assert t == "none"


def test_implausible_year_is_not_a_fixed_date():
    t, _ = v.classify_due_rule("by January 1, 1850")
    assert t != "fixed"


def test_incidental_recurrence_word_does_not_override_an_explicit_date():
    t, rule = v.classify_due_rule("by March 31, 2027", "Customer shall deliver an annual security audit report by March 31, 2027.")
    assert t == "fixed" and rule["date"] == "2027-03-31" and rule["recurrence_hint"] == "ANNUALLY"


def test_quote_is_used_only_when_the_rule_itself_says_nothing():
    t, rule = v.classify_due_rule("not specified", "Provider shall deliver a report within 15 days after the end of each quarter.")
    assert t == "recurring" and rule["recurrence"] == "QUARTERLY"
