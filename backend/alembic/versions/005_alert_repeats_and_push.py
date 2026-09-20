"""repeat notifications, one-click acknowledge token, web push subscriptions

Revision ID: 005_alert_repeats
Revises: 004_pgvector
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_alert_repeats"
down_revision: Union[str, None] = "004_pgvector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("email_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("alerts", sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alerts", sa.Column("push_count", sa.Integer(), nullable=False, server_default="0"))
    # Alerts emailed before this migration count as one email so they are not sent a "first" time again.
    op.execute("UPDATE alerts SET email_count = 1 WHERE emailed_at IS NOT NULL")

    op.add_column("alert_settings", sa.Column("repeat_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("alert_settings", sa.Column("repeat_hours", sa.Integer(), nullable=False, server_default="24"))
    op.add_column("alert_settings", sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("alert_settings", sa.Column("ack_token", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_alert_settings_ack_token", "alert_settings", ["ack_token"])

    op.create_table(
        "push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_constraint("uq_alert_settings_ack_token", "alert_settings", type_="unique")
    for c in ("ack_token", "push_enabled", "repeat_hours", "repeat_enabled"):
        op.drop_column("alert_settings", c)
    for c in ("push_count", "pushed_at", "email_count"):
        op.drop_column("alerts", c)
