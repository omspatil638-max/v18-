"""contract type and tags

Revision ID: 006_type_tags
Revises: 005_alert_repeats
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006_type_tags"
down_revision: Union[str, None] = "005_alert_repeats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contracts", sa.Column("contract_type", sa.String(40), nullable=True))
    op.add_column("contracts", sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("contracts", "tags")
    op.drop_column("contracts", "contract_type")
