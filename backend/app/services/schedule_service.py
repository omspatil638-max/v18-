"""
schedule_service.py
-------------------
Turns an obligation's TIMING RULE into dates. Everything here is plain calendar arithmetic in
code; the LLM only supplies the rule text. Nothing is guessed: if a rule depends on something
we do not know (an invoice date, a notice date, a missing effective date) the result is
"cannot be computed" plus the reason, never an invented date.

    fixed      an explicit calendar date in the document
    relative   "within 30 days of the effective date" -> anchor date + offset
    recurring  "quarterly", "on the 1st of each month" -> generated occurrences
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

HORIZON_DAYS = 730          # never generate occurrences further than two years ahead
LOOKBACK_DAYS = 120         # ...or keep past occurrences older than this (recently overdue ones stay visible)
MAX_OCCURRENCES = 60

_UNIT_DAYS = {"days": 1, "weeks": 7}


# ─── calendar arithmetic ──────────────────────────────────────────────────────

def add_months(d: date, months: int) -> date:
    """Calendar-aware: Jan 31 + 1 month = Feb 28/29 (clamped), never an invalid date."""
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def add_period(d: date, value: int, unit: str, sign: int = 1) -> date:
    unit = (unit or "days").lower()
    if unit in _UNIT_DAYS:
        return d + timedelta(days=sign * value * _UNIT_DAYS[unit])
    if unit == "months":
        return add_months(d, sign * value)
    if unit == "years":
        return add_months(d, sign * value * 12)
    raise ValueError(f"unsupported unit {unit!r}")


def month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


# ─── anchors ──────────────────────────────────────────────────────────────────

@dataclass
class Anchors:
    effective: Optional[date] = None
    expiry: Optional[date] = None

    def resolve(self, text: Optional[str]) -> Tuple[Optional[date], str]:
        """Map anchor wording to a known date. Returns (date|None, human label)."""
        t = (text or "").lower()
        if not t.strip():
            return None, "no anchor stated"
        if re.search(r"effective date|commencement|start date|date of (?:this )?agreement|signing|execution|signature|"
                     r"go-?live|closing date|beginning of the term", t):
            return self.effective, "the effective date"
        if re.search(r"expir|end of the (?:then-?current |initial |renewal )?term|end date|end of the contract|"
                     r"term ends|renewal date", t):
            return self.expiry, "the expiration date"
        return None, f"“{text.strip()}” (an event date the contract does not give)"


# ─── relative rules ───────────────────────────────────────────────────────────

def compute_relative(rule: Dict[str, Any], anchors: Anchors) -> Tuple[Optional[date], str]:
    value, unit = rule.get("value"), rule.get("unit")
    if not isinstance(value, int) or unit not in ("days", "weeks", "months", "years"):
        return None, "The timing could not be read as a number of days/weeks/months."
    base, label = anchors.resolve(rule.get("anchor"))
    if base is None:
        if label == "the effective date":
            return None, "This is measured from the effective date, which was not found in the contract."
        if label == "the expiration date":
            return None, "This is measured from the expiration date, which was not found in the contract."
        return None, f"This is measured from {label}, so no calendar date can be worked out."
    direction = -1 if rule.get("direction") == "before" else 1
    due = add_period(base, value, unit, direction)
    word = "before" if direction < 0 else "after"
    return due, f"{value} {unit} {word} {label} ({base.isoformat()})"


# ─── recurring rules ──────────────────────────────────────────────────────────

_DAY_OF_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b[^.]{0,40}?(?:each|every|per)\s+(?:calendar\s+)?month", re.I)
_LAST_DAY = re.compile(r"\blast\s+(?:business\s+)?day\s+of\s+(?:each|every|the)\s+(?:calendar\s+)?(?:month|quarter)\b", re.I)


@dataclass
class Occurrences:
    dates: List[Tuple[date, str]] = field(default_factory=list)      # (due date, basis)
    reason: Optional[str] = None                                      # why none could be generated


def _period_ends(recurrence: str, lo: date, hi: date) -> List[date]:
    """Calendar period-end dates between lo and hi (inclusive)."""
    step = {"MONTHLY": 1, "QUARTERLY": 3, "SEMIANNUALLY": 6, "ANNUALLY": 12}[recurrence]
    out: List[date] = []
    y, m = lo.year, lo.month
    while True:
        if step == 12:
            end = date(y, 12, 31)
            y += 1
        elif step == 1:
            end = month_end(y, m)
            m += 1
            if m > 12:
                y, m = y + 1, 1
        else:
            m_end = ((m - 1) // step + 1) * step
            end = month_end(y, m_end)
            m = m_end + 1
            if m > 12:
                y, m = y + 1, 1
        if end > hi:
            break
        if end >= lo:
            out.append(end)
        if len(out) > MAX_OCCURRENCES + 24:
            break
    return out


def generate_occurrences(
    rule: Dict[str, Any], due_rule_text: str, anchors: Anchors, today: date,
    first_fixed: Optional[date] = None,
) -> Occurrences:
    """Occurrences of a recurring obligation between (recently past) and the earlier of expiry / horizon."""
    rec = rule.get("recurrence")
    if rec not in ("DAILY", "WEEKLY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY", "SEMIANNUALLY", "ANNUALLY"):
        return Occurrences(reason="The repeat pattern was not recognised.")

    start = first_fixed or anchors.effective
    if start is None:
        return Occurrences(reason="The repeat schedule starts at the effective date, which was not found in the contract.")
    lo = max(start, today - timedelta(days=LOOKBACK_DAYS))
    hi = min(anchors.expiry, today + timedelta(days=HORIZON_DAYS)) if anchors.expiry else today + timedelta(days=HORIZON_DAYS)
    if hi < lo:
        return Occurrences(reason="The contract term has ended.")

    out: List[Tuple[date, str]] = []
    off_v, off_u = rule.get("offset_value"), rule.get("offset_unit")

    def shifted(d: date) -> date:
        return add_period(d, off_v, off_u) if isinstance(off_v, int) and off_u else d

    offset_txt = f" + {off_v} {off_u}" if isinstance(off_v, int) and off_u else ""

    if rec in ("DAILY", "WEEKLY", "FORTNIGHTLY"):
        step = {"DAILY": 1, "WEEKLY": 7, "FORTNIGHTLY": 14}[rec]
        d = start
        while d < lo:
            d += timedelta(days=step)
        while d <= hi and len(out) < MAX_OCCURRENCES:
            out.append((d, f"every {step} day(s) from {start.isoformat()}"))
            d += timedelta(days=step)
        return Occurrences(out)

    if rec == "MONTHLY":
        dm = _DAY_OF_MONTH.search(due_rule_text or "")
        if dm and 1 <= int(dm.group(1)) <= 31 and not off_v:
            day = int(dm.group(1))
            y, m = lo.year, lo.month
            while len(out) < MAX_OCCURRENCES:
                d = date(y, m, min(day, calendar.monthrange(y, m)[1]))
                if d > hi:
                    break
                if d >= lo:
                    out.append((d, f"the {day}{_ordinal(day)} of each month"))
                m += 1
                if m > 12:
                    y, m = y + 1, 1
            return Occurrences(out)
        if _LAST_DAY.search(due_rule_text or "") or off_v:
            for end in _period_ends("MONTHLY", start, hi + timedelta(days=400))[:MAX_OCCURRENCES + 24]:
                d = shifted(end)
                if lo <= d <= hi and len(out) < MAX_OCCURRENCES:
                    out.append((d, f"end of the month {end.isoformat()}{offset_txt}"))
            return Occurrences(out)
        # No day stated: same day each month as the start date.
        y, m = lo.year, lo.month
        while len(out) < MAX_OCCURRENCES:
            d = date(y, m, min(start.day, calendar.monthrange(y, m)[1]))
            if d > hi:
                break
            if d >= lo:
                out.append((d, f"monthly on day {start.day}, as no day is stated (from {start.isoformat()})"))
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return Occurrences(out)

    if rec == "ANNUALLY" and not (isinstance(off_v, int) and "year" in (rule.get("anchor") or "")):
        d = start
        n = 0
        while d < lo:
            n += 1
            d = add_months(start, 12 * n)
        while d <= hi and len(out) < MAX_OCCURRENCES:
            out.append((d, f"yearly anniversary of {start.isoformat()}"))
            n += 1
            d = add_months(start, 12 * n)
        return Occurrences(out)

    label = {"QUARTERLY": "quarter", "SEMIANNUALLY": "half-year", "ANNUALLY": "year"}[rec]
    for end in _period_ends(rec, start, hi + timedelta(days=400)):
        d = shifted(end)
        if lo <= d <= hi and len(out) < MAX_OCCURRENCES:
            out.append((d, f"end of the {label} {end.isoformat()}{offset_txt}"))
    return Occurrences(out)


def _ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ─── renewal notice (the "actionable" deadline) ───────────────────────────────

def renewal_notice_date(expiry: Optional[date], notice: Optional[Dict[str, Any]]) -> Tuple[Optional[date], str]:
    """Last day to give notice = expiration date minus the notice period (calendar-aware)."""
    if expiry is None:
        return None, "There is no expiration date, so a notice deadline cannot be worked out."
    if not notice or notice.get("value") is None or not notice.get("unit"):
        return None, "No notice period was found."
    value, unit = int(notice["value"]), str(notice["unit"]).lower()
    if unit not in ("days", "weeks", "months", "years"):
        return None, "The notice period unit is not recognised."
    due = add_period(expiry, value, unit, -1)
    return due, f"Expiration date {expiry.isoformat()} minus {value} {unit} notice"
