"""Item-level tracking: add bulk_action_items and bulk_action_stats,
drop JSON entity_ids and denormalized counters from bulk_actions.

Revision ID: a1b2c3d4e5f6
Revises: 6f0023417fda
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "6f0023417fda"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- bulk_action_status: add SCHEDULED, CANCELLED -----------------
    # Postgres requires ALTER TYPE ... ADD VALUE to run outside an
    # explicit transaction block in older versions; alembic/psycopg2
    # handles this fine on PG12+, but it must be committed before the
    # new values are used in the same migration run, hence separate
    # statements rather than batching them with other DDL below.
    op.execute("ALTER TYPE bulkactionstatus ADD VALUE IF NOT EXISTS 'SCHEDULED'")
    op.execute("ALTER TYPE bulkactionstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")

    # --- bulk_action_items ---------------------------------------------
    # create_type=False: the type is created explicitly below via
    # `.create()`. Without this, op.create_table()'s own before_create
    # hook tries to create the same enum type a second time (it doesn't
    # know we already created it) and fails with DuplicateObject. Must be
    # postgresql.ENUM here, not the generic sa.Enum - the generic type
    # silently drops the create_type kwarg (it's not a real attribute on
    # it), so it has no effect there and the duplicate-create still fires.
    bulk_action_item_status = postgresql.ENUM(
        "QUEUED", "RUNNING", "SUCCESS", "FAILED", "SKIPPED",
        name="bulk_action_item_status",
        create_type=False,
    )
    bulk_action_item_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "bulk_action_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("bulk_action_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("status", bulk_action_item_status, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bulk_action_id"], ["bulk_actions.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_items_bulk_action_status", "bulk_action_items",
        ["bulk_action_id", "status"],
    )
    op.create_index(
        "ix_items_bulk_action_id_pk", "bulk_action_items",
        ["bulk_action_id", "id"],
    )

    # --- bulk_action_stats ----------------------------------------------
    op.create_table(
        "bulk_action_stats",
        sa.Column("bulk_action_id", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bulk_action_id"], ["bulk_actions.id"]),
        sa.PrimaryKeyConstraint("bulk_action_id"),
    )

    # --- bulk_actions: add idempotency_key, drop old columns -----------
    op.add_column(
        "bulk_actions",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_bulk_actions_idempotency_key",
        "bulk_actions",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.drop_column("bulk_actions", "entity_ids")
    op.drop_column("bulk_actions", "total_records")
    op.drop_column("bulk_actions", "processed_records")
    op.drop_column("bulk_actions", "success_count")
    op.drop_column("bulk_actions", "failed_count")
    op.drop_column("bulk_actions", "skipped_count")


def downgrade() -> None:
    op.add_column(
        "bulk_actions",
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "bulk_actions",
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "bulk_actions",
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "bulk_actions",
        sa.Column("processed_records", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "bulk_actions",
        sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "bulk_actions",
        sa.Column("entity_ids", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.drop_index("uq_bulk_actions_idempotency_key", table_name="bulk_actions")
    op.drop_column("bulk_actions", "idempotency_key")

    op.drop_table("bulk_action_stats")

    op.drop_index("ix_items_bulk_action_id_pk", table_name="bulk_action_items")
    op.drop_index("ix_items_bulk_action_status", table_name="bulk_action_items")
    op.drop_table("bulk_action_items")

    bulk_action_item_status = sa.Enum(name="bulk_action_item_status")
    bulk_action_item_status.drop(op.get_bind(), checkfirst=True)

    # Note: Postgres does not support removing values from an ENUM type,
    # so SCHEDULED/CANCELLED are not reverted here. A full downgrade of
    # the enum would require recreating the type, which is intentionally
    # left as a documented limitation rather than a destructive rebuild.
