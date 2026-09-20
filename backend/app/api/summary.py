"""
summary.py — the one-page stakeholder summary.

  GET  /contracts/{id}/summary      structured summary (built fresh from verified data)
  GET  /contracts/{id}/summary.md   the same as a downloadable Markdown file
  POST /contracts/{id}/summary      (re)write the optional AI overview in the background
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_contract, resolve_version
from app.db.database import get_db
from app.models.models import User
from app.services import job_runner, summary_service as ss

router = APIRouter(prefix="/contracts/{contract_id}", tags=["Summary"])


class SummaryLine(BaseModel):
    text: str
    status: str
    page: Optional[int] = None
    section: Optional[str] = None
    quote: Optional[str] = None
    kind: str
    ref_id: Optional[str] = None
    severity: Optional[str] = None


class SummarySection(BaseModel):
    key: str
    title: str
    lines: List[SummaryLine]


class SummaryResponse(BaseModel):
    contract_id: uuid.UUID
    contract_version_id: uuid.UUID
    title: str
    version_label: str
    status: str                       # ready | partial | unavailable
    overview: Optional[str] = None
    overview_status: str              # ai | unavailable
    notes: List[str] = []
    sections: List[SummarySection]
    word_count: int
    disclaimer: str
    generated_at: datetime


def _notes(version) -> List[str]:
    if version.extraction_status == "unavailable":
        return ["AI extraction did not run for this version, so this summary has no extracted terms. "
                "It lists only what was found by the automatic checks."]
    if version.extraction_status == "partial":
        return ["Only part of this document could be analysed, so this summary may be incomplete."]
    if version.extraction_status == "legacy":
        return ["This version was analysed by an older version of ContractLens and is not fully verified. Re-run extraction."]
    if version.extraction_status == "unsupported":
        return ["This document has no readable text layer, so no summary can be produced."]
    return []


async def _build(db: AsyncSession, contract, version):
    sections = await ss.load_sections(db, version)
    overview = await ss.stored_overview(db, version, sections)
    return sections, overview


@router.get("/summary", response_model=SummaryResponse)
async def get_summary(
    contract_id: uuid.UUID, version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    sections, overview = await _build(db, contract, version)
    return SummaryResponse(
        contract_id=contract.id, contract_version_id=version.id, title=contract.title, version_label=version.label,
        status=ss.overall_status(version), overview=overview, overview_status="ai" if overview else "unavailable",
        notes=_notes(version), sections=sections, word_count=ss.word_count(sections, overview),
        disclaimer=ss.DISCLAIMER, generated_at=datetime.utcnow(),
    )


@router.get("/summary.md")
async def get_summary_markdown(
    contract_id: uuid.UUID, version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    sections, overview = await _build(db, contract, version)
    body = ss.to_markdown(contract.title, version.label, sections, overview, _notes(version))
    safe = "".join(c if c.isalnum() else "-" for c in contract.title)[:40].strip("-") or "contract"
    return Response(content=body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}-summary.md"'})


@router.post("/summary", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_summary_overview(
    contract_id: uuid.UUID, version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Ask for the AI overview to be (re)written. The structured summary itself is always live."""
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    job_runner.enqueue_overview(contract.id, version.id)
    return {"message": "The overview is being written. Reload the summary in a moment."}
