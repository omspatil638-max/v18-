"""
renewals.py: GET /renewals, the renewal radar (what ends or renews soon, and by when to act).
"""

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User
from app.services import renewal_service

router = APIRouter(tags=["Renewals"])


class Source(BaseModel):
    status: str
    page: Optional[int] = None
    section: Optional[str] = None
    quote: Optional[str] = None


class Sources(BaseModel):
    expiry: Optional[Source] = None
    auto_renew: Optional[Source] = None
    notice: Optional[Source] = None


class RadarItem(BaseModel):
    contract_id: uuid.UUID
    title: str
    counterparty: Optional[str] = None
    contract_type: Optional[str] = None
    tags: List[str] = []
    kind: str                       # give_notice_by | expires | notice_missed | expired
    action_date: date
    days_to_action: int
    bucket: str                     # 30 | 60 | 90 | later | overdue | notice_missed | expired
    expiry_date: date
    days_to_expiry: int
    auto_renew: Optional[bool] = None
    notice_period: Optional[str] = None
    notice_deadline: Optional[date] = None
    notice_basis: Optional[str] = None
    what_happens: str
    amounts: List[str] = []         # exactly as written in the contract; never summed
    sources: Sources
    unverified: List[str] = []
    open_flags: int = 0


class NotTracked(BaseModel):
    contract_id: uuid.UUID
    title: str
    reason: str


class RadarSummary(BaseModel):
    due_30: int
    due_60: int
    due_90: int
    auto_renewing: int
    notice_missed: int
    expired_recently: int


class RadarResponse(BaseModel):
    generated_at: datetime
    window_days: int
    summary: RadarSummary
    items: List[RadarItem]
    not_tracked: List[NotTracked]


@router.get("/renewals", response_model=RadarResponse)
async def renewals(
    window: int = Query(renewal_service.DEFAULT_WINDOW, ge=7, le=1825, description="Show actions due within N days"),
    expired_days: int = Query(30, ge=0, le=365, description="Also show contracts that ended within N days"),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    return await renewal_service.build_radar(db, user.id, window, expired_days)
