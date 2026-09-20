"""user-defined policy rules

Revision ID: 007_policy_rules
Revises: 006_type_tags
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_policy_rules"
down_revision: Union[str, None] = "006_type_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "policy_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("operator", sa.String(12), nullable=False),
        sa.Column("value", sa.Integer(), nullable=True),
        sa.Column("target", sa.String(40), nullable=True),
        sa.Column("severity", sa.String(10), nullable=False, server_default="MEDIUM"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_policy_rules_user_id", "policy_rules", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_policy_rules_user_id", table_name="policy_rules")
    op.drop_table("policy_rules")
