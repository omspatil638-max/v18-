"""clauses.py — API router for contract clause retrieval."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_contract, resolve_version
from app.db.database import get_db
from app.models.models import Clause, ClauseType, User
from app.schemas.schemas import ClauseResponse

router = APIRouter(prefix="", tags=["Clauses"])


@router.get("/contracts/{contract_id}/clauses", response_model=List[ClauseResponse])
async def list_contract_clauses(
    contract_id: uuid.UUID,
    clause_type: Optional[ClauseType] = None,
    version_id: Optional[uuid.UUID] = Query(None, description="Defaults to the current version"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    stmt = select(Clause).where(Clause.contract_version_id == version.id)
    if clause_type:
        stmt = stmt.where(Clause.clause_type == clause_type)
    stmt = stmt.order_by(Clause.source_page.asc().nulls_last())
    return (await db.execute(stmt)).scalars().all()


@router.get("/contracts/{contract_id}/clauses/{clause_id}", response_model=ClauseResponse)
async def get_clause(
    contract_id: uuid.UUID,
    clause_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_contract(contract_id, user, db)
    clause = (await db.execute(
        select(Clause).where(Clause.id == clause_id, Clause.contract_id == contract_id)
    )).scalar_one_or_none()
    if not clause:
        raise HTTPException(status_code=404, detail="Clause not found")
    return clause
