"""Phase 5: due dates are computed in code. Known-answer tests for the calendar arithmetic."""

from datetime import date

import pytest

from app.services.schedule_service import (
    Anchors, add_months, add_period, compute_relative, generate_occurrences, renewal_notice_date,
)

TODAY = date(2026, 9, 20)
EFF = date(2026, 1, 15)
EXP = date(2028, 12, 31)
A = Anchors(effective=EFF, expiry=EXP)


# ─── calendar arithmetic ──────────────────────────────────────────────────────

@pytest.mark.parametrize("d,n,expected", [
    (date(2026, 1, 31), 1, date(2026, 2, 28)),          # clamped, not an invalid date
    (date(2028, 1, 31), 1, date(2028, 2, 29)),          # leap year
    (date(2026, 12, 15), 2, date(2027, 2, 15)),         # crosses the year
    (date(2026, 3, 31), -1, date(2026, 2, 28)),
    (date(2026, 1, 15), -14, date(2024, 11, 15)),
    (date(2026, 5, 31), 6, date(2026, 11, 30)),
])
def test_add_months_is_calendar_aware(d, n, expected):
    assert add_months(d, n) == expected


def test_add_period_units():
    assert add_period(date(2026, 1, 15), 30, "days") == date(2026, 2, 14)
    assert add_period(date(2026, 1, 15), 2, "weeks") == date(2026, 1, 29)
    assert add_period(date(2026, 1, 15), 3, "months") == date(2026, 4, 15)
    assert add_period(date(2028, 2, 29), 1, "years") == date(2029, 2, 28)
    assert add_period(date(2026, 1, 15), 30, "days", -1) == date(2025, 12, 16)
    with pytest.raises(ValueError):
        add_period(date(2026, 1, 1), 1, "fortnights")


# ─── relative rules ───────────────────────────────────────────────────────────

def test_relative_after_the_effective_date():
    due, basis = compute_relative({"value": 30, "unit": "days", "direction": "after", "anchor": "the effective date"}, A)
    assert due == date(2026, 2, 14) and "30 days after the effective date (2026-01-15)" == basis


def test_relative_before_expiration():
    due, _ = compute_relative({"value": 60, "unit": "days", "direction": "before", "anchor": "the end of the then-current term"}, A)
    assert due == date(2028, 11, 1)


def test_relative_months_use_the_calendar():
    due, _ = compute_relative({"value": 3, "unit": "months", "direction": "after", "anchor": "commencement"}, A)
    assert due == date(2026, 4, 15)


@pytest.mark.parametrize("anchor", ["receipt of the invoice", "the date of termination notice", None, "delivery of the goods"])
def test_event_based_rules_are_not_given_an_invented_date(anchor):
    due, why = compute_relative({"value": 30, "unit": "days", "direction": "after", "anchor": anchor}, A)
    assert due is None and why


def test_missing_anchor_date_gives_a_reason_not_a_date():
    due, why = compute_relative({"value": 30, "unit": "days", "direction": "after", "anchor": "the effective date"},
                                Anchors(effective=None, expiry=EXP))
    assert due is None and "effective date, which was not found" in why


def test_garbage_rule_is_rejected():
    assert compute_relative({"value": None, "unit": "days", "anchor": "the effective date"}, A)[0] is None
    assert compute_relative({"value": 5, "unit": "fortnights", "anchor": "the effective date"}, A)[0] is None


# ─── recurring rules ──────────────────────────────────────────────────────────

def test_quarterly_with_an_offset_after_quarter_end():
    occ = generate_occurrences({"recurrence": "QUARTERLY", "offset_value": 15, "offset_unit": "days"}, "", A, TODAY)
    ds = [d for d, _ in occ.dates]
    assert date(2026, 10, 15) in ds and date(2027, 1, 15) in ds and date(2027, 4, 15) in ds and date(2027, 7, 15) in ds
    assert ds == sorted(ds) and ds[-1] <= EXP
    assert all("end of the quarter" in b for _, b in occ.dates)
    assert min(ds) >= date(2026, 5, 1)                      # only recently-past + future, not the whole history


def test_quarterly_without_offset_is_the_quarter_end():
    ds = [d for d, _ in generate_occurrences({"recurrence": "QUARTERLY"}, "", A, TODAY).dates]
    assert date(2026, 9, 30) in ds and date(2026, 12, 31) in ds


def test_monthly_on_a_stated_day_including_short_months():
    ds = [d for d, _ in generate_occurrences({"recurrence": "MONTHLY"}, "on the 31st of each month", A, TODAY).dates]
    assert date(2026, 10, 31) in ds and date(2026, 11, 30) in ds and date(2027, 2, 28) in ds and date(2028, 2, 29) in ds


def test_monthly_first_of_each_month():
    ds = [d for d, _ in generate_occurrences({"recurrence": "MONTHLY"}, "on the 1st of each calendar month", A, TODAY).dates]
    assert date(2026, 10, 1) in ds and date(2026, 11, 1) in ds and all(d.day == 1 for d in ds)


def test_annual_uses_anniversaries_of_the_effective_date():
    ds = [d for d, _ in generate_occurrences({"recurrence": "ANNUALLY"}, "annually", A, TODAY).dates]
    assert ds == [date(2027, 1, 15), date(2028, 1, 15)]


def test_annual_report_with_an_explicit_first_date_repeats_from_that_date():
    ds = [d for d, _ in generate_occurrences({"recurrence": "ANNUALLY"}, "by March 31, 2027", A, TODAY,
                                             first_fixed=date(2027, 3, 31)).dates]
    assert ds == [date(2027, 3, 31), date(2028, 3, 31)]


def test_weekly_and_semiannual():
    w = [d for d, _ in generate_occurrences({"recurrence": "WEEKLY"}, "weekly", A, date(2026, 1, 20)).dates]
    assert w[0] >= date(2025, 12, 1) and all((b - a).days == 7 for a, b in zip(w, w[1:]))
    s = [d for d, _ in generate_occurrences({"recurrence": "SEMIANNUALLY"}, "", A, TODAY).dates]
    assert date(2026, 12, 31) in s and date(2027, 6, 30) in s


def test_no_effective_date_means_no_occurrences_and_a_reason():
    occ = generate_occurrences({"recurrence": "QUARTERLY"}, "", Anchors(effective=None, expiry=EXP), TODAY)
    assert occ.dates == [] and "effective date" in occ.reason


def test_occurrences_stop_at_expiry_and_at_the_horizon():
    short = Anchors(effective=EFF, expiry=date(2026, 12, 31))
    assert max(d for d, _ in generate_occurrences({"recurrence": "MONTHLY"}, "", short, TODAY).dates) <= date(2026, 12, 31)
    forever = Anchors(effective=EFF, expiry=None)
    occ = generate_occurrences({"recurrence": "MONTHLY"}, "", forever, TODAY)
    assert max(d for d, _ in occ.dates) <= date(2028, 9, 20) and len(occ.dates) <= 60


def test_expired_contract_has_no_future_occurrences():
    occ = generate_occurrences({"recurrence": "MONTHLY"}, "", Anchors(effective=date(2020, 1, 1), expiry=date(2021, 1, 1)), TODAY)
    assert occ.dates == [] and "ended" in occ.reason


# ─── renewal notice: the actionable deadline ──────────────────────────────────

def test_renewal_notice_is_expiry_minus_the_notice_period():
    due, basis = renewal_notice_date(EXP, {"value": 60, "unit": "days"})
    assert due == date(2028, 11, 1) and "2028-12-31 minus 60 days" in basis


def test_renewal_notice_in_months_is_calendar_exact():
    assert renewal_notice_date(date(2027, 3, 31), {"value": 3, "unit": "months"})[0] == date(2026, 12, 31)


def test_renewal_notice_needs_both_inputs():
    assert renewal_notice_date(None, {"value": 60, "unit": "days"})[0] is None
    assert renewal_notice_date(EXP, None)[0] is None
    assert renewal_notice_date(EXP, {"value": 5, "unit": "fortnights"})[0] is None
