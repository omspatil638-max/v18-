"""
compare.py — version comparison.

  GET  /contracts/{id}/compare            persisted changes between two versions (+ summary)
  GET  /contracts/{id}/compare/sections   aligned old/new sections with word-level diffs (side-by-side view)
  POST /contracts/{id}/compare            (re)run the comparison in the background
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import serializers
from app.api.deps import get_current_user, get_owned_contract
from app.db.database import get_db
from app.models.models import ChangeType, Contract, ContractStatus, ContractVersion, User, VersionChange
from app.schemas.schemas import (
    AlignedSection, AlignedSections, AlignedSide, ComparisonResponse, ComparisonSummary, DiffSegment,
    VersionChangeResponse,
)
from app.services import compare_service, job_runner
from app.services.text_index import DocIndex, pages_from_raw_text

router = APIRouter(prefix="/contracts/{contract_id}/compare", tags=["Version comparison"])


async def _pick(db: AsyncSession, contract: Contract, from_id: Optional[uuid.UUID], to_id: Optional[uuid.UUID]):
    """Default: the current version, compared with the version before it."""
    versions = (await db.execute(
        select(ContractVersion).where(ContractVersion.contract_id == contract.id)
        .order_by(ContractVersion.version_number))).scalars().all()
    by_id = {v.id: v for v in versions}
    if to_id:
        new = by_id.get(to_id)
    else:
        new = by_id.get(contract.current_version_id) or (versions[-1] if versions else None)
    if new is None:
        raise HTTPException(status_code=404, detail="Version not found for this contract")
    if from_id:
        old = by_id.get(from_id)
        if old is None:
            raise HTTPException(status_code=404, detail="Version not found for this contract")
    else:
        earlier = [v for v in versions if v.version_number < new.version_number]
        if not earlier:
            raise HTTPException(status_code=422, detail="There is no earlier version to compare with. Upload a new version first.")
        old = earlier[-1]
    if old.id == new.id:
        raise HTTPException(status_code=422, detail="Choose two different versions to compare.")
    return old, new


def _summary(changes) -> ComparisonSummary:
    s = ComparisonSummary()
    for c in changes:
        if c.change_type == ChangeType.ADDED:
            s.added += 1
        elif c.change_type == ChangeType.REMOVED:
            s.removed += 1
        elif c.change_type == ChangeType.MODIFIED:
            s.modified += 1
        else:
            s.unchanged += 1
        if c.is_material and c.change_type != ChangeType.UNCHANGED:
            s.material += 1
            if c.category != "section":
                s.key_changes += 1
    return s


def _order(c: VersionChange) -> tuple:
    """Key-term changes first, then clause changes; material before minor; unchanged last."""
    if c.change_type == ChangeType.UNCHANGED:
        return (2, 1)
    return (0 if c.category != "section" else 1, 0 if c.is_material else 1)


def _ready(*versions) -> bool:
    return all(v.status == ContractStatus.READY and v.raw_text for v in versions)


@router.get("", response_model=ComparisonResponse)
async def get_comparison(
    contract_id: uuid.UUID,
    from_version_id: Optional[uuid.UUID] = Query(None),
    to_version_id: Optional[uuid.UUID] = Query(None),
    include_unchanged: bool = Query(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db, selectinload(Contract.documents))
    old, new = await _pick(db, contract, from_version_id, to_version_id)
    base = dict(
        contract_id=contract.id,
        from_version=serializers.version_summary(old, serializers.main_filename(contract.documents, old.id)),
        to_version=serializers.version_summary(new, serializers.main_filename(contract.documents, new.id)),
    )
    if not _ready(old, new):
        return ComparisonResponse(**base, status="versions_not_ready",
                                  notes=["Both versions must finish processing before they can be compared."])

    rows = (await db.execute(select(VersionChange).where(
        VersionChange.from_version_id == old.id, VersionChange.to_version_id == new.id))).scalars().all()
    meta = (new.extraction_meta or {}).get("comparison") or {}
    same_pair = meta.get("from_version_id") == str(old.id)
    running = same_pair and meta.get("status") == "running"
    if not rows:
        if not running:
            # First look at this pair (for example a pinned v1 vs v3): start the comparison rather than
            # showing an empty page that needs a manual button. The client polls until it is ready.
            meta_out = dict(new.extraction_meta or {})
            meta_out["comparison"] = {"status": "running", "from_version_id": str(old.id)}
            new.extraction_meta = meta_out
            await db.commit()
            job_runner.enqueue_comparison(contract.id, old.id, new.id)
        return ComparisonResponse(**base, status="running", notes=[])

    shown = [c for c in rows if include_unchanged or c.change_type != ChangeType.UNCHANGED]
    shown.sort(key=_order)
    return ComparisonResponse(
        **base, status="running" if running else "ready",
        impact_status=meta.get("impact_status", "none") if same_pair else "none",
        notes=meta.get("notes", []) if same_pair else [], summary=_summary(rows),
        changes=[VersionChangeResponse.model_validate(c) for c in shown],
    )


@router.get("/sections", response_model=AlignedSections)
async def get_aligned_sections(
    contract_id: uuid.UUID,
    from_version_id: Optional[uuid.UUID] = Query(None),
    to_version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Old and new sections side by side, in document order, with a word-level diff for modified ones."""
    contract = await get_owned_contract(contract_id, user, db)
    old, new = await _pick(db, contract, from_version_id, to_version_id)
    if not (old.raw_text and new.raw_text):
        raise HTTPException(status_code=409, detail="Both versions must finish processing first.")
    old_doc = DocIndex(pages_from_raw_text(old.raw_text))
    new_doc = DocIndex(pages_from_raw_text(new.raw_text))

    def side(sec) -> Optional[AlignedSide]:
        if sec is None:
            return None
        return AlignedSide(label=sec.label, page=sec.page_start, text=sec.text.strip()[: compare_service.MAX_SECTION_TEXT])

    out = []
    for m in compare_service.match_sections(old_doc.sections, new_doc.sections):
        ref = m.new or m.old
        topic = bool(compare_service.MATERIAL_TOPICS.search(f"{ref.heading} {ref.text[:400]}"))
        diff = ([DiffSegment(**s) for s in compare_service.word_diff(m.old.text.strip(), m.new.text.strip())]
                if m.kind == ChangeType.MODIFIED else [])
        out.append(AlignedSection(
            change_type=m.kind, old=side(m.old), new=side(m.new), similarity=round(m.similarity, 3),
            diff=diff, is_material=topic and m.kind != ChangeType.UNCHANGED))
    return AlignedSections(from_version_id=old.id, to_version_id=new.id, sections=out)


@router.post("", response_model=ComparisonResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_comparison(
    contract_id: uuid.UUID,
    from_version_id: Optional[uuid.UUID] = Query(None),
    to_version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recompute the comparison (after re-processing a version, or once an LLM is configured)."""
    contract = await get_owned_contract(contract_id, user, db, selectinload(Contract.documents))
    old, new = await _pick(db, contract, from_version_id, to_version_id)
    if not _ready(old, new):
        raise HTTPException(status_code=409, detail="Both versions must finish processing before they can be compared.")
    meta = dict(new.extraction_meta or {})
    meta["comparison"] = {**(meta.get("comparison") or {}), "status": "running", "from_version_id": str(old.id)}
    new.extraction_meta = meta
    await db.commit()
    job_runner.enqueue_comparison(contract.id, old.id, new.id)
    return ComparisonResponse(
        contract_id=contract.id,
        from_version=serializers.version_summary(old, serializers.main_filename(contract.documents, old.id)),
        to_version=serializers.version_summary(new, serializers.main_filename(contract.documents, new.id)),
        status="running")
