"""
portfolio_service.py
--------------------
"Ask across all contracts" without letting a model state facts.

A question is turned into a structured FILTER (which contracts expire in a period, auto-renew, have
a notice period longer than N days, carry high-priority flags, ...). The filter is then run IN CODE
over the stored, source-linked values. What comes back is real rows: the matching contracts, with
the value that made each one match, its verification status and its source page. A model may only
translate the wording of the question into the filter; it never sees contract text and never
writes an answer. Common phrasings are understood by rules, with no model at all.

If the question is not a filter, the fallback is a keyword search over the contract text, which
returns real passages with their pages ("which contracts mention arbitration?").
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Contract, ContractStatus, ContractVersion, ExtractedField, FieldStatus
from app.services import schedule_service as sched
from app.services import search_service
from app.services.compare_service import FIELD_LABELS
from app.services.llm_service import LLMError, LLMUnavailable, llm_service
from app.services.validators import parse_date

MAX_MATCHES = 50
EXAMPLES = [
    "Which contracts expire in the next 90 days?",
    "Which contracts auto-renew?",
    "Which contracts expire this quarter and auto-renew?",
    "Which contracts have a notice period longer than 60 days?",
    "Which contracts have high-priority review flags?",
    "Which contracts mention arbitration?",
]


# ─── the filter ───────────────────────────────────────────────────────────────

@dataclass
class Filter:
    expires_from: Optional[date] = None
    expires_to: Optional[date] = None
    auto_renew: Optional[bool] = None
    notice_scope: str = "either"                # which notice period the *_days_* conditions look at
    notice_days_gt: Optional[int] = None
    notice_days_lt: Optional[int] = None
    has_high_flags: bool = False
    needs_review: bool = False
    no_expiry: bool = False
    contract_type: Optional[str] = None
    tag: Optional[str] = None
    counterparty: Optional[str] = None

    def is_empty(self) -> bool:
        return not any([self.expires_from, self.expires_to, self.auto_renew is not None, self.notice_days_gt is not None,
                        self.notice_days_lt is not None, self.has_high_flags, self.needs_review, self.no_expiry,
                        self.contract_type, self.tag, self.counterparty])

    def describe(self) -> List[str]:
        out: List[str] = []
        if self.expires_from and self.expires_to:
            out.append(f"Expires between {self.expires_from.isoformat()} and {self.expires_to.isoformat()}")
        elif self.expires_to:
            out.append(f"Expired before {self.expires_to.isoformat()}" if self.expires_to < date.today() else f"Expires by {self.expires_to.isoformat()}")
        elif self.expires_from:
            out.append(f"Expires on or after {self.expires_from.isoformat()}")
        if self.auto_renew is True:
            out.append("Renews automatically")
        elif self.auto_renew is False:
            out.append("Does not renew automatically")
        scope = {"termination": "Termination notice period", "renewal": "Renewal notice period", "either": "Notice period"}[self.notice_scope]
        if self.notice_days_gt is not None:
            out.append(f"{scope} longer than {self.notice_days_gt} days")
        if self.notice_days_lt is not None:
            out.append(f"{scope} shorter than {self.notice_days_lt} days")
        if self.has_high_flags:
            out.append("Has open high-priority review flags")
        if self.needs_review:
            out.append("Has values that need a check")
        if self.no_expiry:
            out.append("No expiry date found")
        if self.contract_type:
            out.append(f"Type: {self.contract_type}")
        if self.tag:
            out.append(f"Tagged: {self.tag}")
        if self.counterparty:
            out.append(f"Counterparty contains “{self.counterparty}”")
        return out


# ─── understanding the question: rules first (no model) ───────────────────────

_NUM = r"(\d{1,4})"
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _to_days(n: int, unit: str) -> int:
    return n * _UNIT_DAYS[unit.lower().rstrip("s")]


def quarter_bounds(d: date, offset: int = 0) -> Tuple[date, date]:
    q = (d.month - 1) // 3 + offset
    year = d.year + q // 4
    q = q % 4
    start = date(year, q * 3 + 1, 1)
    return start, sched.month_end(year, q * 3 + 3)


def rule_parse(question: str, today: date) -> Filter:
    q = " ".join(question.lower().replace("-", " ").split())
    f = Filter()

    # auto renewal (negations first)
    if re.search(r"\b(do(es)? not|don'?t|doesn'?t|without|no|non)\s+(auto\s?)?renew|not\s+(auto\s?)?renew|no auto\s?renew|non renewing", q):
        f.auto_renew = False
    elif re.search(r"auto\s?renew|renew(s|ing)? automatically|automatic(ally)? renew|automatic renewal|evergreen", q):
        f.auto_renew = True

    # expiry period
    expiry_words = r"\b(?:expir\w*|ends?|ending|lapse\w*|runs? out|finish\w*|due to end)\b"
    m = re.search(expiry_words + r".{0,30}?\b(within|in|over|during)\s+(the\s+)?(next|coming|following)?\s*" + _NUM + r"\s*(day|week|month|year)s?", q) or \
        re.search(r"\b(within|in)\s+(the\s+)?(next|coming)\s*" + _NUM + r"\s*(day|week|month|year)s?.{0,20}" + expiry_words, q)
    if m:
        nums = [g for g in m.groups() if g and g.isdigit()]
        unit = next(g for g in m.groups() if g and g.rstrip("s") in _UNIT_DAYS)
        n = int(nums[0])
        if unit.rstrip("s") == "month":
            f.expires_from, f.expires_to = today, sched.add_months(today, n)
        else:
            f.expires_from, f.expires_to = today, today + timedelta(days=_to_days(n, unit))
    elif re.search(expiry_words, q) or f.auto_renew is not None or re.search(r"\bup for renewal\b|\brenewals?\b", q):
        if re.search(r"this quarter", q):
            f.expires_from, f.expires_to = quarter_bounds(today, 0)
        elif re.search(r"next quarter", q):
            f.expires_from, f.expires_to = quarter_bounds(today, 1)
        elif re.search(r"this year", q):
            f.expires_from, f.expires_to = today, date(today.year, 12, 31)
        elif re.search(r"next year", q):
            f.expires_from, f.expires_to = date(today.year + 1, 1, 1), date(today.year + 1, 12, 31)
        elif re.search(r"this month", q):
            f.expires_from, f.expires_to = today, sched.month_end(today.year, today.month)
        elif re.search(r"next month", q):
            nm = sched.add_months(date(today.year, today.month, 1), 1)
            f.expires_from, f.expires_to = nm, sched.month_end(nm.year, nm.month)
        elif re.search(r"\b(already|have|has)\s+expired|\bexpired\b|\bare expired\b|\bpast their\b", q):
            f.expires_to = today - timedelta(days=1)
        elif re.search(r"\bsoon\b|\bupcoming\b|\bcoming up\b", q):
            f.expires_from, f.expires_to = today, today + timedelta(days=90)

    # notice period comparisons
    nm = re.search(r"notice(?: period)?\D{0,25}?(longer|more|greater|over|above|exceed\w*|at least)\D{0,12}?" + _NUM + r"\s*(day|week|month|year)s?", q)
    if nm:
        f.notice_days_gt = _to_days(int(nm.group(2)), nm.group(3)) - (1 if nm.group(1) == "at least" else 0)
    nm = re.search(r"notice(?: period)?\D{0,25}?(shorter|less|under|below|fewer|at most)\D{0,12}?" + _NUM + r"\s*(day|week|month|year)s?", q)
    if nm:
        f.notice_days_lt = _to_days(int(nm.group(2)), nm.group(3)) + (1 if nm.group(1) == "at most" else 0)
    if f.notice_days_gt is not None or f.notice_days_lt is not None:
        f.notice_scope = "termination" if "terminat" in q else "renewal" if "renew" in q else "either"

    if re.search(r"high (risk|priority|severity)|serious|critical|urgent flag|high flags?", q):
        f.has_high_flags = True
    if re.search(r"need(s)? (a )?(review|check)|unverified|not verified|to be reviewed", q):
        f.needs_review = True
    if re.search(r"no expiry|without an? expiry|missing (an? )?expiry|no end date|no expiration|missing expiration", q):
        f.no_expiry = True
    tag = re.search(r"(?:tagged|tag|labelled|labeled)\s+(?:\"([^\"]{1,30})\"|'([^']{1,30})'|([a-z0-9][a-z0-9_]{0,29}))", q)
    if tag:
        f.tag = next(g for g in tag.groups() if g).strip()
    return f


# ─── understanding the question: a model may translate wording, nothing more ─

FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "expires_within_days": {"type": ["integer", "null"]},
        "expires_from": {"type": ["string", "null"]},
        "expires_to": {"type": ["string", "null"]},
        "auto_renew": {"type": ["boolean", "null"]},
        "notice_scope": {"type": ["string", "null"], "enum": ["termination", "renewal", "either", None]},
        "notice_days_gt": {"type": ["integer", "null"]},
        "notice_days_lt": {"type": ["integer", "null"]},
        "has_high_flags": {"type": ["boolean", "null"]},
        "needs_review": {"type": ["boolean", "null"]},
        "no_expiry": {"type": ["boolean", "null"]},
        "contract_type": {"type": ["string", "null"]},
        "tag": {"type": ["string", "null"]},
        "counterparty": {"type": ["string", "null"]},
    },
    "required": ["supported", "expires_within_days", "expires_from", "expires_to", "auto_renew", "notice_scope", "notice_days_gt",
                 "notice_days_lt", "has_high_flags", "needs_review", "no_expiry", "contract_type", "tag", "counterparty"],
    "additionalProperties": False,
}

TRANSLATE_PROMPT = """You translate a question about a person's portfolio of contracts into a structured filter. You never answer the question and you have no access to any contract.
Today is {today}. Set only the conditions the question actually asks for; leave every other field null. Dates are YYYY-MM-DD.
- expires_within_days / expires_from / expires_to: when a contract's expiry date falls.
- auto_renew: true = renews automatically, false = does not.
- notice_scope + notice_days_gt / notice_days_lt: a notice period longer / shorter than N days (convert weeks, months, years to days).
- has_high_flags: contracts with high-priority review flags. needs_review: contracts with values that need a check.
- no_expiry: contracts with no expiry date. contract_type, tag, counterparty: exact words from the question.
If the question cannot be expressed with these conditions (for example it asks what a clause says), set supported=false and leave the rest null."""


def _int(v: Any, lo: int, hi: int) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _text(v: Any) -> Optional[str]:
    return " ".join(v.split())[:60] if isinstance(v, str) and v.strip() else None


def filter_from_model(data: Any, today: date) -> Optional[Filter]:
    """Validate the model's translation in code; anything out of range is dropped, never trusted."""
    if not isinstance(data, dict) or data.get("supported") is False:
        return None
    f = Filter()
    within = _int(data.get("expires_within_days"), 0, 3650)
    if within is not None:
        f.expires_from, f.expires_to = today, today + timedelta(days=within)
    a, b = parse_date(data.get("expires_from")), parse_date(data.get("expires_to"))
    if a and 1990 <= a.year <= today.year + 50:
        f.expires_from = a
    if b and 1990 <= b.year <= today.year + 50:
        f.expires_to = b
    if f.expires_from and f.expires_to and f.expires_from > f.expires_to:
        f.expires_from = f.expires_to = None
    if isinstance(data.get("auto_renew"), bool):
        f.auto_renew = data["auto_renew"]
    f.notice_scope = data.get("notice_scope") if data.get("notice_scope") in ("termination", "renewal", "either") else "either"
    f.notice_days_gt, f.notice_days_lt = _int(data.get("notice_days_gt"), 0, 3650), _int(data.get("notice_days_lt"), 1, 3650)
    f.has_high_flags = data.get("has_high_flags") is True
    f.needs_review = data.get("needs_review") is True
    f.no_expiry = data.get("no_expiry") is True
    f.contract_type, f.tag, f.counterparty = _text(data.get("contract_type")), _text(data.get("tag")), _text(data.get("counterparty"))
    return None if f.is_empty() else f


# ─── running the filter over the stored data ──────────────────────────────────

def _fact(f: ExtractedField) -> Dict[str, Any]:
    return {"label": FIELD_LABELS.get(f.field_key, f.field_key), "value": f.display_value or "", "status": f.status,
            "page": f.page, "quote": f.source_quote, "reviewed": bool((f.notes or {}).get("user_review"))}


def _has(f: Optional[ExtractedField]) -> bool:
    return f is not None and f.value is not None and f.status not in (FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value)


def evaluate(flt: Filter, contract: Contract, version: ContractVersion) -> Optional[List[Dict[str, Any]]]:
    """The facts that made this contract match, or None if it does not match. Unknown never counts as a match."""
    by = {f.field_key: f for f in version.fields}
    facts: List[Dict[str, Any]] = []

    exp = by.get("expiration_date")
    expiry = parse_date((exp.value or {}).get("date")) if _has(exp) else None
    if flt.no_expiry:
        if expiry is not None:
            return None
    if flt.expires_from or flt.expires_to:
        if expiry is None:
            return None
        if (flt.expires_from and expiry < flt.expires_from) or (flt.expires_to and expiry > flt.expires_to):
            return None
        facts.append(_fact(exp))
    if flt.auto_renew is not None:
        ar = by.get("auto_renew")
        if not _has(ar) or (ar.value or {}).get("bool") is not flt.auto_renew:
            return None
        facts.append(_fact(ar))
    if flt.notice_days_gt is not None or flt.notice_days_lt is not None:
        keys = {"termination": ["termination_notice_period"], "renewal": ["renewal_notice_period"],
                "either": ["termination_notice_period", "renewal_notice_period"]}[flt.notice_scope]
        hit = None
        for k in keys:
            nf = by.get(k)
            days = (nf.value or {}).get("days") if _has(nf) else None
            if not isinstance(days, int):
                continue
            if (flt.notice_days_gt is None or days > flt.notice_days_gt) and (flt.notice_days_lt is None or days < flt.notice_days_lt):
                hit = nf
                break
        if hit is None:
            return None
        facts.append(_fact(hit))
    open_flags = [x for x in version.flags if x.status.value == "OPEN"]
    if flt.has_high_flags:
        high = [x for x in open_flags if x.severity.value == "HIGH"]
        if not high:
            return None
        facts.append({"label": "High-priority flags", "value": f"{len(high)} open", "status": "flag", "page": high[0].source_page,
                      "quote": high[0].source_quote, "reviewed": False})
    if flt.needs_review:
        shaky = [x for x in version.fields if x.status == FieldStatus.NEEDS_REVIEW.value and not (x.notes or {}).get("user_review")]
        if not shaky:
            return None
        facts.append({"label": "Values that need a check", "value": ", ".join(FIELD_LABELS.get(x.field_key, x.field_key) for x in shaky),
                      "status": "needs_review", "page": None, "quote": None, "reviewed": False})
    if flt.no_expiry:
        facts.append({"label": "Expiry date", "value": "not found", "status": "not_found", "page": None, "quote": None, "reviewed": False})
    if flt.contract_type:
        if (contract.contract_type or "").lower() != flt.contract_type.lower():
            return None
        facts.append({"label": "Type", "value": contract.contract_type, "status": "user", "page": None, "quote": None, "reviewed": True})
    if flt.tag:
        if flt.tag.lower() not in [t.lower() for t in (contract.tags or [])]:
            return None
        facts.append({"label": "Tag", "value": flt.tag, "status": "user", "page": None, "quote": None, "reviewed": True})
    if flt.counterparty:
        names = [contract.counterparty or ""] + [p.name for p in version.parties]
        if not any(flt.counterparty.lower() in n.lower() for n in names):
            return None
        facts.append({"label": "Counterparty", "value": next(n for n in names if flt.counterparty.lower() in n.lower()),
                      "status": "party", "page": None, "quote": None, "reviewed": False})
    return facts


async def run_filter(db: AsyncSession, user_id, flt: Filter) -> Tuple[int, List[Dict[str, Any]]]:
    rows = (await db.execute(
        select(Contract, ContractVersion)
        .join(ContractVersion, ContractVersion.id == Contract.current_version_id)
        .where(Contract.user_id == user_id, Contract.deleted_at.is_(None), ContractVersion.status == ContractStatus.READY)
        .options(selectinload(ContractVersion.fields), selectinload(ContractVersion.flags), selectinload(ContractVersion.parties))
        .order_by(Contract.title)
    )).all()
    matches: List[Dict[str, Any]] = []
    for contract, version in rows:
        facts = evaluate(flt, contract, version)
        if facts is not None:
            matches.append({"contract_id": contract.id, "title": contract.title, "counterparty": contract.counterparty,
                            "contract_type": contract.contract_type, "tags": list(contract.tags or []), "facts": facts})
    return len(rows), matches[:MAX_MATCHES]


# ─── the entry point ──────────────────────────────────────────────────────────

def _summary(n_match: int, n_total: int, unverified: int) -> str:
    if n_match == 0:
        return f"No contract matches (out of {n_total} analysed)."
    s = f"{n_match} of {n_total} analysed contract{'s' if n_total != 1 else ''} match."
    if unverified:
        s += f" {unverified} of the matching values are not verified against the document; check them before relying on this."
    return s


async def ask(db: AsyncSession, user_id, question: str, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    question = search_service.clean_query(question)
    base: Dict[str, Any] = {"question": question, "interpretation": [], "interpreted_by": "none", "status": "not_understood",
                            "summary": "", "total_contracts": 0, "matches": [], "passages": [], "examples": EXAMPLES}
    if len(question) < 3:
        base["summary"] = "Ask a question about your contracts."
        return base

    flt, by = rule_parse(question, today), "rules"
    if flt.is_empty():
        flt, by = None, "none"
        if llm_service.is_configured:
            try:
                data = await llm_service.complete_json(TRANSLATE_PROMPT.format(today=today.isoformat()), f"Question: {question}",
                                                       FILTER_SCHEMA, "portfolio_filter", max_tokens=300)
                flt = filter_from_model(data, today)
                by = "ai" if flt else "none"
            except (LLMUnavailable, LLMError):
                base["status"] = "unavailable_ai"
    if flt is not None:
        total, matches = await run_filter(db, user_id, flt)
        unverified = sum(1 for m in matches for f in m["facts"] if f["status"] == "needs_review" and not f["reviewed"])
        base.update({"interpretation": flt.describe(), "interpreted_by": by, "status": "answered" if matches else "no_match",
                     "total_contracts": total, "matches": matches, "summary": _summary(len(matches), total, unverified)})
        return base

    # not a filter: real passages that mention the words, with their pages
    # A loose "any of these words" search is only offered when we know the question is about the text (the model
    # said so) or no model is involved; when the model was simply unavailable it would return unrelated passages.
    found = await search_service.search(db, user_id, question, lenient=base["status"] != "unavailable_ai")
    if found["passages"]:
        base.update({"interpreted_by": "keyword", "status": "passages",
                     "interpretation": [f"Passages of contract text matching “{question}”"], "passages": found["passages"],
                     "summary": f"{len(found['passages'])} passage(s) mention these words. This is a text search, not an answer; open each to read it in context."})
    elif base["status"] == "unavailable_ai":
        base["summary"] = "The AI helper that understands free-form questions is unavailable, and the question did not match a known pattern."
    else:
        base["summary"] = "I could not turn that into a search of your contracts."
    return base
