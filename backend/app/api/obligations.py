import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_contract, resolve_version
from app.db.database import get_db
from app.models.models import Contract, Obligation, ObligationStatus, User
from app.schemas.schemas import ObligationResponse, ObligationUpdate

router = APIRouter(prefix="", tags=["Obligations"])


@router.get("/contracts/{contract_id}/obligations", response_model=List[ObligationResponse])
async def list_contract_obligations(
    contract_id: uuid.UUID,
    status: Optional[ObligationStatus] = None,
    version_id: Optional[uuid.UUID] = Query(None, description="Defaults to the current version"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    stmt = select(Obligation).where(Obligation.contract_version_id == version.id)
    if status:
        stmt = stmt.where(Obligation.status == status)
    stmt = stmt.order_by(Obligation.due_date.asc().nulls_last(), Obligation.source_page.asc().nulls_last())
    return (await db.execute(stmt)).scalars().all()


@router.patch("/obligations/{obligation_id}", response_model=ObligationResponse)
async def update_obligation_status(
    obligation_id: uuid.UUID,
    payload: ObligationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    obligation = (await db.execute(
        select(Obligation)
        .join(Contract, Contract.id == Obligation.contract_id)
        .where(Obligation.id == obligation_id, Contract.user_id == user.id, Contract.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")

    if payload.status:
        obligation.status = payload.status
    await db.commit()
    await db.refresh(obligation)
    return obligation
