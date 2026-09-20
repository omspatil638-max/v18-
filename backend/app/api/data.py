"""
data.py: get your data out and back in.

  GET  /export/register.csv               every live contract with its key values, status and page (opens in Excel)
  GET  /contracts/{id}/evidence.md        one contract's values with status, page and quote, plus review history
  GET  /backup                            everything you own as one .zip (contracts, files, values, flags, rules ...)
  POST /backup/restore                    add the contracts of a backup to this account (never overwrites anything)
"""

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_contract, resolve_version
from app.core.config import settings
from app.db.database import get_db
from app.models.models import User
from app.services import backup_service, export_service
from app.services.text_index import DocIndex, pages_from_raw_text
from app.services.upload_validation import read_upload_limited
from app.services.vector_service import vector_service

router = APIRouter(tags=["Data"])
RESTORE_MAX_BYTES = 600 * 1024 * 1024


def _attachment(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}


@router.get("/export/register.csv")
async def export_register(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    text = await export_service.register_csv(db, user.id)
    return Response(text, media_type="text/csv; charset=utf-8", headers=_attachment(f"contractlens-register-{date.today().isoformat()}.csv"))


@router.get("/contracts/{contract_id}/evidence.md")
async def evidence_pack(
    contract_id: uuid.UUID, version_id: Optional[uuid.UUID] = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    text = await export_service.evidence_markdown(db, contract, version)
    stem = "".join(ch if ch.isalnum() else "-" for ch in contract.title)[:60].strip("-") or "contract"
    return Response(text, media_type="text/markdown; charset=utf-8", headers=_attachment(f"evidence-{stem}.md"))


@router.get("/backup")
async def download_backup(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data, counts = await backup_service.build_backup(db, user, settings.absolute_storage_path)
    headers = _attachment(f"contractlens-backup-{date.today().isoformat()}.zip")
    headers["X-Backup-Contracts"] = str(counts.get("contracts", 0))
    return Response(data, media_type="application/zip", headers=headers)


@router.post("/backup/restore")
async def restore_backup(
    file: UploadFile = File(...), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    data = await read_upload_limited(file, RESTORE_MAX_BYTES)

    async def chunker(session: AsyncSession, version) -> None:
        await vector_service.chunk_and_store(session, version.contract_id, version.id, DocIndex(pages_from_raw_text(version.raw_text)))

    try:
        counts = await backup_service.restore_backup(db, user, data, settings.absolute_storage_path, chunker)
    except backup_service.BackupError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"message": f"Restored {counts['contracts']} contract(s) from the backup.", **counts}
