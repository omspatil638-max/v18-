"""
review_service.py
-----------------
A person reviews an extracted field: confirm it, correct it, say the contract does not contain it,
or revert to what the AI found. The rules that keep this honest:

  * The value is validated in code (dates must be unambiguous and plausible, notice periods must be
    a number with a unit, the term must not end before it starts). Nothing is guessed.
  * The original AI value is kept (notes.user_review.original) so a correction can be undone.
  * A source quote is optional. If one is given it MUST be found in the document, and it must
    contain the value (the date, the number). Then, and only then, the field is `verified` with a
    page and section derived in code. Without a quote the field stays `needs_review`: a person
    typed it, the document has not been shown to say it.
  * A reviewed field no longer raises "low confidence" / "quote not found" flags: a person already
    looked at it. It keeps its `needs_review` status, so nothing is passed off as verified.
  * Deadlines, alerts and flags are rebuilt from the corrected values.
  * Re-processing a contract keeps the person's reviews (capture / reapply).
"""

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Alert, Clause, ContractParty, ContractVersion, Deadline, ExtractedField, FieldStatus, Obligation,
)
from app.services import deadline_service, flag_service
from app.services.text_index import DocIndex, pages_from_raw_text
from app.services.validators import (
    date_is_plausible, date_supported_by_quote, notice_period_days, number_in_text, parse_date, term_dates_consistent,
)

DATE_KEYS = {"effective_date", "expiration_date"}
NOTICE_KEYS = {"renewal_notice_period", "termination_notice_period"}
BOOL_KEYS = {"auto_renew"}
TEXT_KEYS = {"renewal_terms", "payment_terms", "termination_conditions"}
ACTIONS = ("confirm", "correct", "not_in_contract", "revert")
MAX_TEXT = 2000
MAX_QUOTE = 1200
HISTORY_CAP = 20
_NOTICE = re.compile(r"^\s*(\d{1,4})\s*(day|week|month|year)s?\s*$", re.IGNORECASE)
_TRUE, _FALSE = {"yes", "true", "y", "1"}, {"no", "false", "n", "0"}


class ReviewError(ValueError):
    """The reviewer's input cannot be accepted. The message is safe to show."""


# ─── value parsing (all in code) ──────────────────────────────────────────────

def parse_value(key: str, raw: Any) -> Tuple[Dict[str, Any], str]:
    """(stored value dict, display text) for a reviewer's input, or ReviewError."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ReviewError("Enter a value.")
    if key in DATE_KEYS:
        d = parse_date(str(raw))
        if d is None:
            raise ReviewError("Enter a clear date such as 2027-03-31 or March 31, 2027. Dates like 03/04/2027 are ambiguous, so they are not accepted.")
        if not date_is_plausible(d):
            raise ReviewError("That date is outside a plausible contract range.")
        return {"date": d.isoformat()}, d.isoformat()
    if key in NOTICE_KEYS:
        m = _NOTICE.match(str(raw))
        if not m:
            raise ReviewError("Enter a period such as 30 days, 4 weeks, 3 months or 1 year.")
        n, unit = int(m.group(1)), m.group(2).lower()
        days, approx = notice_period_days(n, unit)
        if days is None:
            raise ReviewError("That notice period is not valid (1 to 3650 days).")
        return {"days": days, "unit": unit + ("s" if n != 1 else ""), "value": n, "approximate": approx}, f"{n} {unit}{'s' if n != 1 else ''}"
    if key in BOOL_KEYS:
        if isinstance(raw, bool):
            b = raw
        else:
            s = str(raw).strip().lower()
            if s in _TRUE:
                b = True
            elif s in _FALSE:
                b = False
            else:
                raise ReviewError("Answer yes or no.")
        return {"bool": b}, "Yes" if b else "No"
    if key in TEXT_KEYS:
        text = " ".join(str(raw).split())
        if len(text) > MAX_TEXT:
            raise ReviewError(f"Keep it under {MAX_TEXT} characters.")
        return {"text": text}, text
    raise ReviewError("This field cannot be edited.")


def _snapshot(f: ExtractedField) -> Dict[str, Any]:
    return {
        "value": f.value, "display_value": f.display_value, "source_quote": f.source_quote, "page": f.page,
        "section": f.section, "confidence": f.confidence, "status": f.status,
        "notes": {k: v for k, v in (f.notes or {}).items() if k != "user_review"} or None,
    }


def is_reviewed(f: ExtractedField) -> bool:
    return bool((f.notes or {}).get("user_review"))


# ─── the review itself ────────────────────────────────────────────────────────

def _clean_notes(f: ExtractedField) -> Dict[str, Any]:
    """Notes without the extraction-time conflict/validation remarks: a person has resolved them."""
    return {k: v for k, v in (f.notes or {}).items() if k not in ("conflicts", "validation", "other_mentions")}


def _check_quote(doc: DocIndex, key: str, value: Dict[str, Any], quote: str):
    m = doc.find(quote)
    if m is None:
        raise ReviewError("That quote was not found in the document, so it cannot be used as the source.")
    text = " ".join(m.matched_text.split())[:MAX_QUOTE]
    if key in DATE_KEYS and not date_supported_by_quote(parse_date(value["date"]), m.matched_text):
        raise ReviewError("The quote does not contain that date.")
    if key in NOTICE_KEYS and not number_in_text(int(value["value"]), m.matched_text):
        raise ReviewError("The quote does not contain that number.")
    return m.page_start, m.section, text


def _term_check(fields: List[ExtractedField], key: str, value: Dict[str, Any]) -> None:
    if key not in DATE_KEYS:
        return
    other_key = "expiration_date" if key == "effective_date" else "effective_date"
    other = next((f for f in fields if f.field_key == other_key), None)
    other_d = parse_date((other.value or {}).get("date")) if other and other.value else None
    new_d = parse_date(value["date"])
    eff, exp = (new_d, other_d) if key == "effective_date" else (other_d, new_d)
    ok, why = term_dates_consistent(eff, exp)
    if not ok:
        raise ReviewError(why or "The term would end before it starts.")


def apply_to_field(
    f: ExtractedField, fields: List[ExtractedField], doc: DocIndex, action: str,
    raw_value: Any = None, quote: Optional[str] = None, note: Optional[str] = None,
) -> None:
    """Mutate one field according to the review. Raises ReviewError before changing anything."""
    if action not in ACTIONS:
        raise ReviewError("Unknown action.")
    review = dict((f.notes or {}).get("user_review") or {})
    now = datetime.utcnow().isoformat()
    before = f.display_value
    original = review.get("original") or _snapshot(f)

    if action == "revert":
        if not review.get("original"):
            raise ReviewError("This field has not been changed, so there is nothing to revert.")
        o = review["original"]
        f.value, f.display_value, f.source_quote, f.page, f.section = o["value"], o["display_value"], o["source_quote"], o["page"], o["section"]
        f.confidence, f.status, f.notes = o["confidence"], o["status"], o["notes"]
        return

    new: Dict[str, Any]
    if action == "confirm":
        if f.status in (FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value) or f.value is None:
            raise ReviewError("There is no value to confirm. Use Correct to enter one, or say it is not in the contract.")
        # A confirmed value becomes verified only if its source really is in the document.
        located = f.page is not None and f.source_quote and doc.find(f.source_quote) is not None
        new = {"value": f.value, "display_value": f.display_value, "source_quote": f.source_quote,
               "page": f.page, "section": f.section, "confidence": f.confidence,
               "status": FieldStatus.VERIFIED.value if located else f.status}
    elif action == "not_in_contract":
        new = {"value": None, "display_value": None, "source_quote": None, "page": None, "section": None,
               "confidence": None, "status": FieldStatus.NOT_FOUND.value}
    else:  # correct
        value, display = parse_value(f.field_key, raw_value)
        _term_check(fields, f.field_key, value)
        page = section = None
        text = None
        status = FieldStatus.NEEDS_REVIEW.value
        if quote and quote.strip():
            page, section, text = _check_quote(doc, f.field_key, value, quote)
            status = FieldStatus.VERIFIED.value
        new = {"value": value, "display_value": display, "source_quote": text, "page": page, "section": section,
               "confidence": None, "status": status}

    f.value, f.display_value, f.source_quote, f.page, f.section = new["value"], new["display_value"], new["source_quote"], new["page"], new["section"]
    f.confidence, f.status = new["confidence"], new["status"]
    history = list(review.get("history") or [])
    history.append({"at": now, "action": action, "from": before, "to": f.display_value})
    review = {
        "action": {"confirm": "confirmed", "correct": "corrected", "not_in_contract": "not_in_contract"}[action],
        "at": now, "note": (note or "").strip()[:500] or None, "original": original, "history": history[-HISTORY_CAP:],
    }
    f.notes = {**_clean_notes(f), "user_review": review}


async def review_field(
    db: AsyncSession, contract, version: ContractVersion, field: ExtractedField, action: str,
    raw_value: Any = None, quote: Optional[str] = None, note: Optional[str] = None,
) -> ExtractedField:
    fields = list((await db.execute(select(ExtractedField).where(ExtractedField.contract_version_id == version.id))).scalars())
    doc = DocIndex(pages_from_raw_text(version.raw_text))
    apply_to_field(field, fields, doc, action, raw_value, quote, note)
    await db.flush()
    await rebuild_after_review(db, contract, version, fields, doc)
    return field


def sync_version_columns(version: ContractVersion, fields: List[ExtractedField]) -> None:
    by = {f.field_key: f for f in fields}
    d = lambda k: parse_date((by[k].value or {}).get("date")) if k in by and by[k].value else None  # noqa: E731
    version.effective_date, version.expiry_date = d("effective_date"), d("expiration_date")
    for col, key in (("renewal_terms", "renewal_terms"), ("payment_terms", "payment_terms"),
                     ("termination_conditions", "termination_conditions")):
        setattr(version, col, by[key].display_value if key in by else None)


_ALERT_KEEP = ("status", "acknowledged_at", "emailed_at", "email_count", "pushed_at", "push_count")


async def _alert_state(db: AsyncSession, version_id) -> Dict[Tuple[str, date, int], Dict[str, Any]]:
    """Read/emailed state of the alerts of a version, keyed by what they are about (label, date, lead)."""
    rows = (await db.execute(
        select(Alert, Deadline).join(Deadline, Deadline.id == Alert.deadline_id).where(Deadline.contract_version_id == version_id)
    )).all()
    return {(dl.label, dl.deadline_date, a.lead_days): {k: getattr(a, k) for k in _ALERT_KEEP} for a, dl in rows}


async def _restore_alert_state(db: AsyncSession, version_id, kept: Dict[Tuple[str, date, int], Dict[str, Any]]) -> None:
    """An alert about the same thing on the same date keeps its state, so a correction elsewhere does not
    make you read (or be emailed) the same alert again."""
    if not kept:
        return
    rows = (await db.execute(
        select(Alert, Deadline).join(Deadline, Deadline.id == Alert.deadline_id).where(Deadline.contract_version_id == version_id)
    )).all()
    for a, dl in rows:
        state = kept.get((dl.label, dl.deadline_date, a.lead_days))
        if state:
            for k, v in state.items():
                setattr(a, k, v)


async def rebuild_after_review(db: AsyncSession, contract, version: ContractVersion, fields: List[ExtractedField], doc: DocIndex) -> None:
    """Deadlines, alerts and flags follow the corrected values."""
    sync_version_columns(version, fields)
    obligations = list((await db.execute(select(Obligation).where(Obligation.contract_version_id == version.id))).scalars())
    parties = list((await db.execute(select(ContractParty).where(ContractParty.contract_version_id == version.id))).scalars())
    clauses = list((await db.execute(select(Clause).where(Clause.contract_version_id == version.id))).scalars())
    kept_alerts = await _alert_state(db, version.id)
    await db.execute(delete(Deadline).where(Deadline.contract_version_id == version.id))     # alerts go with them
    await deadline_service.generate_for_version(db, contract, version, fields, obligations)
    await _restore_alert_state(db, version.id, kept_alerts)
    meta = version.extraction_meta or {}
    await flag_service.generate_flags(
        db, contract, version, fields, parties, clauses, obligations, doc,
        extraction_status=version.extraction_status, extraction_error=version.extraction_error,
        chunks_ok=meta.get("chunks_ok"), chunks_total=meta.get("chunks_total"), source=meta.get("source"),
    )
    contract.updated_at = datetime.utcnow()


# ─── keep reviews across a re-process ─────────────────────────────────────────

async def capture(db: AsyncSession, version_id) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """Reviewed fields of a version, by (field_key, item_index), before re-processing deletes them."""
    rows = (await db.execute(select(ExtractedField).where(ExtractedField.contract_version_id == version_id))).scalars()
    return {
        (f.field_key, f.item_index): {
            "value": f.value, "display_value": f.display_value, "source_quote": f.source_quote, "page": f.page,
            "section": f.section, "confidence": f.confidence, "status": f.status, "notes": f.notes,
        }
        for f in rows if is_reviewed(f)
    }


def reapply(fields: List[ExtractedField], kept: Dict[Tuple[str, int], Dict[str, Any]]) -> int:
    """Put the reviewer's values back on freshly extracted fields. The new AI result becomes the 'original'."""
    n = 0
    for f in fields:
        old = kept.get((f.field_key, f.item_index))
        if not old:
            continue
        review = dict((old["notes"] or {}).get("user_review") or {})
        review["original"] = _snapshot(f)                          # what the AI found this time (for Revert)
        f.value, f.display_value, f.source_quote, f.page, f.section = old["value"], old["display_value"], old["source_quote"], old["page"], old["section"]
        f.confidence, f.status = old["confidence"], old["status"]
        f.notes = {**{k: v for k, v in (old["notes"] or {}).items() if k != "user_review"}, "user_review": review}
        n += 1
    return n
