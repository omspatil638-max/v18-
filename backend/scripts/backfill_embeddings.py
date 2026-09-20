"""
backfill_embeddings.py — embed retrieval chunks that have no vector yet (contracts processed
before Phase 7, or while embeddings were off). Local model only: no network calls after the
one-time model download, and no LLM quota is used. Safe to re-run.

    python -m scripts.backfill_embeddings
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.db.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.models import ContractChunk  # noqa: E402
from app.services import embedding_service  # noqa: E402
from app.services.vector_service import vector_service  # noqa: E402


async def main() -> int:
    if embedding_service.provider() == "none":
        print("EMBEDDING_PROVIDER=none: nothing to do.")
        return 0
    total = 0
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(text(
            "SELECT DISTINCT contract_version_id FROM contract_chunks WHERE embedding_vec IS NULL"))).scalars().all()
        for vid in ids:
            rows = (await db.execute(select(ContractChunk).where(
                ContractChunk.contract_version_id == vid, text("embedding_vec IS NULL")
            ).order_by(ContractChunk.chunk_index))).scalars().all()
            n = await vector_service._store_embeddings(db, list(rows))
            await db.commit()
            print(f"  version {str(vid)[:8]}: {n}/{len(rows)} chunks embedded")
            total += n
    await engine.dispose()
    print(f"Embedded {total} chunk(s).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
