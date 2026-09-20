"""
policy_service.py
-----------------
Your own rules ("flag payment terms longer than 45 days", "flag any auto-renewal", "flag a missing
renewal notice period"). They are checked in code against values the app already extracted and
validated. No model decides anything, and a rule only fires on a value that is actually known: an
unknown value never triggers (or clears) a rule, it is simply not evaluated.

A rule that fires becomes an ordinary review flag ("POLICY_RULE") that names the rule, shows the
actual value and links to its source, and can be resolved or dismissed like any other flag.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Clause, Contract, ContractParty, ContractStatus, ContractVersion, ExtractedField, FieldStatus, Obligation, PolicyRule,
)
from app.services import flag_service
from app.services.flag_service import FlagDraft, H, L, M
from app.services.text_index import DocIndex, pages_from_raw_text

MAX_RULES_PER_USER = 30
SEVERITIES = {"HIGH": H, "MEDIUM": M, "LOW": L}
MISSING_TARGETS = {
    "effective_date": "Effective date", "expiration_date": "Expiration date", "renewal_terms": "Renewal terms",
    "auto_renew": "Auto-renewal", "renewal_notice_period": "Renewal notice period", "payment_terms": "Payment terms",
    "termination_conditions": "Termination conditions", "termination_notice_period": "Termination notice period",
}
NUMERIC_OPS = {"gt": "more than", "gte": "at least", "lt": "less than", "lte": "at most", "eq": "exactly"}

# What a rule can be about. Each entry says how the value is obtained, so the UI can offer only valid choices.
CATALOG: Dict[str, Dict[str, Any]] = {
    "termination_notice_days": {"label": "Termination notice period", "kind": "number", "unit": "days", "source": "termination_notice_period",
                                "operators": list(NUMERIC_OPS)},
    "renewal_notice_days": {"label": "Renewal notice period", "kind": "number", "unit": "days", "source": "renewal_notice_period",
                            "operators": list(NUMERIC_OPS)},
    "payment_days": {"label": "Payment period (days, read from the payment terms)", "kind": "number", "unit": "days", "source": "payment_terms",
                     "operators": list(NUMERIC_OPS)},
    "auto_renew": {"label": "Auto-renewal", "kind": "bool", "unit": None, "source": "auto_renew", "operators": ["is_true", "is_false"]},
    "missing_field": {"label": "A value is missing from the contract", "kind": "missing", "unit": None, "source": None, "operators": ["missing"]},
}

_PAYMENT_DAYS = [
    re.compile(r"\((\d{1,3})\)\s*(?:calendar\s+|business\s+|working\s+)?days", re.I),           # thirty (30) days
    re.compile(r"\bnet\s*(\d{1,3})\b", re.I),                                                     # net 45
    re.compile(r"\b(?:within|in|after|of)\s+(\d{1,3})\s*(?:calendar\s+|business\s+|working\s+)?days", re.I),
]


class RuleError(ValueError):
    """The rule is not valid. The message is safe to show."""


def payment_days_of(text: Optional[str]) -> Optional[int]:
    """The payment period in days as the payment terms state it, or None if it cannot be read (never guessed)."""
    if not text:
        return None
    found: List[Tuple[int, int]] = []
    for pat in _PAYMENT_DAYS:
        for m in pat.finditer(text):
            found.append((m.start(), int(m.group(1))))
    return min(found)[1] if found else None


def validate(field: str, operator: str, value: Any, target: Optional[str], severity: str, name: str) -> Tuple[Optional[int], Optional[str]]:
    """Returns the cleaned (value, target) or raises RuleError."""
    if not name or not name.strip():
        raise RuleError("Give the rule a name.")
    entry = CATALOG.get(field)
    if entry is None:
        raise RuleError("Unknown rule type.")
    if operator not in entry["operators"]:
        raise RuleError(f"'{operator}' cannot be used with {entry['label']}.")
    if severity not in SEVERITIES:
        raise RuleError("Severity must be HIGH, MEDIUM or LOW.")
    if entry["kind"] == "number":
        if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 3650):
            raise RuleError("Enter a whole number of days between 0 and 3650.")
        return value, None
    if entry["kind"] == "missing":
        if target not in MISSING_TARGETS:
            raise RuleError("Choose which value must be present.")
        return None, target
    return None, None


def _status_suffix(f: ExtractedField) -> str:
    if f.status == FieldStatus.VERIFIED.value or (f.notes or {}).get("user_review"):
        return ""
    return " This value is not verified against the document."


def _value_of(catalog_key: str, by: Dict[str, ExtractedField]) -> Tuple[Optional[Any], Optional[ExtractedField]]:
    entry = CATALOG[catalog_key]
    f = by.get(entry["source"]) if entry["source"] else None
    if f is None or f.value is None or f.status in (FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value):
        return None, f
    if catalog_key in ("termination_notice_days", "renewal_notice_days"):
        d = f.value.get("days")
        return (d if isinstance(d, int) else None), f
    if catalog_key == "payment_days":
        return payment_days_of(f.display_value), f
    if catalog_key == "auto_renew":
        b = f.value.get("bool")
        return (b if isinstance(b, bool) else None), f
    return None, f


def _compare(op: str, actual: int, threshold: int) -> bool:
    return {"gt": actual > threshold, "gte": actual >= threshold, "lt": actual < threshold,
            "lte": actual <= threshold, "eq": actual == threshold}[op]


def evaluate(rules: List[PolicyRule], fields: List[ExtractedField]) -> List[FlagDraft]:
    by = {f.field_key: f for f in fields}
    out: List[FlagDraft] = []
    for r in rules:
        if not r.enabled or r.field not in CATALOG:
            continue
        entry = CATALOG[r.field]
        sev = SEVERITIES.get(r.severity, M)
        note = (r.message or "").strip()

        if entry["kind"] == "missing":
            f = by.get(r.target or "")
            if f is None or f.status == FieldStatus.EXTRACTION_UNAVAILABLE.value:
                continue                                              # not analysed: cannot say it is missing
            if f.status == FieldStatus.NOT_FOUND.value or f.value is None:
                label = MISSING_TARGETS.get(r.target, r.target)
                out.append(FlagDraft("POLICY_RULE", sev, note or f"Your rule “{r.name}”: {label} was not found in this contract.",
                                     "field", f.id, r.name))
            continue

        actual, f = _value_of(r.field, by)
        if actual is None or f is None:
            continue                                                  # unknown: not evaluated
        if entry["kind"] == "bool":
            if (r.operator == "is_true" and actual is True) or (r.operator == "is_false" and actual is False):
                what = "renews automatically" if actual else "does not renew automatically"
                out.append(FlagDraft("POLICY_RULE", sev, note or f"Your rule “{r.name}”: this contract {what}.{_status_suffix(f)}",
                                     "field", f.id, r.name, f.page, f.section, f.source_quote))
            continue
        if _compare(r.operator, actual, int(r.value or 0)):
            out.append(FlagDraft(
                "POLICY_RULE", sev,
                note or f"Your rule “{r.name}”: {entry['label']} is {actual} {entry['unit']}, which is {NUMERIC_OPS[r.operator]} {r.value} {entry['unit']}.{_status_suffix(f)}",
                "field", f.id, r.name, f.page, f.section, f.source_quote))
    return out


async def drafts_for_user(db: AsyncSession, user_id, fields: List[ExtractedField]) -> List[FlagDraft]:
    rules = list((await db.execute(select(PolicyRule).where(PolicyRule.user_id == user_id, PolicyRule.enabled.is_(True)))).scalars())
    return evaluate(rules, fields) if rules else []


async def refresh_flags(db: AsyncSession, contract: Contract, version: ContractVersion) -> None:
    """Rebuild one version's flags (decisions on existing flags are kept by fingerprint)."""
    def q(model):
        return select(model).where(model.contract_version_id == version.id)

    fields = list((await db.execute(q(ExtractedField))).scalars())
    parties = list((await db.execute(q(ContractParty))).scalars())
    clauses = list((await db.execute(q(Clause))).scalars())
    obligations = list((await db.execute(q(Obligation))).scalars())
    meta = version.extraction_meta or {}
    await flag_service.generate_flags(
        db, contract, version, fields, parties, clauses, obligations, DocIndex(pages_from_raw_text(version.raw_text)),
        extraction_status=version.extraction_status, extraction_error=version.extraction_error,
        chunks_ok=meta.get("chunks_ok"), chunks_total=meta.get("chunks_total"), source=meta.get("source"),
    )


async def reapply(db: AsyncSession, user_id) -> int:
    """After a rule is added, changed or removed: refresh the flags of every live, analysed contract."""
    rows = (await db.execute(
        select(Contract, ContractVersion)
        .join(ContractVersion, ContractVersion.id == Contract.current_version_id)
        .where(Contract.user_id == user_id, Contract.deleted_at.is_(None), ContractVersion.status == ContractStatus.READY,
               ContractVersion.extraction_status.in_(["complete", "partial", "unavailable"]))
    )).all()
    for contract, version in rows:
        await refresh_flags(db, contract, version)
    return len(rows)
