"""
backfill_flags.py — generate review flags for contract versions processed before Phase 4.

Uses only data already stored (extracted fields/parties/clauses/obligations and the document
text), so it makes NO LLM calls and spends no API quota. Safe to re-run: flags are rebuilt and
any resolved/dismissed decisions are preserved.

    python -m scripts.backfill_flags
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.db.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.models import Contract, ContractStatus, ContractVersion  # noqa: E402
from app.services import flag_service  # noqa: E402
from app.services.text_index import DocIndex, pages_from_raw_text  # noqa: E402


async def main() -> int:
    done = 0
    async with AsyncSessionLocal() as db:
        versions = (await db.execute(
            select(ContractVersion)
            .where(ContractVersion.status == ContractStatus.READY, ContractVersion.raw_text.isnot(None))
            .options(
                selectinload(ContractVersion.fields), selectinload(ContractVersion.parties),
                selectinload(ContractVersion.clauses), selectinload(ContractVersion.obligations),
            )
        )).scalars().all()

        for v in versions:
            contract = await db.get(Contract, v.contract_id)
            if contract is None:
                continue
            meta = v.extraction_meta or {}
            n = await flag_service.generate_flags(
                db, contract, v, v.fields, v.parties, v.clauses, v.obligations,
                DocIndex(pages_from_raw_text(v.raw_text)), extraction_status=v.extraction_status,
                extraction_error=v.extraction_error, chunks_ok=meta.get("chunks_ok"), chunks_total=meta.get("chunks_total"),
            )
            print(f"  {contract.title[:40]:40} {v.label:8} -> {n} flag(s)")
            done += 1
        await db.commit()
    await engine.dispose()
    print(f"Backfilled {done} version(s).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
