"""
search.py

  GET /search?q=...        contracts, extracted values and text passages (Ctrl+K)
  GET /tags                tags in use, with counts
  GET /contract-types      contract types in use, with counts
"""

import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User
from app.services import search_service

router = APIRouter(tags=["Search"])


class ContractHit(BaseModel):
    contract_id: uuid.UUID
    title: str
    detail: Optional[str] = None
    contract_type: Optional[str] = None
    tags: List[str] = []
    expiry_date: Optional[date] = None


class FieldHit(BaseModel):
    contract_id: uuid.UUID
    title: str
    label: str
    value: str
    status: str
    page: Optional[int] = None
    quote: Optional[str] = None


class PassageHit(BaseModel):
    contract_id: uuid.UUID
    title: str
    page: Optional[int] = None
    section: Optional[str] = None
    snippet: str          # marked with « » around the matched words; plain text, never HTML
    quote: str            # the same text without markers, for the source viewer's highlight


class SearchResponse(BaseModel):
    query: str
    contracts: List[ContractHit]
    fields: List[FieldHit]
    passages: List[PassageHit]


class CountRow(BaseModel):
    name: str
    count: int


@router.get("/search", response_model=SearchResponse)
async def search(q: str = Query("", max_length=200), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await search_service.search(db, user.id, q)


@router.get("/tags", response_model=List[CountRow])
async def tags(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await search_service.tag_counts(db, user.id)


@router.get("/contract-types", response_model=List[CountRow])
async def contract_types(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await search_service.type_counts(db, user.id)
