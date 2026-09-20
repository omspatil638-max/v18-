"""
flags.py — the "Needs human review" queue.

  GET   /flags            flags across the CURRENT version of each of the user's live contracts
  GET   /flags/summary    counts for badges
  PATCH /flags/{id}       resolve / dismiss / reopen

A contract that is soft-deleted, or a superseded version, never appears in the queue.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import Contract, ContractVersion, FlagSeverity, FlagStatus, ReviewFlag, User
from app.schemas.schemas import FlagSummary, QueueFlag, ReviewFlagResponse, ReviewFlagUpdate

router = APIRouter(prefix="/flags", tags=["Review flags"])

_SEVERITY_ORDER = case(
    (ReviewFlag.severity == FlagSeverity.HIGH, 0),
    (ReviewFlag.severity == FlagSeverity.MEDIUM, 1),
    else_=2,
)


def _live_current_flags(user: User):
    """Flags on the current version of each of the user's non-deleted contracts."""
    return (
        select(ReviewFlag, Contract.title, ContractVersion.label)
        .join(Contract, Contract.id == ReviewFlag.contract_id)
        .join(ContractVersion, ContractVersion.id == ReviewFlag.contract_version_id)
        .where(
            Contract.user_id == user.id,
            Contract.deleted_at.is_(None),
            ReviewFlag.contract_version_id == Contract.current_version_id,
        )
    )


@router.get("", response_model=List[QueueFlag])
async def list_flag_queue(
    status: str = Query("OPEN", pattern="^(OPEN|RESOLVED|DISMISSED|ALL)$"),
    severity: Optional[FlagSeverity] = None,
    contract_id: Optional[uuid.UUID] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = _live_current_flags(user)
    if status != "ALL":
        stmt = stmt.where(ReviewFlag.status == FlagStatus(status))
    if severity is not None:
        stmt = stmt.where(ReviewFlag.severity == severity)
    if contract_id is not None:
        stmt = stmt.where(ReviewFlag.contract_id == contract_id)
    stmt = stmt.order_by(_SEVERITY_ORDER, ReviewFlag.created_at.desc())

    out = []
    for flag, title, label in (await db.execute(stmt)).all():
        out.append(QueueFlag(
            **ReviewFlagResponse.model_validate(flag).model_dump(), contract_title=title, version_label=label,
        ))
    return out


@router.get("/summary", response_model=FlagSummary)
async def flag_summary(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ReviewFlag.severity, func.count())
        .join(Contract, Contract.id == ReviewFlag.contract_id)
        .where(
            Contract.user_id == user.id,
            Contract.deleted_at.is_(None),
            ReviewFlag.contract_version_id == Contract.current_version_id,
            ReviewFlag.status == FlagStatus.OPEN,
        )
        .group_by(ReviewFlag.severity)
    )).all()
    counts = {sev.value: n for sev, n in rows}
    return FlagSummary(
        open=sum(counts.values()), high=counts.get("HIGH", 0),
        medium=counts.get("MEDIUM", 0), low=counts.get("LOW", 0),
    )


@router.patch("/{flag_id}", response_model=ReviewFlagResponse)
async def update_flag(
    flag_id: uuid.UUID,
    payload: ReviewFlagUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    flag = (await db.execute(
        select(ReviewFlag)
        .join(Contract, Contract.id == ReviewFlag.contract_id)
        .where(ReviewFlag.id == flag_id, Contract.user_id == user.id, Contract.deleted_at.is_(None))
    )).scalar_one_or_none()
    if flag is None:
        raise HTTPException(status_code=404, detail="Flag not found")
    flag.status = payload.status
    flag.resolved_at = None if payload.status == FlagStatus.OPEN else datetime.utcnow()
    await db.commit()
    await db.refresh(flag)
    return flag
