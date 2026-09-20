"""sign-up profile (phone, consent, email verification) and morning briefing settings

Revision ID: 008_accounts_briefing
Revises: 007_policy_rules
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_accounts_briefing"
down_revision: Union[str, None] = "007_policy_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "email_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_verifications_user_id", "email_verifications", ["user_id"])

    op.add_column("alert_settings", sa.Column("briefing_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("alert_settings", sa.Column("briefing_hour", sa.Integer(), nullable=False, server_default="8"))
    op.add_column("alert_settings", sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"))
    op.add_column("alert_settings", sa.Column("paused_until", sa.Date(), nullable=True))
    op.add_column("alert_settings", sa.Column("last_briefing_on", sa.Date(), nullable=True))


def downgrade() -> None:
    for c in ("last_briefing_on", "paused_until", "timezone", "briefing_hour", "briefing_enabled"):
        op.drop_column("alert_settings", c)
    op.drop_index("ix_email_verifications_user_id", table_name="email_verifications")
    op.drop_table("email_verifications")
    for c in ("onboarded_at", "consented_at", "email_verified_at", "phone"):
        op.drop_column("users", c)
