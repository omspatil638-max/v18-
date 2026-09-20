import asyncio
import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import serializers
from app.api.deps import get_current_user, get_owned_contract, resolve_version
from app.core.config import settings
from app.db.database import get_db
from app.models.models import (
    Contract, ContractStatus, ContractVersion, Document, DocumentKind, ExtractionStatus, FlagStatus,
    ReviewFlag, User,
)
from app.schemas.schemas import (
    ContractDetail, ContractStatusResponse, ContractSummary, ContractText, ContractUpdate,
    DocumentResponse, ExtractedFieldResponse, PageText, ReviewFlagResponse, VersionSummary,
)
from app.services import docx_service, job_runner
from app.services.text_index import pages_from_raw_text
from app.services.upload_validation import (
    detect_kind, read_upload_limited, sanitize_display_filename, title_from_display_name,
)

router = APIRouter(prefix="/contracts", tags=["Contracts"])

VERSION_LOADERS = (
    selectinload(ContractVersion.parties),
    selectinload(ContractVersion.clauses),
    selectinload(ContractVersion.obligations),
    selectinload(ContractVersion.deadlines),
    selectinload(ContractVersion.fields),
    selectinload(ContractVersion.flags),
)


ORIGINAL_DOCX_SUFFIX = ".original.docx"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _store_pdf(file: UploadFile) -> tuple:
    """Validate an upload and write it under a server-generated key. Returns (key, name, bytes, sha)."""
    display_name = sanitize_display_filename(file.filename)
    data = await read_upload_limited(file, settings.max_upload_bytes)
    kind = detect_kind(data, display_name)
    stem = str(uuid.uuid4())                    # user input never reaches a filesystem path
    storage_key = f"{stem}.pdf"
    pdf_bytes = data
    if kind == "docx":
        # A Word file has no pages: it is converted to a PDF for the pipeline and the viewer; the original is kept.
        try:
            pdf_bytes = (await asyncio.to_thread(docx_service.convert, data)).pdf
        except docx_service.DocxError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        await asyncio.to_thread((settings.absolute_storage_path / f"{stem}{ORIGINAL_DOCX_SUFFIX}").write_bytes, data)
    await asyncio.to_thread((settings.absolute_storage_path / storage_key).write_bytes, pdf_bytes)
    return storage_key, display_name, len(data), hashlib.sha256(data).hexdigest()


async def _load_full(db: AsyncSession, contract_id: uuid.UUID, user: User, version_id: Optional[uuid.UUID]):
    contract = await get_owned_contract(
        contract_id, user, db, selectinload(Contract.documents), selectinload(Contract.versions),
    )
    version = await resolve_version(db, contract, version_id)
    version = (await db.execute(
        select(ContractVersion).where(ContractVersion.id == version.id).options(*VERSION_LOADERS)
    )).scalar_one()
    return contract, version


MAX_TAG_LEN = 30


def normalize_tags(tags: List[str]) -> List[str]:
    """Trim, collapse spaces, drop empties and case-insensitive duplicates (the first spelling wins)."""
    out: List[str] = []
    seen = set()
    for t in tags:
        t = " ".join(str(t).split())[:MAX_TAG_LEN]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


@router.get("", response_model=List[ContractSummary])
async def list_contracts(
    include_deleted: bool = Query(False, description="Include soft-deleted contracts"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Contract)
        .where(Contract.user_id == user.id)
        .options(selectinload(Contract.documents), selectinload(Contract.versions))
        .order_by(Contract.created_at.desc())
    )
    if not include_deleted:
        stmt = stmt.where(Contract.deleted_at.is_(None))
    contracts = (await db.execute(stmt)).scalars().all()

    current_ids = [c.current_version_id for c in contracts if c.current_version_id]
    parties_by_version = {}
    flag_counts: dict = {}
    if current_ids:
        for vid, sev, n in (await db.execute(
            select(ReviewFlag.contract_version_id, ReviewFlag.severity, func.count())
            .where(ReviewFlag.contract_version_id.in_(current_ids), ReviewFlag.status == FlagStatus.OPEN)
            .group_by(ReviewFlag.contract_version_id, ReviewFlag.severity)
        )).all():
            total, high = flag_counts.get(vid, (0, 0))
            flag_counts[vid] = (total + n, high + (n if sev.value == "HIGH" else 0))
        versions = (await db.execute(
            select(ContractVersion)
            .where(ContractVersion.id.in_(current_ids))
            .options(selectinload(ContractVersion.parties))
        )).scalars().all()
        parties_by_version = {v.id: v.parties for v in versions}

    out = []
    for c in contracts:
        if not c.versions:
            continue
        version = next((v for v in c.versions if v.id == c.current_version_id), c.versions[-1])
        out.append(serializers.contract_summary(
            c, version, c.documents, parties_by_version.get(version.id, []), len(c.versions),
            flag_counts.get(version.id, (0, 0)),
        ))
    return out


@router.post("/upload", response_model=ContractSummary, status_code=status.HTTP_202_ACCEPTED)
async def upload_contract(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new contract from a PDF (its version 1) and queue processing."""
    storage_key, display_name, size, sha = await _store_pdf(file)

    contract = Contract(user_id=user.id, title=title_from_display_name(display_name))
    db.add(contract)
    await db.flush()

    version = ContractVersion(
        contract_id=contract.id, version_number=1, label="v1",
        status=ContractStatus.PENDING, stage="Queued", progress=0,
        extraction_status=ExtractionStatus.PENDING.value,
    )
    db.add(version)
    await db.flush()

    db.add(Document(
        contract_id=contract.id, version_id=version.id, kind=DocumentKind.MAIN,
        filename=display_name, storage_key=storage_key, sha256=sha, size_bytes=size,
    ))
    contract.current_version_id = version.id
    await db.commit()

    job_runner.enqueue(version.id)
    contract, version = await _load_full(db, contract.id, user, None)
    return serializers.contract_summary(contract, version, contract.documents, version.parties, len(contract.versions))


SAMPLE_FILE = Path(__file__).resolve().parent.parent.parent / "sample_data" / "northwind_msa_v2.pdf"


@router.post("/sample", response_model=ContractSummary, status_code=status.HTTP_202_ACCEPTED)
async def load_sample_contract(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a bundled, clearly-labelled synthetic contract so a new user can see the app work. Safe to delete."""
    if not SAMPLE_FILE.exists():
        raise HTTPException(status_code=404, detail="The sample contract is not available in this installation.")
    import io
    from starlette.datastructures import UploadFile as StarletteUpload
    upload = StarletteUpload(file=io.BytesIO(SAMPLE_FILE.read_bytes()), filename="Sample Northwind MSA (safe to delete).pdf")
    return await upload_contract(file=upload, user=user, db=db)


@router.post("/{contract_id}/versions", response_model=VersionSummary, status_code=status.HTTP_202_ACCEPTED)
async def upload_version(
    contract_id: uuid.UUID,
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
    kind: DocumentKind = Form(DocumentKind.MAIN),
    make_current: bool = Form(True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a new version (v2, an amendment, ...) of an existing contract."""
    contract = await get_owned_contract(contract_id, user, db)
    storage_key, display_name, size, sha = await _store_pdf(file)

    next_number = ((await db.execute(
        select(func.max(ContractVersion.version_number)).where(ContractVersion.contract_id == contract.id)
    )).scalar() or 0) + 1

    version = ContractVersion(
        contract_id=contract.id, version_number=next_number,
        label=(label or "").strip()[:120] or f"v{next_number}",
        status=ContractStatus.PENDING, stage="Queued", progress=0,
        extraction_status=ExtractionStatus.PENDING.value,
    )
    db.add(version)
    await db.flush()

    db.add(Document(
        contract_id=contract.id, version_id=version.id, kind=kind,
        filename=display_name, storage_key=storage_key, sha256=sha, size_bytes=size,
    ))
    if make_current:
        contract.current_version_id = version.id
    contract.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(version)

    job_runner.enqueue(version.id)
    return serializers.version_summary(version, display_name)


@router.get("/{contract_id}", response_model=ContractDetail)
async def get_contract(
    contract_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = Query(None, description="Defaults to the current version"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract, version = await _load_full(db, contract_id, user, version_id)
    return serializers.contract_detail(contract, version, contract.documents, contract.versions)


@router.patch("/{contract_id}", response_model=ContractSummary)
async def update_contract(
    contract_id: uuid.UUID,
    payload: ContractUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    if payload.title is not None:
        contract.title = payload.title.strip()
    if payload.counterparty is not None:
        contract.counterparty = payload.counterparty.strip() or None
    if payload.contract_type is not None:
        contract.contract_type = " ".join(payload.contract_type.split()) or None
    if payload.tags is not None:
        contract.tags = normalize_tags(payload.tags)
    contract.updated_at = datetime.utcnow()
    await db.commit()
    contract, version = await _load_full(db, contract_id, user, None)
    return serializers.contract_summary(contract, version, contract.documents, version.parties, len(contract.versions))


@router.get("/{contract_id}/versions", response_model=List[VersionSummary])
async def list_versions(
    contract_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(
        contract_id, user, db, selectinload(Contract.versions), selectinload(Contract.documents)
    )
    return [
        serializers.version_summary(v, serializers.main_filename(contract.documents, v.id))
        for v in sorted(contract.versions, key=lambda v: v.version_number)
    ]


@router.get("/{contract_id}/documents", response_model=List[DocumentResponse])
async def list_documents(
    contract_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db, selectinload(Contract.documents))
    return sorted(contract.documents, key=lambda d: d.created_at)


@router.get("/{contract_id}/status", response_model=ContractStatusResponse)
async def get_contract_status(
    contract_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight progress endpoint for polling while a background job runs."""
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    return serializers.contract_status(contract, version)


@router.get("/{contract_id}/fields", response_model=List[ExtractedFieldResponse])
async def list_fields(
    contract_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _, version = await _load_full(db, contract_id, user, version_id)
    return sorted(version.fields, key=lambda f: (f.field_key, f.item_index))


@router.get("/{contract_id}/flags", response_model=List[ReviewFlagResponse])
async def list_flags(
    contract_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = Query(None),
    status: Optional[FlagStatus] = Query(None, description="OPEN, RESOLVED or DISMISSED; omit for all"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The 'needs human review' flags for one contract version, most severe first."""
    _, version = await _load_full(db, contract_id, user, version_id)
    flags = [f for f in version.flags if status is None or f.status == status]
    return sorted(flags, key=serializers.flag_sort_key)


@router.get("/{contract_id}/text", response_model=ContractText)
async def get_contract_text(
    contract_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    pages = pages_from_raw_text(version.raw_text)
    return ContractText(
        contract_version_id=version.id, page_count=version.page_count,
        pages=[PageText(page=p, text=t) for p, t in pages],
    )


@router.get("/{contract_id}/file")
async def get_contract_file(
    contract_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = Query(None),
    document_id: Optional[uuid.UUID] = Query(None),
    original: bool = Query(False, description="For a Word upload: the original .docx instead of the converted PDF"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The stored PDF for a version (or a specific related document), owners only."""
    contract = await get_owned_contract(contract_id, user, db, selectinload(Contract.documents))
    if document_id is not None:
        doc = next((d for d in contract.documents if d.id == document_id), None)
    else:
        version = await resolve_version(db, contract, version_id)
        doc = next((d for d in contract.documents if d.version_id == version.id), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        path = job_runner.storage_file_path(doc.storage_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if doc.filename.lower().endswith(".docx"):
        if original:
            orig = job_runner.storage_file_path(f"{Path(doc.storage_key).stem}{ORIGINAL_DOCX_SUFFIX}")
            if not orig.exists():
                raise HTTPException(status_code=404, detail="The original Word file is not available.")
            return FileResponse(orig, media_type=DOCX_MEDIA_TYPE, filename=doc.filename, content_disposition_type="attachment")
        return FileResponse(path, media_type="application/pdf", filename=doc.filename[:-5] + ".pdf", content_disposition_type="inline")
    return FileResponse(path, media_type="application/pdf", filename=doc.filename, content_disposition_type="inline")


@router.post("/{contract_id}/reprocess", response_model=ContractStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def reprocess_contract(
    contract_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run extraction for a version (for example after configuring an LLM key)."""
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    if version.status in (ContractStatus.PENDING, ContractStatus.PROCESSING):
        raise HTTPException(status_code=409, detail="This version is already being processed.")
    version.status = ContractStatus.PENDING
    version.stage, version.progress = "Queued", 0
    version.extraction_status = ExtractionStatus.PENDING.value
    version.extraction_error = None
    await db.commit()
    job_runner.enqueue(version.id)
    return serializers.contract_status(contract, version)


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contract(
    contract_id: uuid.UUID,
    permanent: bool = Query(False, description="Delete irreversibly, including the stored files"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete by default, so an accidental delete can be undone with /restore."""
    contract = await get_owned_contract(contract_id, user, db, selectinload(Contract.documents), include_deleted=True)
    if not permanent:
        if contract.deleted_at is None:
            contract.deleted_at = datetime.utcnow()
            await db.commit()
        return

    for doc in contract.documents:
        try:
            for key in (doc.storage_key, f"{Path(doc.storage_key).stem}{ORIGINAL_DOCX_SUFFIX}"):
                path = job_runner.storage_file_path(key)
                if path.exists():
                    os.remove(path)
        except (ValueError, OSError):
            pass
    await db.delete(contract)
    await db.commit()


@router.post("/{contract_id}/restore", response_model=ContractSummary)
async def restore_contract(
    contract_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db, include_deleted=True)
    contract.deleted_at = None
    contract.updated_at = datetime.utcnow()
    await db.commit()
    contract, version = await _load_full(db, contract_id, user, None)
    return serializers.contract_summary(contract, version, contract.documents, version.parties, len(contract.versions))
