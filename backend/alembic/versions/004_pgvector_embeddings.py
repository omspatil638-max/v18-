"""pgvector embeddings for hybrid retrieval

Revision ID: 004_pgvector
Revises: 003_unified_model
Create Date: 2026-09-20

Adds a real `vector(384)` column (bge-small-en-v1.5) with an HNSW cosine index next to the
existing full-text index. The legacy JSONB `embedding` column stays untouched and unused.
If the pgvector extension cannot be created (a server without it), the migration still
succeeds and the app falls back to full-text-only retrieval.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_pgvector"
down_revision: Union[str, None] = "003_unified_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIM = 384


def upgrade() -> None:
    op.add_column("contract_chunks", sa.Column("embedding_model", sa.String(80), nullable=True))
    bind = op.get_bind()
    has_vector = bind.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar()
    if not has_vector:
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"ALTER TABLE contract_chunks ADD COLUMN IF NOT EXISTS embedding_vec vector({DIM})")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contract_chunks_embedding_hnsw "
        "ON contract_chunks USING hnsw (embedding_vec vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contract_chunks_embedding_hnsw")
    op.execute("ALTER TABLE contract_chunks DROP COLUMN IF EXISTS embedding_vec")
    op.drop_column("contract_chunks", "embedding_model")
