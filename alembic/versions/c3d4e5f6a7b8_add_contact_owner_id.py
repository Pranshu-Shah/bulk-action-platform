"""Add owner_id to contacts (supports BulkAssignOwnerAction)

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_contacts_owner_id", "contacts", ["owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_contacts_owner_id", table_name="contacts")
    op.drop_column("contacts", "owner_id")
