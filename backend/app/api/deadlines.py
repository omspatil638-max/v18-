import uuid
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_owned_contract, resolve_version
from app.db.database import get_db
from app.models.models import (
    AlertSetting, Contract, ContractVersion, Deadline, DeadlineType, FieldStatus, Obligation, ObligationOccurrence,
    ObligationStatus, User,
)
from app.schemas.schemas import (
    DeadlineResponse, DeadlineWithContext, ScheduleEntry, ScheduleResponse, UndatedObligation,
)
from app.services import deadline_service
from app.services.ics_service import build_ics

router = APIRouter(prefix="", tags=["Deadlines & Timeline"])


async def _version_deadlines(contract_id: uuid.UUID, version_id: Optional[uuid.UUID], user: User, db: AsyncSession):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    stmt = select(Deadline).where(Deadline.contract_version_id == version.id).order_by(Deadline.deadline_date.asc())
    return (await db.execute(stmt)).scalars().all()


@router.get("/contracts/{contract_id}/deadlines", response_model=List[DeadlineResponse])
async def list_contract_deadlines(
    contract_id: uuid.UUID, version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """All deadlines for a contract version, ordered by date."""
    return await _version_deadlines(contract_id, version_id, user, db)


@router.get("/contracts/{contract_id}/timeline", response_model=List[DeadlineResponse])
async def get_contract_timeline(
    contract_id: uuid.UUID, version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Kept for compatibility: the flat deadline list. See /schedule for the grouped obligation timeline."""
    return await _version_deadlines(contract_id, version_id, user, db)


@router.get("/contracts/{contract_id}/schedule", response_model=ScheduleResponse)
async def get_contract_schedule(
    contract_id: uuid.UUID, version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """
    The obligation timeline: every dated item for a version, with who is responsible, how the date
    was derived, and its source. Obligations whose rule cannot be turned into a date are listed
    separately under `undated` (with the reason) rather than being dropped.
    """
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    today = date.today()

    deadlines = (await db.execute(
        select(Deadline).where(Deadline.contract_version_id == version.id).order_by(Deadline.deadline_date)
    )).scalars().all()
    obligations = {o.id: o for o in (await db.execute(
        select(Obligation).where(Obligation.contract_version_id == version.id)
        .options(selectinload(Obligation.occurrences)))).scalars().all()}

    entries: List[ScheduleEntry] = []
    for dl in deadlines:
        ob = obligations.get(dl.obligation_id) if dl.obligation_id else None
        if dl.deadline_type == DeadlineType.OBLIGATION and ob is not None:
            kind = "occurrence" if (ob.due_rule_type == "recurring" or (ob.due_rule_json or {}).get("repeats")) and \
                ob.due_date != dl.deadline_date else "obligation"
        else:
            kind = "renewal_notice" if dl.deadline_type == DeadlineType.RENEWAL_NOTICE else "contract_date"
        entries.append(ScheduleEntry(
            id=str(dl.id), kind=kind, due_on=dl.deadline_date, label=dl.label,
            party=ob.responsible_party if ob else None, action=ob.action if ob else None,
            rule_type=ob.due_rule_type if ob else None, recurrence=ob.recurrence if ob else None,
            basis=dl.basis,
            is_overdue=dl.deadline_date < today and (ob is None or ob.status != ObligationStatus.COMPLETED),
            status=ob.status.value if ob else None, verification_status=ob.verification_status if ob else None,
            obligation_id=dl.obligation_id, deadline_id=dl.id,
            source_page=dl.source_page, source_section=dl.source_section, source_quote=dl.source_quote,
        ))

    dated = {e.obligation_id for e in entries if e.obligation_id}
    undated = [
        UndatedObligation(
            obligation_id=o.id, party=o.responsible_party, action=o.action, due_rule=o.due_rule, reason=o.due_reason,
            source_page=o.source_page, source_section=o.source_section, source_quote=o.source_quote,
            verification_status=o.verification_status,
        )
        for o in sorted(obligations.values(), key=lambda o: (o.source_page or 10 ** 6, o.responsible_party))
        if o.id not in dated
    ]
    return ScheduleResponse(contract_version_id=version.id, entries=entries, undated=undated)


def _live_current_deadlines(user: User):
    return (
        select(Deadline, Contract.title, Obligation)
        .join(Contract, Contract.id == Deadline.contract_id)
        .outerjoin(Obligation, Obligation.id == Deadline.obligation_id)
        .where(
            Contract.user_id == user.id, Contract.deleted_at.is_(None),
            Deadline.contract_version_id == Contract.current_version_id,
            # A completed obligation is no longer a deadline anyone needs to be reminded of.
            or_(Obligation.id.is_(None), Obligation.status != ObligationStatus.COMPLETED),
        )
    )


def _context(dl: Deadline, title: str, ob: Optional[Obligation], today: date) -> DeadlineWithContext:
    days = (dl.deadline_date - today).days
    return DeadlineWithContext(
        **DeadlineResponse.model_validate(dl).model_dump(),
        contract_title=title, days_until=days, is_overdue=days < 0,
        responsible_party=ob.responsible_party if ob else None,
        obligation_status=ob.status if ob else None, verification_status=ob.verification_status if ob else None,
    )


@router.get("/deadlines", response_model=List[DeadlineWithContext])
async def list_all_upcoming_deadlines(
    days_ahead: int = Query(365, ge=1, le=1825, description="Deadlines within N days from today (30/60/90 on the dashboard)"),
    include_overdue: bool = Query(False, description="Also include deadlines already in the past"),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Dashboard feed: deadlines from the CURRENT version of each live contract, overdue ones flagged."""
    today = date.today()
    stmt = _live_current_deadlines(user).where(Deadline.deadline_date <= today + timedelta(days=days_ahead))
    if not include_overdue:
        stmt = stmt.where(Deadline.deadline_date >= today)
    stmt = stmt.order_by(Deadline.deadline_date.asc())
    return [_context(dl, title, ob, today) for dl, title, ob in (await db.execute(stmt)).all()]


# ─── calendar export (.ics) ───────────────────────────────────────────────────

async def _lead_days_for(db: AsyncSession, user: User) -> List[int]:
    setting = (await db.execute(select(AlertSetting).where(AlertSetting.user_id == user.id))).scalar_one_or_none()
    if setting and setting.lead_days:
        return [int(d) for d in setting.lead_days if isinstance(d, int) and d > 0]
    from app.core.config import settings
    return settings.alert_lead_days


def _ics_response(events: List[dict], name: str, leads: List[int], filename: str) -> Response:
    body = build_ics(events, name=name, lead_days=leads)
    return Response(content=body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _event(dl: Deadline, title: str) -> dict:
    where = f"Source: page {dl.source_page}" + (f", {dl.source_section}" if dl.source_section else "") if dl.source_page else ""
    desc = "\n".join(p for p in (f"Contract: {title}", dl.basis or "", where, f"“{dl.source_quote}”" if dl.source_quote else "") if p)
    return {"id": dl.id, "date": dl.deadline_date, "title": f"{title}: {dl.label}", "description": desc}


@router.get("/calendar.ics")
async def export_all_deadlines_ics(
    days_ahead: int = Query(730, ge=1, le=1825), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """All upcoming deadlines across the user's contracts as an iCalendar file."""
    today = date.today()
    stmt = _live_current_deadlines(user).where(
        Deadline.deadline_date >= today, Deadline.deadline_date <= today + timedelta(days=days_ahead)
    ).order_by(Deadline.deadline_date)
    events = [_event(dl, title) for dl, title, _ in (await db.execute(stmt)).all()]
    return _ics_response(events, "ContractLens deadlines", await _lead_days_for(db, user), "contractlens-deadlines.ics")


@router.get("/contracts/{contract_id}/calendar.ics")
async def export_contract_deadlines_ics(
    contract_id: uuid.UUID, version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    today = date.today()
    deadlines = (await db.execute(
        select(Deadline).where(Deadline.contract_version_id == version.id, Deadline.deadline_date >= today)
        .order_by(Deadline.deadline_date))).scalars().all()
    events = [_event(dl, contract.title) for dl in deadlines]
    safe = "".join(c if c.isalnum() else "-" for c in contract.title)[:40].strip("-") or "contract"
    return _ics_response(events, f"{contract.title} deadlines", await _lead_days_for(db, user), f"{safe}-deadlines.ics")
