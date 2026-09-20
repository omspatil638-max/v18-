"""
deadline_service.py
-------------------
Builds Deadline, ObligationOccurrence and Alert rows for a version. Dates come from exactly
three places, and every one is either stated in the document or computed in code:

  1. dates stated in the contract         expiration date, explicit obligation dates
  2. computed from a stated rule          "30 days after the effective date", "quarterly"
  3. the actionable renewal deadline      expiration date minus the notice period

Nothing is scraped from raw text and nothing is guessed: when a rule depends on something the
contract does not give (an invoice date, a missing effective date) the obligation keeps its rule
text and records WHY no date exists (due_rule_json["reason"]).
"""

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import (
    Alert, AlertSetting, Contract, ContractVersion, Deadline, DeadlineType, ExtractedField,
    FieldStatus, Obligation, ObligationOccurrence,
)
from app.services import schedule_service as sched
from app.services.validators import parse_date

logger = logging.getLogger(__name__)

DEADLINE_WINDOW_PAST_DAYS = 30
DEADLINE_WINDOW_FUTURE_DAYS = 400
MAX_DEADLINES_PER_OBLIGATION = 24


def _field_date(f: Optional[ExtractedField]) -> Optional[date]:
    if f is None or not f.value or f.status == FieldStatus.EXTRACTION_UNAVAILABLE.value:
        return None
    return parse_date(f.value.get("date"))


async def _lead_days(db: AsyncSession, user_id) -> List[int]:
    setting = (await db.execute(select(AlertSetting).where(AlertSetting.user_id == user_id))).scalar_one_or_none()
    if setting and setting.lead_days:
        days = sorted({int(d) for d in setting.lead_days if isinstance(d, int) and d > 0}, reverse=True)
        if days:
            return days
    return settings.alert_lead_days


def alerts_for(deadline: Deadline, user_id, contract_id, lead_days: List[int], today: date) -> List[Alert]:
    """One alert per configured lead time that is still ahead; if the deadline is nearer than every
    lead time, a single alert for today so it is not silently missed. Past deadlines get none."""
    if deadline.deadline_date < today:
        return []
    out = [
        Alert(
            user_id=user_id, contract_id=contract_id, deadline_id=deadline.id, lead_days=lead,
            fire_on=deadline.deadline_date - timedelta(days=lead),
            message=f"{lead}-day alert: '{deadline.label}' is due on {deadline.deadline_date.isoformat()}.",
        )
        for lead in lead_days
        if deadline.deadline_date - timedelta(days=lead) >= today
    ]
    if not out:
        days = (deadline.deadline_date - today).days
        out.append(Alert(
            user_id=user_id, contract_id=contract_id, deadline_id=deadline.id, lead_days=days, fire_on=today,
            message=f"Approaching: '{deadline.label}' is due on {deadline.deadline_date.isoformat()} ({days} days).",
        ))
    return out


async def generate_for_version(
    db: AsyncSession,
    contract: Contract,
    version: ContractVersion,
    fields: List[ExtractedField],
    obligations: List[Obligation],
    today: Optional[date] = None,
) -> int:
    today = today or date.today()
    by_key = {f.field_key: f for f in fields}
    lead_days = await _lead_days(db, contract.user_id)
    anchors = sched.Anchors(effective=_field_date(by_key.get("effective_date")),
                            expiry=_field_date(by_key.get("expiration_date")))
    created = 0

    # A rebuild must not leave alerts behind for deadlines that no longer exist.
    await db.execute(delete(Alert).where(
        Alert.deadline_id.in_(select(Deadline.id).where(Deadline.contract_version_id == version.id))
    ))

    async def add(label, when, dtype, quote, page, section, basis, obligation_id=None) -> None:
        nonlocal created
        dl = Deadline(
            contract_id=contract.id, contract_version_id=version.id, obligation_id=obligation_id,
            label=str(label)[:255], deadline_date=when, deadline_type=dtype,
            source_quote=quote, source_page=page, source_section=section, basis=basis,
        )
        db.add(dl)
        await db.flush()
        for alert in alerts_for(dl, contract.user_id, contract.id, lead_days, today):
            db.add(alert)
        created += 1

    def unverified(status: str) -> str:
        return "" if status == FieldStatus.VERIFIED.value else " (unverified: needs review)"

    # 1. contract expiration -----------------------------------------------------------------
    exp = by_key.get("expiration_date")
    if anchors.expiry and exp:
        await add(f"Contract expires — {contract.title}", anchors.expiry, DeadlineType.EXPIRY,
                  exp.source_quote, exp.page, exp.section,
                  f"Expiration date stated in the contract{unverified(exp.status)}.")

    # 2. renewal: the actionable deadline = expiration minus notice period -------------------
    notice = by_key.get("renewal_notice_period")
    if notice and notice.value and notice.status != FieldStatus.EXTRACTION_UNAVAILABLE.value:
        due, basis = sched.renewal_notice_date(anchors.expiry, notice.value)
        if due is not None:
            auto = (by_key.get("auto_renew").value or {}).get("bool") if by_key.get("auto_renew") and by_key["auto_renew"].value else None
            consequence = (" The contract renews automatically unless notice is given by this date." if auto is True
                           else " This is the last day to give notice about renewal.")
            await add(f"Renewal notice deadline — {contract.title}", due, DeadlineType.RENEWAL_NOTICE,
                      notice.source_quote, notice.page, notice.section,
                      f"{basis}.{consequence}{unverified(notice.status)}")

    # 3. obligations: stated, computed, or recurring -----------------------------------------
    for ob in obligations:
        label = f"{ob.responsible_party}: {ob.action}"
        rule = dict(ob.due_rule_json or {})
        flag = unverified(ob.verification_status)
        window_lo, window_hi = today - timedelta(days=DEADLINE_WINDOW_PAST_DAYS), today + timedelta(days=DEADLINE_WINDOW_FUTURE_DAYS)

        async def occurrence_deadlines(occ: sched.Occurrences, skip: Optional[date] = None) -> None:
            n = 0
            for when, basis in occ.dates:
                db.add(ObligationOccurrence(obligation_id=ob.id, due_date=when, basis=basis))
                if when == skip or not (window_lo <= when <= window_hi) or n >= MAX_DEADLINES_PER_OBLIGATION:
                    continue
                n += 1
                await add(label, when, DeadlineType.OBLIGATION, ob.source_quote, ob.source_page, ob.source_section,
                          f"Recurring ({ob.recurrence or rule.get('recurrence', 'repeats')}): {basis}{flag}", ob.id)

        if ob.due_rule_type == "fixed" and ob.due_date:
            rule.update({"computed": False, "basis": "Date stated in the contract"})
            await add(label, ob.due_date, DeadlineType.OBLIGATION, ob.source_quote, ob.source_page, ob.source_section,
                      f"Due date stated in the contract for this obligation{flag}.", ob.id)
            if rule.get("recurrence_hint"):     # "an ANNUAL report by March 31, 2027": repeats from that date
                occ = sched.generate_occurrences({"recurrence": rule["recurrence_hint"]}, ob.due_rule, anchors, today,
                                                 first_fixed=ob.due_date)
                await occurrence_deadlines(occ, skip=ob.due_date)
                rule["repeats"] = rule["recurrence_hint"]

        elif ob.due_rule_type == "relative":
            due, basis = sched.compute_relative(rule, anchors)
            if due is not None:
                ob.due_date = due
                rule.update({"computed": True, "basis": basis})
                await add(label, due, DeadlineType.OBLIGATION, ob.source_quote, ob.source_page, ob.source_section,
                          f"Computed: {basis}{flag}.", ob.id)
            else:
                rule.update({"computed": False, "reason": basis})

        elif ob.due_rule_type == "recurring":
            occ = sched.generate_occurrences(rule, ob.due_rule, anchors, today)
            if occ.dates:
                upcoming = [d for d, _ in occ.dates if d >= today]
                ob.due_date = upcoming[0] if upcoming else occ.dates[-1][0]
                rule.update({"computed": True, "basis": f"Next of {len(occ.dates)} scheduled occurrences",
                             "date_is": "next_occurrence"})
                await occurrence_deadlines(occ)
            else:
                rule.update({"computed": False, "reason": occ.reason})

        ob.due_rule_json = rule or None

    logger.info("Generated %d deadlines for version %s", created, str(version.id)[:8])
    return created


async def regenerate_alerts_for_user(db: AsyncSession, user_id, today: Optional[date] = None) -> int:
    """
    Apply a changed set of lead times: rebuild the PENDING alerts of every live contract's current
    deadlines. Alerts a person already acknowledged, or that were already emailed, are kept as-is.
    """
    today = today or date.today()
    lead_days = await _lead_days(db, user_id)

    await db.execute(delete(Alert).where(Alert.user_id == user_id, Alert.status == "PENDING"))

    rows = (await db.execute(
        select(Deadline)
        .join(Contract, Contract.id == Deadline.contract_id)
        .where(Contract.user_id == user_id, Contract.deleted_at.is_(None),
               Deadline.contract_version_id == Contract.current_version_id, Deadline.deadline_date >= today)
    )).scalars().all()

    existing = {(a.deadline_id, a.lead_days) for a in (await db.execute(
        select(Alert).where(Alert.user_id == user_id))).scalars().all()}
    made = 0
    for dl in rows:
        for alert in alerts_for(dl, user_id, dl.contract_id, lead_days, today):
            if (alert.deadline_id, alert.lead_days) not in existing:
                db.add(alert)
                made += 1
    return made
