"""
review.py: a person reviews an extracted field.

  POST /contracts/{contract_id}/fields/{field_id}/review
       {"action": "confirm" | "correct" | "not_in_contract" | "revert", "value": ..., "quote": ..., "note": ...}

See services/review_service.py for the rules (validated in code, quote must exist, original kept).
"""

import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_contract
from app.db.database import get_db
from app.models.models import ContractStatus, ContractVersion, ExtractedField, User
from app.schemas.schemas import ExtractedFieldResponse
from app.services import review_service

router = APIRouter(prefix="/contracts/{contract_id}/fields", tags=["Review"])


class ReviewRequest(BaseModel):
    action: Literal["confirm", "correct", "not_in_contract", "revert"]
    value: Optional[Any] = None
    quote: Optional[str] = Field(default=None, max_length=3000)
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/{field_id}/review", response_model=ExtractedFieldResponse)
async def review_field(
    contract_id: uuid.UUID, field_id: uuid.UUID, payload: ReviewRequest,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    field = (await db.execute(
        select(ExtractedField).where(ExtractedField.id == field_id, ExtractedField.contract_id == contract.id)
    )).scalar_one_or_none()
    if field is None:
        raise HTTPException(status_code=404, detail="Field not found")
    version = (await db.execute(select(ContractVersion).where(ContractVersion.id == field.contract_version_id))).scalar_one()
    if version.status != ContractStatus.READY:
        raise HTTPException(status_code=409, detail="This version is still being processed. Try again when it is ready.")
    try:
        await review_service.review_field(db, contract, version, field, payload.action, payload.value, payload.quote, payload.note)
    except review_service.ReviewError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    await db.refresh(field)
    return field
