"""
quality_service.py
------------------
The data-quality centre: what is missing, unverified or unreadable across all contracts, as a
to-do list. Everything here is computed from stored data. Nothing is guessed, and a field a person
has already reviewed is not reported again.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import (
    Contract, ContractStatus, ContractVersion, ExtractedField, FieldStatus, ObligationStatus,
)
from app.services.compare_service import FIELD_LABELS

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
KEY_FIELDS = ["effective_date", "expiration_date", "auto_renew", "renewal_notice_period",
              "termination_notice_period", "payment_terms"]


def _reviewed(f: ExtractedField) -> bool:
    return bool((f.notes or {}).get("user_review"))


def _has_value(f: Optional[ExtractedField]) -> bool:
    return f is not None and f.value is not None and f.status not in (
        FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value)


def _solid(f: Optional[ExtractedField]) -> bool:
    """Has a value that is verified against the document, or that a person confirmed."""
    return _has_value(f) and (f.status == FieldStatus.VERIFIED.value or _reviewed(f))


ISSUES = {
    "cannot_read": ("high", "Could not be read", "These files could not be processed, so nothing was extracted.", "Open the contract"),
    "analysis_incomplete": ("high", "Analysis incomplete", "Only part of the document was analysed, or the AI did not run. An empty result here does not mean the contract is clear.", "Re-run the analysis"),
    "no_expiry": ("high", "No expiry date found", "Without an expiry date there are no renewal or expiry alerts for this contract.", "Add the date"),
    "renewal_notice_unknown": ("medium", "Auto-renews, but the notice period is unknown", "The deadline to stop the renewal cannot be worked out without the notice period.", "Add the notice period"),
    "fields_need_review": ("medium", "Values that need a check", "These values could not be fully verified against the document.", "Review the values"),
    "no_effective_date": ("low", "No effective date found", "Some due dates that count from the start date cannot be worked out.", "Add the date"),
    "undated_obligations": ("low", "Obligations without a due date", "These will not appear on the calendar or in alerts.", "Open obligations"),
    "obligations_unverified": ("low", "Obligations not verified against the text", "Their wording could not be found in the document.", "Open obligations"),
    "expired": ("low", "Already expired", "The expiry date has passed. If it should still be tracked, check the date.", "Check the contract"),
}


async def build_report(db: AsyncSession, user_id, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    rows = (await db.execute(
        select(Contract, ContractVersion)
        .join(ContractVersion, ContractVersion.id == Contract.current_version_id)
        .where(Contract.user_id == user_id, Contract.deleted_at.is_(None))
        .options(selectinload(ContractVersion.fields), selectinload(ContractVersion.obligations))
        .order_by(Contract.created_at.desc())
    )).all()

    buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in ISSUES}
    coverage = {k: {"have": 0, "total": 0} for k in KEY_FIELDS}
    processing = 0
    analysed = 0

    def add(code: str, contract: Contract, detail: str, field_key: Optional[str] = None) -> None:
        buckets[code].append({"contract_id": contract.id, "title": contract.title, "detail": detail, "field_key": field_key})

    for contract, version in rows:
        if version.status in (ContractStatus.PENDING, ContractStatus.PROCESSING):
            processing += 1
            continue
        if version.status in (ContractStatus.FAILED, ContractStatus.UNSUPPORTED):
            add("cannot_read", contract, version.extraction_error or "The file could not be read.")
            continue
        ex = version.extraction_status
        if ex in ("partial", "unavailable", "legacy"):
            add("analysis_incomplete", contract, {"partial": "Only part of the document was analysed.", "unavailable": "AI extraction did not run.",
                                                    "legacy": "Analysed by an older version without verification."}[ex])
            if ex != "partial":
                continue
        analysed += 1
        by = {f.field_key: f for f in version.fields}

        for key in KEY_FIELDS:
            coverage[key]["total"] += 1
            coverage[key]["have"] += 1 if _solid(by.get(key)) else 0

        exp, eff = by.get("expiration_date"), by.get("effective_date")
        if ex == "complete":
            if not _has_value(exp) and not (exp and _reviewed(exp)):
                add("no_expiry", contract, "The contract does not state an expiry date, or it could not be found.", "expiration_date")
            if not _has_value(eff) and not (eff and _reviewed(eff)):
                add("no_effective_date", contract, "No effective date was found.", "effective_date")
            ar, notice = by.get("auto_renew"), by.get("renewal_notice_period")
            if ar and (ar.value or {}).get("bool") is True and not _has_value(notice) and not (notice and _reviewed(notice)):
                add("renewal_notice_unknown", contract, "Auto-renewal is on but no renewal notice period was found.", "renewal_notice_period")

        shaky = [FIELD_LABELS.get(f.field_key, f.field_key) for f in version.fields
                 if f.status == FieldStatus.NEEDS_REVIEW.value and not _reviewed(f)]
        if shaky:
            add("fields_need_review", contract, ", ".join(shaky))

        pending = [o for o in version.obligations if o.status == ObligationStatus.PENDING]
        undated = [o for o in pending if o.due_date is None]
        if undated:
            add("undated_obligations", contract, f"{len(undated)} of {len(pending)} open obligations have no due date.")
        unverified = [o for o in version.obligations if o.verification_status != FieldStatus.VERIFIED.value]
        if unverified:
            add("obligations_unverified", contract, f"{len(unverified)} of {len(version.obligations)} obligations.")
        if version.expiry_date and version.expiry_date < today:
            add("expired", contract, f"Expired on {version.expiry_date.isoformat()}.")

    issues = []
    for code, items in buckets.items():
        if not items:
            continue
        sev, title, why, action = ISSUES[code]
        issues.append({"code": code, "severity": sev, "title": title, "why": why, "action": action,
                       "count": len(items), "contracts": items})
    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], -i["count"]))

    return {
        "generated_at": datetime.utcnow(),
        "contracts_total": len(rows),
        "processing": processing,
        "analysed": analysed,
        "open_issues": sum(i["count"] for i in issues),
        "coverage": [
            {"field_key": k, "label": FIELD_LABELS.get(k, k), "have": v["have"], "total": v["total"]}
            for k, v in coverage.items()
        ],
        "issues": issues,
    }
