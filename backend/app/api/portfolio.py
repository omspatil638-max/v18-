"""
portfolio.py: POST /portfolio/ask, questions across all of a user's contracts.

The answer is rows computed in code from stored, source-linked values (see services/portfolio_service.py).
"""

import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User
from app.services import portfolio_service

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class Fact(BaseModel):
    label: str
    value: str
    status: str              # verified | needs_review | flag | user | party | not_found
    page: Optional[int] = None
    quote: Optional[str] = None
    reviewed: bool = False


class Match(BaseModel):
    contract_id: uuid.UUID
    title: str
    counterparty: Optional[str] = None
    contract_type: Optional[str] = None
    tags: List[str] = []
    facts: List[Fact]


class Passage(BaseModel):
    contract_id: uuid.UUID
    title: str
    page: Optional[int] = None
    section: Optional[str] = None
    snippet: str
    quote: str


class AskResponse(BaseModel):
    question: str
    status: str              # answered | no_match | passages | not_understood | unavailable_ai
    interpreted_by: str      # rules | ai | keyword | none
    interpretation: List[str]
    summary: str
    total_contracts: int
    matches: List[Match]
    passages: List[Passage]
    examples: List[str]


@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await portfolio_service.ask(db, user.id, payload.question)
