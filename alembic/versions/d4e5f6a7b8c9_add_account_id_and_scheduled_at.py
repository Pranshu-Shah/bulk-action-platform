"""Add account_id and scheduled_at to bulk_actions (rate limiting + scheduling)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bulk_actions",
        sa.Column("account_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_bulk_actions_account_id", "bulk_actions", ["account_id"],
    )

    op.add_column(
        "bulk_actions",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bulk_actions", "scheduled_at")

    op.drop_index("ix_bulk_actions_account_id", table_name="bulk_actions")
    op.drop_column("bulk_actions", "account_id")
