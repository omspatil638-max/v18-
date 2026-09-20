"""
quality.py: GET /data-quality, the to-do list of missing, unverified or unreadable data.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User
from app.services import quality_service

router = APIRouter(tags=["Data quality"])


class IssueContract(BaseModel):
    contract_id: uuid.UUID
    title: str
    detail: str
    field_key: Optional[str] = None


class Issue(BaseModel):
    code: str
    severity: str
    title: str
    why: str
    action: str
    count: int
    contracts: List[IssueContract]


class CoverageRow(BaseModel):
    field_key: str
    label: str
    have: int
    total: int


class QualityReport(BaseModel):
    generated_at: datetime
    contracts_total: int
    processing: int
    analysed: int
    open_issues: int
    coverage: List[CoverageRow]
    issues: List[Issue]


@router.get("/data-quality", response_model=QualityReport)
async def data_quality(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await quality_service.build_report(db, user.id)
