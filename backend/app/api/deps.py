"""
deps.py — request-scoped identity, ownership and version resolution.

Every route that touches a contract (or anything under it) goes through
get_owned_contract. A contract that exists but belongs to someone else, or has been
soft-deleted, is reported as 404 so its existence is not disclosed.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import hash_token
from app.db.database import get_db
from app.db.seed import seed_demo_user
from app.services.account_service import auth_state
from app.models.models import Contract, ContractVersion, User, UserSession

SESSION_COOKIE = "cl_session"


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    if await auth_state(db) in ("demo", "setup"):        # setup: nobody has signed up yet, the local data still works
        return await seed_demo_user(db)

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    row = (await db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token))
    )).first()
    if row is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    session, user = row
    if session.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired.")
    return user


async def get_owned_contract(
    contract_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    *options,
    include_deleted: bool = False,
) -> Contract:
    stmt = select(Contract).where(Contract.id == contract_id, Contract.user_id == user.id)
    if not include_deleted:
        stmt = stmt.where(Contract.deleted_at.is_(None))
    if options:
        stmt = stmt.options(*options)
    contract: Optional[Contract] = (await db.execute(stmt)).scalar_one_or_none()
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


async def resolve_version(
    db: AsyncSession,
    contract: Contract,
    version_id: Optional[uuid.UUID] = None,
) -> ContractVersion:
    """
    The version a request refers to: an explicit ?version_id=, else the current one.
    An id belonging to a different contract is rejected (404), not silently ignored.
    """
    if version_id is not None:
        version = (await db.execute(
            select(ContractVersion).where(
                ContractVersion.id == version_id, ContractVersion.contract_id == contract.id
            )
        )).scalar_one_or_none()
        if version is None:
            raise HTTPException(status_code=404, detail="Version not found for this contract")
        return version

    if contract.current_version_id is not None:
        version = await db.get(ContractVersion, contract.current_version_id)
        if version is not None:
            return version

    version = (await db.execute(
        select(ContractVersion)
        .where(ContractVersion.contract_id == contract.id)
        .order_by(ContractVersion.version_number.desc())
        .limit(1)
    )).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="This contract has no versions")
    return version


async def owned_contract_and_version(
    contract_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    version_id: Optional[uuid.UUID] = None,
    *options,
) -> Tuple[Contract, ContractVersion]:
    contract = await get_owned_contract(contract_id, user, db, *options)
    return contract, await resolve_version(db, contract, version_id)
