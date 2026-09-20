"""honest extraction foundation: sessions, extracted_fields, job state, provenance columns

Revision ID: 002_honest_extraction
Revises: 001_initial_schema
Create Date: 2026-09-19

Additive and reversible. Existing extraction rows were produced by the old pipeline,
which could fabricate values, so they are downgraded to needs_review with NULL
confidence (we do not know how sure that pipeline really was) and known fabricated
placeholder values are removed.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_honest_extraction"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users / sessions ─────────────────────────────────────────────────────
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)

    # ── contracts: background-job state ──────────────────────────────────────
    op.add_column("contracts", sa.Column("stage", sa.String(64), nullable=True))
    op.add_column("contracts", sa.Column("progress", sa.Integer(), server_default="0", nullable=False))
    op.add_column("contracts", sa.Column("extraction_status", sa.String(24), server_default="pending", nullable=False))
    op.add_column("contracts", sa.Column("extraction_error", sa.Text(), nullable=True))
    op.add_column("contracts", sa.Column("extraction_meta", postgresql.JSONB(), nullable=True))
    op.execute("UPDATE contracts SET extraction_status = 'unsupported', progress = 100 WHERE status = 'UNSUPPORTED'")
    op.execute("UPDATE contracts SET extraction_status = 'legacy', progress = 100 WHERE status = 'READY'")
    op.execute("UPDATE contracts SET extraction_status = 'unavailable', progress = 100 WHERE status = 'FAILED'")

    # ── shared-shape provenance on parties / clauses / obligations ───────────
    op.alter_column("contract_parties", "source_text", new_column_name="source_quote")
    op.alter_column("obligations", "source_text", new_column_name="source_quote")
    op.add_column("clauses", sa.Column("source_quote", sa.Text(), nullable=True))
    for table in ("contract_parties", "clauses", "obligations"):
        op.alter_column(table, "confidence", nullable=True, server_default=None)
        op.alter_column(table, "source_page", nullable=True, server_default=None)
    op.add_column("contract_parties", sa.Column("status", sa.String(24), server_default="needs_review", nullable=False))
    op.add_column("clauses", sa.Column("status", sa.String(24), server_default="needs_review", nullable=False))
    op.add_column("obligations", sa.Column("verification_status", sa.String(24), server_default="needs_review", nullable=False))

    # Legacy rows: unverified by construction. Remove placeholder values the old fallback invented.
    op.execute("DELETE FROM contract_parties WHERE name IN ('Party A (Licensor)', 'Party B (Licensee)')")
    op.execute("DELETE FROM obligations WHERE action = 'Pay required fees according to schedule' AND due_rule = 'Monthly'")
    op.execute("DELETE FROM clauses WHERE content = 'Licensee shall pay fees as set forth in the agreement.'")
    op.execute(
        "UPDATE contracts SET renewal_terms = NULL WHERE renewal_terms = 'Auto-renews annually unless notice is given prior to expiry.'"
    )
    op.execute(
        "UPDATE contracts SET payment_terms = NULL WHERE payment_terms = 'Payment due according to specified schedule.'"
    )
    op.execute(
        "UPDATE contracts SET termination_conditions = NULL WHERE termination_conditions = 'Written notice required for termination.'"
    )
    op.execute("UPDATE contract_parties SET confidence = NULL")
    op.execute("UPDATE clauses SET confidence = NULL")
    op.execute("UPDATE obligations SET confidence = NULL")

    # ── extracted_fields ─────────────────────────────────────────────────────
    op.create_table(
        "extracted_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_key", sa.String(40), nullable=False),
        sa.Column("item_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=True),
        sa.Column("display_value", sa.Text(), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("notes", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_extracted_fields_contract_id", "extracted_fields", ["contract_id"])

    # ── deadlines: provenance ────────────────────────────────────────────────
    op.add_column("deadlines", sa.Column("source_page", sa.Integer(), nullable=True))
    op.add_column("deadlines", sa.Column("source_section", sa.String(255), nullable=True))
    op.add_column("deadlines", sa.Column("source_quote", sa.Text(), nullable=True))
    op.add_column("deadlines", sa.Column("basis", sa.Text(), nullable=True))
    # Legacy deadlines were regex-scanned dates and a fabricated 60-day notice rule: not trustworthy.
    op.execute("DELETE FROM reminders")
    op.execute("DELETE FROM deadlines")

    # ── retrieval: full-text index (hash-embedding vectors were unusable; drop them) ──
    op.execute("UPDATE contract_chunks SET embedding = NULL")
    op.execute(
        "ALTER TABLE contract_chunks ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED"
    )
    op.execute("CREATE INDEX ix_contract_chunks_tsv ON contract_chunks USING GIN (tsv)")
    op.create_index("ix_contract_chunks_contract_id", "contract_chunks", ["contract_id"])

    # ── chat ─────────────────────────────────────────────────────────────────
    op.add_column("chat_messages", sa.Column("answer_status", sa.String(24), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "answer_status")
    op.drop_index("ix_contract_chunks_contract_id", table_name="contract_chunks")
    op.execute("DROP INDEX IF EXISTS ix_contract_chunks_tsv")
    op.execute("ALTER TABLE contract_chunks DROP COLUMN IF EXISTS tsv")
    for col in ("basis", "source_quote", "source_section", "source_page"):
        op.drop_column("deadlines", col)
    op.drop_index("ix_extracted_fields_contract_id", table_name="extracted_fields")
    op.drop_table("extracted_fields")
    op.drop_column("obligations", "verification_status")
    op.drop_column("clauses", "status")
    op.drop_column("contract_parties", "status")
    op.execute("UPDATE contract_parties SET confidence = 1.0 WHERE confidence IS NULL")
    op.execute("UPDATE clauses SET confidence = 1.0 WHERE confidence IS NULL")
    op.execute("UPDATE obligations SET confidence = 1.0 WHERE confidence IS NULL")
    op.execute("UPDATE contract_parties SET source_page = 1 WHERE source_page IS NULL")
    op.execute("UPDATE clauses SET source_page = 1 WHERE source_page IS NULL")
    op.execute("UPDATE obligations SET source_page = 1 WHERE source_page IS NULL")
    for table in ("contract_parties", "clauses", "obligations"):
        op.alter_column(table, "confidence", nullable=False, server_default="1.0")
        op.alter_column(table, "source_page", nullable=False, server_default="1")
    op.drop_column("clauses", "source_quote")
    op.alter_column("obligations", "source_quote", new_column_name="source_text")
    op.alter_column("contract_parties", "source_quote", new_column_name="source_text")
    for col in ("extraction_meta", "extraction_error", "extraction_status", "progress", "stage"):
        op.drop_column("contracts", col)
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_table("sessions")
    op.drop_column("users", "password_hash")
