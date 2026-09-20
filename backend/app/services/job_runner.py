"""
job_runner.py
-------------
Background processing of one contract VERSION, with persisted progress the UI polls.

  read PDF -> index -> retrieval chunks -> map-reduce extraction -> persist -> deadlines/alerts

In-process asyncio worker, one version at a time (which also keeps LLM usage inside
free-tier rate limits). Job state lives on the contract_versions row, so a restart
loses nothing: recover_incomplete() re-queues anything that was mid-flight.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.models import (
    Clause, ClauseType, Contract, ContractParty, ContractStatus, ContractVersion, Deadline,
    Document, ExtractedField, ExtractionStatus, Obligation,
)
from app.services import (
    compare_service, deadline_service, docx_service, flag_service, ocr_service, review_service, summary_service,
)
from app.services.extraction_service import extraction_service, unavailable_fields
from app.services.pdf_service import pdf_service
from app.services.text_index import DocIndex
from app.services.validators import parse_date
from app.services.vector_service import vector_service

logger = logging.getLogger(__name__)

_tasks: Set[asyncio.Task] = set()
_sem: Optional[asyncio.Semaphore] = None


def storage_file_path(storage_key: str) -> Path:
    """Resolve a stored file, refusing anything that escapes the storage directory."""
    root = settings.absolute_storage_path.resolve()
    p = Path(storage_key)
    resolved = (p if p.is_absolute() else root / p).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("Stored file path escapes the storage directory.")
    return resolved


def docx_notes(storage_key: str) -> List[str]:
    """What the Word-to-PDF conversion left out (images, text boxes), read from the kept original."""
    try:
        orig = storage_file_path(f"{Path(storage_key).stem}.original.docx")
        return docx_service.analyze(orig.read_bytes()) if orig.exists() else []
    except Exception:  # noqa: BLE001 - the notes are informational
        return []


def enqueue(version_id: uuid.UUID) -> None:
    task = asyncio.get_running_loop().create_task(_guarded(version_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def enqueue_overview(contract_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """(Re)write the AI summary overview in the background."""
    task = asyncio.get_running_loop().create_task(_guarded_overview(contract_id, version_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _guarded_overview(contract_id: uuid.UUID, version_id: uuid.UUID) -> None:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(1)
    async with _sem:
        async with AsyncSessionLocal() as db:
            contract = await db.get(Contract, contract_id)
            version = await db.get(ContractVersion, version_id)
            if contract is None or version is None:
                return
            try:
                await summary_service.generate_overview(db, contract, version)
            except Exception:  # noqa: BLE001
                logger.exception("Summary overview failed for version %s", version_id)


def enqueue_comparison(contract_id: uuid.UUID, from_id: uuid.UUID, to_id: uuid.UUID) -> None:
    """Recompute a version comparison in the background (shares the one-at-a-time LLM budget)."""
    task = asyncio.get_running_loop().create_task(_guarded_compare(contract_id, from_id, to_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _guarded_compare(contract_id: uuid.UUID, from_id: uuid.UUID, to_id: uuid.UUID) -> None:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(1)
    async with _sem:
        async with AsyncSessionLocal() as db:
            contract = await db.get(Contract, contract_id)
            old = await db.get(ContractVersion, from_id)
            new = await db.get(ContractVersion, to_id)
            if contract is None or old is None or new is None:
                return
            try:
                await compare_service.compare_versions(db, contract, old, new)
            except Exception:  # noqa: BLE001
                logger.exception("Comparison failed for contract %s", contract_id)
                await db.rollback()
                meta = dict(new.extraction_meta or {})
                meta["comparison"] = {**(meta.get("comparison") or {}), "status": "ready", "impact_status": "unavailable",
                                      "notes": ["The comparison could not be completed. Try again."]}
                new.extraction_meta = meta
                await db.commit()


async def wait_idle() -> None:
    """Wait for all queued/running jobs (used by tests and graceful shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def recover_incomplete() -> int:
    """Re-queue versions that were PENDING/PROCESSING when the server stopped."""
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(
            select(ContractVersion.id).where(
                ContractVersion.status.in_([ContractStatus.PENDING, ContractStatus.PROCESSING])
            )
        )).scalars().all()
    for vid in ids:
        enqueue(vid)
    if ids:
        logger.info("Re-queued %d interrupted version job(s).", len(ids))
    return len(ids)


async def _guarded(version_id: uuid.UUID) -> None:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(1)
    async with _sem:
        try:
            await process_version(version_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Processing failed for version %s", version_id)
            async with AsyncSessionLocal() as db:
                v = await db.get(ContractVersion, version_id)
                if v is not None:
                    v.status = ContractStatus.FAILED
                    v.extraction_status = ExtractionStatus.UNAVAILABLE.value
                    v.extraction_error = f"Processing failed unexpectedly: {exc.__class__.__name__}"
                    v.stage, v.progress = None, 100
                    await db.commit()


async def process_version(version_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        version = await db.get(ContractVersion, version_id)
        if version is None:
            return
        contract = await db.get(Contract, version.contract_id)
        document = (await db.execute(
            select(Document).where(Document.version_id == version.id).order_by(Document.created_at)
        )).scalars().first()
        if contract is None or document is None:
            logger.warning("Version %s has no document to process", version_id)
            return

        async def step(stage: str, progress: int) -> None:
            version.stage, version.progress = stage, progress
            await db.commit()

        version.status = ContractStatus.PROCESSING
        version.extraction_status = ExtractionStatus.RUNNING.value
        version.extraction_error = None
        await step("Reading PDF", 5)

        pdf = await asyncio.to_thread(pdf_service.extract_text_from_pdf, str(storage_file_path(document.storage_key)))
        version.page_count = pdf.page_count
        document.page_count = pdf.page_count

        # Pages with no text layer (scans) are read with local OCR; whatever is found there is never marked verified.
        source_meta: Optional[Dict[str, Any]] = None
        ocr_pages: List[int] = []
        if pdf.page_count > 0 and ocr_service.available():
            pdf_path = str(storage_file_path(document.storage_key))
            need = await asyncio.to_thread(ocr_service.pages_needing_ocr, pdf_path, {p.page_number: p.char_count for p in pdf.pages})
            if need:
                async def ocr_progress(done: int, total: int) -> None:
                    await step(f"Reading scanned page {done + 1} of {total} (OCR)", 6 + int(8 * done / max(total, 1)))

                results, skipped = await ocr_service.ocr_pages(pdf_path, need, ocr_progress)
                for p in pdf.pages:
                    r = results.get(p.page_number)
                    if r is not None and r.text.strip():
                        p.text, p.char_count = r.text, len(r.text)
                pdf.total_char_count = sum(p.char_count for p in pdf.pages)
                pdf.full_text = "\n\n".join(f"--- PAGE {p.page_number} ---\n{p.text}" for p in pdf.pages if p.text)
                pdf.is_valid_text_pdf = pdf.total_char_count >= pdf_service.MIN_TOTAL_CHARS_THRESHOLD
                if not pdf.is_valid_text_pdf:
                    pdf.error_message = "No readable text was found in this document, even with OCR. The scan may be too faint, rotated or blank."
                source_meta = ocr_service.summarize(results, skipped)
                ocr_pages = [n for n, r in results.items() if r.text.strip()]
        if source_meta is None and document.filename.lower().endswith(".docx"):
            source_meta = {"kind": "docx", "converted": True, "notes": await asyncio.to_thread(docx_notes, document.storage_key)}

        if not pdf.is_valid_text_pdf:
            version.status = ContractStatus.UNSUPPORTED
            version.extraction_status = ExtractionStatus.UNSUPPORTED.value
            version.extraction_error = pdf.error_message
            version.raw_text = None
            version.stage, version.progress = None, 100
            await db.commit()
            return

        version.raw_text = pdf.full_text
        doc = DocIndex([(p.page_number, p.text) for p in pdf.pages if p.text])
        await step("Indexing document", 15)

        # Derived data is rebuilt from scratch, so re-processing never leaves stale rows.
        kept_reviews = await review_service.capture(db, version.id)      # a person's corrections survive a re-run
        for model in (Deadline, Obligation, Clause, ContractParty, ExtractedField):
            await db.execute(delete(model).where(model.contract_version_id == version.id))
        await vector_service.chunk_and_store(db, contract.id, version.id, doc)
        await step("Analysing contract", 20)

        async def on_progress(done: int, total: int) -> None:
            await step(f"Analysing section {done} of {total}", 20 + int(65 * done / max(total, 1)))

        outcome = await extraction_service.extract(doc, on_progress)
        if ocr_pages:
            demoted = sum(ocr_service.demote(rows, ocr_pages)
                          for rows in (outcome.fields, outcome.parties, outcome.clauses, outcome.obligations))
            source_meta = {**(source_meta or {}), "demoted": demoted}
        if source_meta:
            outcome.meta = {**(outcome.meta or {}), "source": source_meta}
        await step("Saving results", 90)

        ids = {"contract_id": contract.id, "contract_version_id": version.id}
        rows = outcome.fields if outcome.status != "unavailable" else unavailable_fields(outcome.error or "Extraction unavailable.")
        fields = [ExtractedField(**ids, **r) for r in rows]
        review_service.reapply(fields, kept_reviews)
        db.add_all(fields)
        parties = [ContractParty(**ids, **p) for p in outcome.parties]
        clauses = [Clause(**ids, **{**c, "clause_type": ClauseType(c["clause_type"])}) for c in outcome.clauses]
        obligations = [Obligation(**ids, **o) for o in outcome.obligations]
        db.add_all(parties)
        db.add_all(clauses)
        db.add_all(obligations)
        await db.flush()

        by_key = {f.field_key: f for f in fields}

        def field_date(key: str):
            f = by_key.get(key)
            return parse_date((f.value or {}).get("date")) if f and f.value else None

        version.effective_date = field_date("effective_date")
        version.expiry_date = field_date("expiration_date")
        version.renewal_terms = by_key["renewal_terms"].display_value if "renewal_terms" in by_key else None
        version.payment_terms = by_key["payment_terms"].display_value if "payment_terms" in by_key else None
        version.termination_conditions = (
            by_key["termination_conditions"].display_value if "termination_conditions" in by_key else None
        )

        await deadline_service.generate_for_version(db, contract, version, fields, obligations)

        meta = outcome.meta or {}
        await flag_service.generate_flags(
            db, contract, version, fields, parties, clauses, obligations, doc,
            extraction_status=outcome.status, extraction_error=outcome.error,
            chunks_ok=meta.get("chunks_ok"), chunks_total=meta.get("chunks_total"), source=source_meta,
        )

        version.status = ContractStatus.READY
        version.extraction_status = {
            "complete": ExtractionStatus.COMPLETE, "partial": ExtractionStatus.PARTIAL,
        }.get(outcome.status, ExtractionStatus.UNAVAILABLE).value
        version.extraction_error = outcome.error
        version.extraction_meta = outcome.meta
        version.processed_at = datetime.utcnow()
        version.stage, version.progress = None, 100
        contract.updated_at = datetime.utcnow()
        await db.commit()

        # The optional AI overview of the summary (the structured summary itself is always live).
        try:
            await summary_service.generate_overview(db, contract, version)
        except Exception:  # noqa: BLE001
            logger.exception("Summary overview failed for version %s", version.id)

        # A newer version is compared with the one before it as soon as it is ready. A failure here
        # must never turn a successfully processed version into a failed one.
        if version.version_number > 1:
            try:
                previous = await compare_service.previous_ready_version(db, version)
                if previous is not None:
                    await compare_service.compare_versions(db, contract, previous, version)
            except Exception:  # noqa: BLE001
                logger.exception("Automatic comparison failed for version %s", version.id)
