"""
renewal_service.py
------------------
The renewal radar: which contracts end or renew soon, and by when a person must act.

Everything is computed in code from stored, source-linked values:
  * the action date is the renewal-notice deadline (expiry minus the notice period) for a contract
    that auto-renews, otherwise the expiry date;
  * "what happens" is a sentence assembled from those values, never written by a model;
  * amounts are shown exactly as the contract writes them; they are never summed or annualised;
  * a value that is not verified says so.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Contract, ContractStatus, ContractVersion, ExtractedField, FieldStatus
from app.services import schedule_service as sched
from app.services.validators import _AMOUNT, parse_date

DEFAULT_WINDOW = 90


def _fdate(f: Optional[ExtractedField]) -> Optional[date]:
    if f is None or not f.value or f.status in (FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value):
        return None
    return parse_date(f.value.get("date"))


def _src(f: Optional[ExtractedField]) -> Optional[Dict[str, Any]]:
    if f is None or f.value is None:
        return None
    return {"status": f.status, "page": f.page, "section": f.section, "quote": f.source_quote}


def amounts_as_written(text: Optional[str], limit: int = 3) -> List[str]:
    out: List[str] = []
    for m in _AMOUNT.finditer(text or ""):
        a = " ".join(m.group(0).split()).rstrip(",.;:")     # the pattern can swallow the sentence's comma
        if a not in out:
            out.append(a)
    return out[:limit]


def bucket(days: int, kind: str) -> str:
    if kind in ("notice_missed", "expired"):
        return kind
    if days < 0:
        return "overdue"
    return "30" if days <= 30 else "60" if days <= 60 else "90" if days <= 90 else "later"


def what_happens(auto: Optional[bool], expiry: Optional[date], notice_by: Optional[date], has_notice_period: bool, today: date) -> str:
    """One plain sentence built from the values above. Says 'unknown' where the contract text was not read that way."""
    if expiry is None:
        return "No expiry date was found for this contract."
    ended = expiry < today
    if auto is True:
        if notice_by is not None:
            if notice_by < today <= expiry:
                return f"Renews automatically on {expiry.isoformat()}. The last day to give notice ({notice_by.isoformat()}) has passed."
            if ended:
                return f"Was due to renew automatically on {expiry.isoformat()}."
            return f"Renews automatically on {expiry.isoformat()} unless notice is given by {notice_by.isoformat()}."
        return (f"Renews automatically on {expiry.isoformat()}. The notice period was not found, so the deadline to stop the renewal cannot be worked out."
                if not ended else f"Was due to renew automatically on {expiry.isoformat()}.")
    if auto is False:
        return f"Ended on {expiry.isoformat()}." if ended else f"Ends on {expiry.isoformat()} unless a renewal is agreed."
    return (f"{'Ended' if ended else 'Ends'} on {expiry.isoformat()}. It could not be determined whether the contract renews automatically.")


async def build_radar(db: AsyncSession, user_id, window: int = DEFAULT_WINDOW, expired_days: int = 30, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    rows = (await db.execute(
        select(Contract, ContractVersion)
        .join(ContractVersion, ContractVersion.id == Contract.current_version_id)
        .where(Contract.user_id == user_id, Contract.deleted_at.is_(None), ContractVersion.status == ContractStatus.READY)
        .options(selectinload(ContractVersion.fields), selectinload(ContractVersion.flags))
        .order_by(Contract.title)
    )).all()

    items: List[Dict[str, Any]] = []
    not_tracked: List[Dict[str, Any]] = []
    for contract, version in rows:
        by = {f.field_key: f for f in version.fields}
        expiry = _fdate(by.get("expiration_date"))
        if expiry is None:
            if version.extraction_status in ("complete", "partial"):
                not_tracked.append({"contract_id": contract.id, "title": contract.title,
                                    "reason": "No expiry date found, so there are no renewal alerts for it."})
            continue
        ar_field = by.get("auto_renew")
        auto = (ar_field.value or {}).get("bool") if ar_field and ar_field.value and ar_field.status != FieldStatus.NOT_FOUND.value else None
        notice_f = by.get("renewal_notice_period")
        notice_by, notice_basis = None, None
        if notice_f and notice_f.value and notice_f.status not in (FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value):
            notice_by, notice_basis = sched.renewal_notice_date(expiry, notice_f.value)

        if auto is True and notice_by is not None:
            kind, action_date = ("notice_missed", notice_by) if notice_by < today <= expiry else ("give_notice_by", notice_by)
            if expiry < today:
                kind = "expired"
        else:
            kind, action_date = ("expired", expiry) if expiry < today else ("expires", expiry)
        days = (action_date - today).days

        if kind == "expired":
            if (today - expiry).days > expired_days:
                continue
        elif kind != "notice_missed" and days > window:
            continue

        open_flags = [f for f in version.flags if f.status.value == "OPEN"]
        items.append({
            "contract_id": contract.id, "title": contract.title, "counterparty": contract.counterparty,
            "contract_type": contract.contract_type, "tags": list(contract.tags or []),
            "kind": kind, "action_date": action_date, "days_to_action": days, "bucket": bucket(days, kind),
            "expiry_date": expiry, "days_to_expiry": (expiry - today).days,
            "auto_renew": auto, "notice_period": notice_f.display_value if notice_f and notice_f.value else None,
            "notice_deadline": notice_by, "notice_basis": notice_basis,
            "what_happens": what_happens(auto, expiry, notice_by, bool(notice_f and notice_f.value), today),
            "amounts": amounts_as_written(by["payment_terms"].display_value if by.get("payment_terms") else None),
            "sources": {"expiry": _src(by.get("expiration_date")), "auto_renew": _src(ar_field), "notice": _src(notice_f)},
            "unverified": [k for k in ("expiration_date", "auto_renew", "renewal_notice_period")
                           if by.get(k) and by[k].value is not None and by[k].status != FieldStatus.VERIFIED.value
                           and not (by[k].notes or {}).get("user_review")],
            "open_flags": len(open_flags),
        })
    items.sort(key=lambda i: (i["action_date"], i["title"].lower()))

    def count(pred) -> int:
        return sum(1 for i in items if pred(i))

    return {
        "generated_at": datetime.utcnow(), "window_days": window,
        "summary": {
            "due_30": count(lambda i: i["bucket"] == "30"), "due_60": count(lambda i: i["bucket"] in ("30", "60")),
            "due_90": count(lambda i: i["bucket"] in ("30", "60", "90")),
            "auto_renewing": count(lambda i: i["auto_renew"] is True and i["kind"] != "expired"),
            "notice_missed": count(lambda i: i["kind"] == "notice_missed"), "expired_recently": count(lambda i: i["kind"] == "expired"),
        },
        "items": items, "not_tracked": not_tracked,
    }
