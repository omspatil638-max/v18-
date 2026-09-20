"""unified data model: contract versions, documents, review flags, alerts, changes

Revision ID: 003_unified_model
Revises: 002_honest_extraction
Create Date: 2026-09-19

A contract becomes a logical agreement that owns one or more VERSIONS. Everything
extracted (fields, parties, clauses, obligations, deadlines, chunks) moves under the
version it came from, so an insight is always traceable to an exact revision.

Existing data is preserved: every contract gets a version 1 carrying its current
processing state and text, plus a document row for its uploaded file. Reminders are
carried over into the new alerts table (lead time derived from the original dates).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_unified_model"
down_revision: Union[str, None] = "002_honest_extraction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHILD_TABLES = ("extracted_fields", "contract_parties", "clauses", "obligations", "deadlines", "contract_chunks")

CONTRACT_STATUS = postgresql.ENUM(
    "PENDING", "PROCESSING", "READY", "FAILED", "UNSUPPORTED", name="contractstatus", create_type=False
)
DEADLINE_TYPE = postgresql.ENUM(
    "EXPIRY", "RENEWAL_NOTICE", "PAYMENT", "OBLIGATION", "OTHER", name="deadlinetype", create_type=False
)
OBLIGATION_STATUS = postgresql.ENUM("PENDING", "COMPLETED", "OVERDUE", name="obligationstatus", create_type=False)


def _enum(name: str, *labels: str) -> postgresql.ENUM:
    """Create the enum type once, and return a reference that will NOT try to create it
    again when it is used inside create_table (which would raise DuplicateObject)."""
    postgresql.ENUM(*labels, name=name).create(op.get_bind(), checkfirst=True)
    return postgresql.ENUM(*labels, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. contract_versions ─────────────────────────────────────────────────
    op.create_table(
        "contract_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("status", CONTRACT_STATUS, nullable=False),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("extraction_status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column("extraction_meta", postgresql.JSONB(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("renewal_terms", sa.Text(), nullable=True),
        sa.Column("payment_terms", sa.Text(), nullable=True),
        sa.Column("termination_conditions", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("contract_id", "version_number", name="uq_version_number_per_contract"),
    )
    op.create_index("ix_contract_versions_contract_id", "contract_versions", ["contract_id"])

    # Every existing contract becomes version 1, carrying its state and text.
    bind.execute(sa.text("""
        INSERT INTO contract_versions (
            id, contract_id, version_number, label, status, stage, progress, extraction_status,
            extraction_error, extraction_meta, effective_date, expiry_date, renewal_terms,
            payment_terms, termination_conditions, raw_text, page_count, created_at, processed_at)
        SELECT gen_random_uuid(), c.id, 1, 'v1', c.status, c.stage, COALESCE(c.progress, 0),
               c.extraction_status, c.extraction_error, c.extraction_meta, c.effective_date,
               c.expiry_date, c.renewal_terms, c.payment_terms, c.termination_conditions,
               c.raw_text, COALESCE(c.page_count, 0), c.created_at, c.processed_at
        FROM contracts c
    """))

    # ── 2. documents ─────────────────────────────────────────────────────────
    document_kind = _enum("documentkind", "MAIN", "AMENDMENT", "SOW", "EXHIBIT", "OTHER")
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", document_kind, server_default="MAIN", nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("page_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_documents_contract_id", "documents", ["contract_id"])
    op.create_index("ix_documents_version_id", "documents", ["version_id"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])

    bind.execute(sa.text("""
        INSERT INTO documents (id, contract_id, version_id, kind, filename, storage_key, page_count, created_at)
        SELECT gen_random_uuid(), c.id, v.id, 'MAIN', c.filename, c.storage_path,
               COALESCE(c.page_count, 0), c.created_at
        FROM contracts c
        JOIN contract_versions v ON v.contract_id = c.id AND v.version_number = 1
    """))

    # ── 3. point every extracted row at its version ──────────────────────────
    for table in CHILD_TABLES:
        op.add_column(table, sa.Column("contract_version_id", postgresql.UUID(as_uuid=True), nullable=True))
        bind.execute(sa.text(f"""
            UPDATE {table} t SET contract_version_id = v.id
            FROM contract_versions v
            WHERE v.contract_id = t.contract_id AND v.version_number = 1
        """))
        # A row whose contract vanished cannot be attributed to a version; it is unusable.
        bind.execute(sa.text(f"DELETE FROM {table} WHERE contract_version_id IS NULL"))
        op.alter_column(table, "contract_version_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_version", table, "contract_versions", ["contract_version_id"], ["id"], ondelete="CASCADE"
        )
        op.create_index(f"ix_{table}_version_id", table, ["contract_version_id"])

    op.add_column("chat_messages", sa.Column("contract_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_chat_messages_version", "chat_messages", "contract_versions", ["contract_version_id"], ["id"], ondelete="SET NULL"
    )

    # ── 4. contracts becomes the logical agreement ───────────────────────────
    op.add_column("contracts", sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    bind.execute(sa.text("""
        UPDATE contracts c SET current_version_id = v.id
        FROM contract_versions v WHERE v.contract_id = c.id AND v.version_number = 1
    """))
    op.create_foreign_key(
        "fk_contracts_current_version", "contracts", "contract_versions", ["current_version_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("contracts", sa.Column("counterparty", sa.String(255), nullable=True))
    op.add_column("contracts", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("contracts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_contracts_deleted_at", "contracts", ["deleted_at"])
    op.create_index("ix_contracts_user_id", "contracts", ["user_id"])

    for col in ("filename", "storage_path", "status", "effective_date", "expiry_date", "renewal_terms",
                "payment_terms", "termination_conditions", "raw_text", "page_count", "processed_at",
                "stage", "progress", "extraction_status", "extraction_error", "extraction_meta"):
        op.drop_column("contracts", col)

    # ── 5. obligations: due-rule columns + occurrences (Phase 5 fills them) ──
    op.add_column("obligations", sa.Column("due_rule_type", sa.String(16), nullable=True))
    op.add_column("obligations", sa.Column("due_rule_json", postgresql.JSONB(), nullable=True))
    op.add_column("obligations", sa.Column("recurrence", sa.String(32), nullable=True))
    op.create_table(
        "obligation_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", OBLIGATION_STATUS, server_default="PENDING", nullable=False),
        sa.Column("basis", sa.Text(), nullable=True),
    )
    op.create_index("ix_obligation_occurrences_obligation_id", "obligation_occurrences", ["obligation_id"])

    # ── 6. review flags ──────────────────────────────────────────────────────
    flag_severity = _enum("flagseverity", "HIGH", "MEDIUM", "LOW")
    flag_status = _enum("flagstatus", "OPEN", "RESOLVED", "DISMISSED")
    op.create_table(
        "review_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("severity", flag_severity, server_default="MEDIUM", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_label", sa.String(255), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_section", sa.String(255), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=True),
        sa.Column("status", flag_status, server_default="OPEN", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_flags_contract_id", "review_flags", ["contract_id"])
    op.create_index("ix_review_flags_version_id", "review_flags", ["contract_version_id"])

    # ── 7. alerts (replacing reminders) ──────────────────────────────────────
    alert_status = _enum("alertstatus", "PENDING", "SENT", "ACKNOWLEDGED", "DISMISSED")
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deadline_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deadlines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_days", sa.Integer(), nullable=False),
        sa.Column("fire_on", sa.Date(), nullable=False),
        sa.Column("status", alert_status, server_default="PENDING", nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    for col in ("user_id", "contract_id", "deadline_id", "fire_on"):
        op.create_index(f"ix_alerts_{col}", "alerts", [col])

    # Carry existing reminders over; lead time is recovered from the original dates.
    bind.execute(sa.text("""
        INSERT INTO alerts (id, user_id, contract_id, deadline_id, lead_days, fire_on, status, message, acknowledged_at, created_at)
        SELECT gen_random_uuid(), r.user_id, d.contract_id, r.deadline_id,
               GREATEST((d.deadline_date - r.remind_at), 0), r.remind_at,
               CASE WHEN r.acknowledged THEN 'ACKNOWLEDGED' ELSE 'PENDING' END::alertstatus,
               r.message,
               CASE WHEN r.acknowledged THEN now() ELSE NULL END,
               now()
        FROM reminders r JOIN deadlines d ON d.id = r.deadline_id
    """))
    op.drop_table("reminders")

    op.create_table(
        "alert_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("lead_days", postgresql.JSONB(), nullable=True),
        sa.Column("email_to", sa.String(255), nullable=True),
        sa.Column("email_enabled", sa.Boolean(), server_default="false", nullable=False),
    )

    # ── 8. version comparison + summaries (Phases 6 and 7) ───────────────────
    change_type = _enum("changetype", "UNCHANGED", "MODIFIED", "ADDED", "REMOVED")
    op.create_table(
        "version_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_type", change_type, nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("old_quote", sa.Text(), nullable=True),
        sa.Column("new_quote", sa.Text(), nullable=True),
        sa.Column("old_page", sa.Integer(), nullable=True),
        sa.Column("new_page", sa.Integer(), nullable=True),
        sa.Column("old_section", sa.String(255), nullable=True),
        sa.Column("new_section", sa.String(255), nullable=True),
        sa.Column("is_material", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("impact_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_version_changes_contract_id", "version_changes", ["contract_id"])
    op.create_index("ix_version_changes_from", "version_changes", ["from_version_id"])
    op.create_index("ix_version_changes_to", "version_changes", ["to_version_id"])

    op.create_table(
        "summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lines", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_summaries_version_id", "summaries", ["contract_version_id"])


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("summaries")
    op.drop_table("version_changes")
    op.execute("DROP TYPE IF EXISTS changetype")
    op.drop_table("alert_settings")

    # Rebuild reminders from alerts before dropping them.
    op.create_table(
        "reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("deadline_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deadlines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("remind_at", sa.Date(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), server_default="false"),
        sa.Column("message", sa.Text(), nullable=False),
    )
    bind.execute(sa.text("""
        INSERT INTO reminders (id, deadline_id, user_id, remind_at, acknowledged, message)
        SELECT gen_random_uuid(), a.deadline_id, a.user_id, a.fire_on,
               (a.status IN ('ACKNOWLEDGED', 'DISMISSED')), a.message
        FROM alerts a
    """))
    op.drop_table("alerts")
    op.execute("DROP TYPE IF EXISTS alertstatus")

    op.drop_table("review_flags")
    op.execute("DROP TYPE IF EXISTS flagstatus")
    op.execute("DROP TYPE IF EXISTS flagseverity")

    op.drop_table("obligation_occurrences")
    for col in ("recurrence", "due_rule_json", "due_rule_type"):
        op.drop_column("obligations", col)

    # Restore the columns that moved onto contract_versions.
    op.add_column("contracts", sa.Column("filename", sa.String(255), nullable=True))
    op.add_column("contracts", sa.Column("storage_path", sa.String(512), nullable=True))
    op.add_column("contracts", sa.Column("status", CONTRACT_STATUS, nullable=True))
    op.add_column("contracts", sa.Column("effective_date", sa.Date(), nullable=True))
    op.add_column("contracts", sa.Column("expiry_date", sa.Date(), nullable=True))
    op.add_column("contracts", sa.Column("renewal_terms", sa.Text(), nullable=True))
    op.add_column("contracts", sa.Column("payment_terms", sa.Text(), nullable=True))
    op.add_column("contracts", sa.Column("termination_conditions", sa.Text(), nullable=True))
    op.add_column("contracts", sa.Column("raw_text", sa.Text(), nullable=True))
    op.add_column("contracts", sa.Column("page_count", sa.Integer(), server_default="0"))
    op.add_column("contracts", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("contracts", sa.Column("stage", sa.String(64), nullable=True))
    op.add_column("contracts", sa.Column("progress", sa.Integer(), server_default="0"))
    op.add_column("contracts", sa.Column("extraction_status", sa.String(24), server_default="pending"))
    op.add_column("contracts", sa.Column("extraction_error", sa.Text(), nullable=True))
    op.add_column("contracts", sa.Column("extraction_meta", postgresql.JSONB(), nullable=True))
    bind.execute(sa.text("""
        UPDATE contracts c SET
            status = v.status, effective_date = v.effective_date, expiry_date = v.expiry_date,
            renewal_terms = v.renewal_terms, payment_terms = v.payment_terms,
            termination_conditions = v.termination_conditions, raw_text = v.raw_text,
            page_count = v.page_count, processed_at = v.processed_at, stage = v.stage,
            progress = v.progress, extraction_status = v.extraction_status,
            extraction_error = v.extraction_error, extraction_meta = v.extraction_meta
        FROM contract_versions v
        WHERE v.id = c.current_version_id
    """))
    bind.execute(sa.text("""
        UPDATE contracts c SET filename = d.filename, storage_path = d.storage_key
        FROM documents d WHERE d.contract_id = c.id AND d.version_id = c.current_version_id
    """))
    bind.execute(sa.text("UPDATE contracts SET filename = 'unknown.pdf' WHERE filename IS NULL"))
    bind.execute(sa.text("UPDATE contracts SET storage_path = '' WHERE storage_path IS NULL"))
    bind.execute(sa.text("UPDATE contracts SET status = 'PENDING' WHERE status IS NULL"))
    for col in ("filename", "storage_path", "status"):
        op.alter_column("contracts", col, nullable=False)

    op.drop_constraint("fk_contracts_current_version", "contracts", type_="foreignkey")
    op.drop_index("ix_contracts_user_id", table_name="contracts")
    op.drop_index("ix_contracts_deleted_at", table_name="contracts")
    for col in ("deleted_at", "updated_at", "counterparty", "current_version_id"):
        op.drop_column("contracts", col)

    op.drop_constraint("fk_chat_messages_version", "chat_messages", type_="foreignkey")
    op.drop_column("chat_messages", "contract_version_id")

    for table in CHILD_TABLES:
        op.drop_index(f"ix_{table}_version_id", table_name=table)
        op.drop_constraint(f"fk_{table}_version", table, type_="foreignkey")
        op.drop_column(table, "contract_version_id")

    op.drop_table("documents")
    op.execute("DROP TYPE IF EXISTS documentkind")
    op.drop_index("ix_contract_versions_contract_id", table_name="contract_versions")
    op.drop_table("contract_versions")
