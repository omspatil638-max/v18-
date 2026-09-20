"""
validators.py
-------------
Code-side validation. The LLM proposes values; nothing derived from a date, a notice
period or a number is trusted until these functions confirm it against the source quote.
"""

import re
from datetime import date
from typing import List, Optional, Tuple

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_MDY_TEXT = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)
_DMY_TEXT = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?!\d)")


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: Optional[str]) -> List[date]:
    """
    All unambiguous calendar dates in text. Numeric day/month dates such as 03/04/2026
    are accepted only when the order is unambiguous (one part > 12); otherwise skipped,
    because guessing DD/MM vs MM/DD would silently corrupt deadlines.
    """
    if not text:
        return []
    found: List[Tuple[int, date]] = []
    for m in _ISO.finditer(text):
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))
    for m in _MDY_TEXT.finditer(text):
        d = _safe_date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        if d:
            found.append((m.start(), d))
    for m in _DMY_TEXT.finditer(text):
        d = _safe_date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        if d:
            found.append((m.start(), d))
    for m in _NUMERIC.finditer(text):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        d = None
        if a > 12 >= b:
            d = _safe_date(y, b, a)
        elif b > 12 >= a or a == b:
            d = _safe_date(y, a, b)
        if d:
            found.append((m.start(), d))
    found.sort(key=lambda t: t[0])
    return [d for _, d in found]


def parse_date(value: Optional[str]) -> Optional[date]:
    """First unambiguous date in a string (ISO preferred), else None. Never guesses."""
    dates = find_dates(value)
    return dates[0] if dates else None


def date_supported_by_quote(d: Optional[date], quote: Optional[str]) -> bool:
    """A proposed date is only valid if that exact date is written in the quoted passage."""
    return d is not None and d in find_dates(quote)


def date_is_plausible(d: Optional[date]) -> bool:
    """Reject dates outside a sane contract window (OCR noise, mis-read years)."""
    return d is not None and 1990 <= d.year <= date.today().year + 50


def term_dates_consistent(effective: Optional[date], expiration: Optional[date]) -> Tuple[bool, Optional[str]]:
    """The term must not end before it starts, and must not be absurdly long."""
    if effective is None or expiration is None:
        return True, None
    if expiration < effective:
        return False, (
            f"The expiration date ({expiration.isoformat()}) is before the effective date "
            f"({effective.isoformat()}). One of them is wrong."
        )
    if expiration == effective:
        return False, "The expiration date is the same as the effective date."
    if (expiration - effective).days > 365 * 50:
        return False, "The term is longer than 50 years, which usually means a date was misread."
    return True, None


# ─── party names ──────────────────────────────────────────────────────────────

# Defined terms a contract uses to REFER to a party. They are not entity names, and
# the model reliably mistakes them for parties in later sections of a document.
ROLE_WORDS = {
    "provider", "customer", "client", "supplier", "vendor", "contractor", "subcontractor",
    "licensor", "licensee", "lessor", "lessee", "landlord", "tenant", "buyer", "seller",
    "purchaser", "employer", "employee", "consultant", "company", "corporation", "partner",
    "party", "parties", "disclosing party", "receiving party", "service provider", "reseller",
    "distributor", "agent", "principal", "borrower", "lender", "guarantor", "counterparty",
    "affiliate", "third party", "sender", "recipient", "each party", "either party", "both parties",
}

_PLACEHOLDER = re.compile(r"^(?:party|entity|company)\s*[a-z0-9]$", re.IGNORECASE)


_ABBREVIATION_END = re.compile(r"\b(inc|ltd|llc|corp|co|plc|l\.l\.c|s\.a|n\.v|b\.v|pty|pvt)\.$", re.IGNORECASE)


def normalize_party_name(name: Optional[str]) -> str:
    """Tidy a party name for comparison/display. A period that belongs to an abbreviation
    ('Inc.', 'Ltd.') is kept: stripping it would change the legal name."""
    n = " ".join((name or "").split()).strip(" ,;:\"'“”()")
    n = re.sub(r"^(the|a|an)\s+", "", n, flags=re.IGNORECASE)
    if n.endswith(".") and not _ABBREVIATION_END.search(n):
        n = n.rstrip(". ")
    return n


def is_role_label(name: Optional[str]) -> bool:
    """
    True if this is how the contract REFERS to a party ("Provider", "the Receiving Party")
    rather than who the party IS. A longer name that merely contains a role word
    ("Provider Services Ltd", "Acme Company Inc") is a real entity and is kept.
    """
    n = normalize_party_name(name).lower()
    if not n:
        return True
    return n in ROLE_WORDS or bool(_PLACEHOLDER.match(n))


def looks_like_entity(name: Optional[str]) -> bool:
    """A usable party name: not a role label, and long enough to identify someone."""
    n = normalize_party_name(name)
    return bool(n) and 2 <= len(n) <= 120 and not is_role_label(n)


# ─── obligation due rules ─────────────────────────────────────────────────────

RECURRENCES = {
    "daily": "DAILY", "weekly": "WEEKLY", "fortnightly": "FORTNIGHTLY", "biweekly": "FORTNIGHTLY",
    "monthly": "MONTHLY", "quarterly": "QUARTERLY", "annually": "ANNUALLY", "yearly": "ANNUALLY",
    "annual": "ANNUALLY", "semi-annually": "SEMIANNUALLY", "semiannually": "SEMIANNUALLY",
}
_PERIOD_UNITS = {"day": "days", "days": "days", "week": "weeks", "weeks": "weeks",
                 "month": "months", "months": "months", "year": "years", "years": "years"}

_RECURRING_PHRASES = [
    (re.compile(r"\beach\s+(?:calendar\s+)?month\b|\bevery\s+month\b|\bper\s+month\b|\bmonthly\b", re.I), "MONTHLY"),
    (re.compile(r"\beach\s+(?:calendar\s+)?quarter\b|\bevery\s+quarter\b|\bquarterly\b", re.I), "QUARTERLY"),
    (re.compile(r"\beach\s+(?:calendar\s+)?year\b|\bevery\s+year\b|\bannually\b|\byearly\b|\bper\s+annum\b|\bannual\b", re.I), "ANNUALLY"),
    (re.compile(r"\beach\s+week\b|\bevery\s+week\b|\bweekly\b", re.I), "WEEKLY"),
    (re.compile(r"\bsemi-?annually\b|\btwice\s+(?:a|per)\s+year\b", re.I), "SEMIANNUALLY"),
]

_RELATIVE = re.compile(
    r"\bwithin\s+(?:(\d{1,4})|([a-z\- ]{3,20}?))\s*(?:\((\d{1,4})\))?\s*(day|days|week|weeks|month|months|year|years)\b"
    r"(?:\s+(?:of|after|from|following)\s+(?:the\s+)?([a-z][a-z \-']{2,40}))?",
    re.IGNORECASE,
)
_PRIOR = re.compile(
    r"\b(?:at\s+least\s+)?(?:(\d{1,4})|([a-z\- ]{3,20}?))\s*(?:\((\d{1,4})\))?\s*(day|days|week|weeks|month|months|year|years)\s+"
    r"(?:prior\s+to|before)\s+(?:the\s+)?([a-z][a-z \-']{2,40})",
    re.IGNORECASE,
)

def _to_int(digits: Optional[str], words: Optional[str], paren: Optional[str]) -> Optional[int]:
    """Prefer the digits in '(30)', then plain digits, then a spelled-out number."""
    for candidate in (paren, digits):
        if candidate and candidate.isdigit():
            return int(candidate)
    if words:
        w = words.strip().lower()
        for n in range(1, 400):
            if w in number_words(n):
                return n
    return None


def classify_due_rule(due_rule: Optional[str], source_quote: Optional[str] = None):
    """
    Classify an obligation's timing into a structured rule that Phase 5 can compute:

      fixed     -> {"date": "YYYY-MM-DD"}            an explicit calendar date
      recurring -> {"recurrence": "QUARTERLY", ...}  repeats on a cycle
      relative  -> {"value": 30, "unit": "days", "anchor": "the effective date"}
      none      -> {}                                no timing stated

    The stated due rule decides. The surrounding quote is consulted only when the rule says
    nothing, so a word like "annual" in "deliver an annual report by March 31, 2027" does not
    override the explicit date; it is kept as a `recurrence_hint` for later review instead.
    Only text that is actually present is used; nothing is inferred.
    """
    kind, rule = _classify_text(due_rule, due_rule)
    if kind == "none" and source_quote:
        kind, rule = _classify_text(source_quote, source_quote)
    elif kind in ("fixed", "relative") and source_quote:
        for pattern, recurrence in _RECURRING_PHRASES:
            if pattern.search(source_quote) and not pattern.search(due_rule or ""):
                rule = {**rule, "recurrence_hint": recurrence}
                break
    return kind, rule


def _classify_text(primary: Optional[str], secondary: Optional[str]):
    due_rule, source_quote = primary, secondary
    text = " ".join(f"{due_rule or ''} {source_quote or ''}".split())
    if not text.strip():
        return "none", {}

    dates = find_dates(due_rule) or find_dates(source_quote)
    plausible = [d for d in dates if date_is_plausible(d)]

    for pattern, recurrence in _RECURRING_PHRASES:
        if pattern.search(text):
            rule = {"recurrence": recurrence}
            m = _RELATIVE.search(text)
            if m:
                value = _to_int(m.group(1), m.group(2), m.group(3))
                if value is not None:
                    rule.update({"offset_value": value, "offset_unit": _PERIOD_UNITS[m.group(4).lower()],
                                 "anchor": (m.group(5) or "").strip() or "period end"})
            return "recurring", rule

    if plausible:
        return "fixed", {"date": plausible[0].isoformat()}

    for pattern, direction in ((_RELATIVE, "after"), (_PRIOR, "before")):
        m = pattern.search(text)
        if m:
            value = _to_int(m.group(1), m.group(2), m.group(3))
            if value is not None:
                return "relative", {
                    "value": value, "unit": _PERIOD_UNITS[m.group(4).lower()],
                    "direction": direction, "anchor": (m.group(5) or "").strip() or None,
                }

    return "none", {}


# ─── numbers ──────────────────────────────────────────────────────────────────

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> List[str]:
    """English spellings of n (0-999), with and without hyphens."""
    if n < 0 or n > 999:
        return []
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        t, o = divmod(n, 10)
        return [_TENS[t]] if o == 0 else [f"{_TENS[t]}-{_ONES[o]}", f"{_TENS[t]} {_ONES[o]}"]
    h, rest = divmod(n, 100)
    head = f"{_ONES[h]} hundred"
    if rest == 0:
        return [head]
    return [f"{head} {w}" for w in number_words(rest)] + [f"{head} and {w}" for w in number_words(rest)]


def number_in_text(n: int, text: Optional[str]) -> bool:
    """True if n appears in text as digits or as English words."""
    if text is None:
        return False
    low = text.lower()
    if re.search(rf"(?<![\d,.]){n}(?![\d,]|\.\d)", low):
        return True
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in number_words(n))


def notice_period_days(value: Optional[int], unit: Optional[str]) -> Tuple[Optional[int], bool]:
    """
    Convert a stated notice period to days. Returns (days, approximate).
    Days and weeks are exact; months/years are approximated (30/365) and flagged so
    Phase 5 can compute exact calendar dates when a reference date exists.
    """
    if value is None or unit is None or isinstance(value, bool):
        return None, False
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None, False
    if v <= 0 or v > 3650:
        return None, False
    u = unit.lower().rstrip("s")
    if u == "day":
        return v, False
    if u == "week":
        return v * 7, False
    if u == "month":
        return v * 30, True
    if u == "year":
        return v * 365, True
    return None, False


# ─── amounts ──────────────────────────────────────────────────────────────────

_AMOUNT = re.compile(
    r"(?:[$€£₹]\s?(\d[\d,]*(?:\.\d+)?))|(?:(\d[\d,]*(?:\.\d+)?)\s?(?:USD|EUR|GBP|INR|dollars|euros|pounds|rupees)\b)",
    re.IGNORECASE,
)


def amounts_in(text: Optional[str]) -> set:
    """Monetary amounts written in text, normalized to plain numeric strings ('8,500.00' -> '8500')."""
    out = set()
    for m in _AMOUNT.finditer(text or ""):
        raw = (m.group(1) or m.group(2) or "").replace(",", "")
        if raw:
            out.add(raw[:-3] if raw.endswith(".00") else raw)
    return out


def amounts_supported(claimed_text: Optional[str], source_text: Optional[str]) -> Tuple[bool, List[str]]:
    """Every amount in a generated summary must also appear in the source passage."""
    src = amounts_in(source_text)
    missing = sorted(a for a in amounts_in(claimed_text) if a not in src)
    return (not missing), missing


# ─── confidence ───────────────────────────────────────────────────────────────

def compute_confidence(llm_confidence: Optional[float], quote_found: bool, valid: bool, corroborated: bool) -> float:
    """
    Deterministic, documented heuristic (NOT a calibrated probability):
      0.50 quote exists in the document   0.25 code validators passed
      0.10 same value seen in >1 place    0.15 x the model's own self-report
    An item whose quote cannot be found in the document is capped at 0.40.
    """
    llm = 0.0 if llm_confidence is None else max(0.0, min(1.0, float(llm_confidence)))
    score = 0.15 * llm + (0.5 if quote_found else 0.0) + (0.25 if valid else 0.0) + (0.1 if corroborated else 0.0)
    if not quote_found:
        score = min(score, 0.40)
    return round(min(score, 1.0), 2)
