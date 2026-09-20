"""
pages.py — the click-to-source PDF viewer.

  GET /contracts/{id}/pages/{page}/image?scale=1.5[&version_id=]      the page as a PNG
  GET /contracts/{id}/pages/{page}/highlights?q=<quote>[&version_id=]  where the quote sits, normalised 0..1

Owner-only, like every other route. The page image is produced on the fly from the original PDF.
"""

import asyncio
import uuid
from typing import List, Optional

import pymupdf
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_owned_contract, resolve_version
from app.db.database import get_db
from app.models.models import Contract, User
from app.services import job_runner, pdf_highlight_service as hl

router = APIRouter(prefix="/contracts/{contract_id}/pages", tags=["PDF viewer"])


class HighlightRect(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class HighlightResponse(BaseModel):
    page: int
    page_count: int
    width: float                 # page size in PDF points
    height: float
    matched: bool
    rects: List[HighlightRect] = []


async def _open_pdf(db: AsyncSession, contract: Contract, version_id: Optional[uuid.UUID]):
    version = await resolve_version(db, contract, version_id)
    contract_docs = contract.documents
    doc = next((d for d in contract_docs if d.version_id == version.id), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        path = job_runner.storage_file_path(doc.storage_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return path


def _check_page(pdf: "pymupdf.Document", page: int) -> None:
    count = len(pdf)
    if page < 1 or page > count:
        pdf.close()
        raise HTTPException(status_code=404, detail=f"Page {page} does not exist (this document has {count} pages).")


@router.get("/{page}/image")
async def page_image(
    contract_id: uuid.UUID, page: int,
    scale: float = Query(1.5, ge=0.5, le=3.0),
    version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db, selectinload(Contract.documents))
    path = await _open_pdf(db, contract, version_id)

    def work() -> bytes:
        pdf = pymupdf.open(str(path))
        try:
            _check_page(pdf, page)
            return hl.render_png(pdf.load_page(page - 1), scale)
        finally:
            if not pdf.is_closed:
                pdf.close()

    png = await asyncio.to_thread(work)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{page}/highlights", response_model=HighlightResponse)
async def page_highlights(
    contract_id: uuid.UUID, page: int,
    q: str = Query("", max_length=2000, description="The quote to locate on this page"),
    version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db, selectinload(Contract.documents))
    path = await _open_pdf(db, contract, version_id)

    def work() -> HighlightResponse:
        pdf = pymupdf.open(str(path))
        try:
            _check_page(pdf, page)
            pg = pdf.load_page(page - 1)
            w, h = hl.page_size(pg)
            rects = hl.find_quote_rects(pg, q)
            return HighlightResponse(
                page=page, page_count=len(pdf), width=w, height=h, matched=bool(rects),
                rects=[HighlightRect(**r.as_dict()) for r in rects],
            )
        finally:
            if not pdf.is_closed:
                pdf.close()

    return await asyncio.to_thread(work)
