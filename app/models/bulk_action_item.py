from sqlalchemy import BigInteger, ForeignKey, Index, SmallInteger, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.enums.bulk_item_status import BulkActionItemStatus
from app.models.base import TimestampMixin


class BulkActionItem(Base, TimestampMixin):
    __tablename__ = "bulk_action_items"

    # BigInteger, not the default Integer/UUID: this table can hold up to
    # ~1M rows per bulk action, so PK choice matters for index locality.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    bulk_action_id: Mapped[int] = mapped_column(
        ForeignKey("bulk_actions.id"),
        nullable=False,
    )

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=False,
    )

    status: Mapped[BulkActionItemStatus] = mapped_column(
        SAEnum(BulkActionItemStatus, name="bulk_action_item_status"),
        default=BulkActionItemStatus.QUEUED,
        nullable=False,
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-item retry count, not to be confused with the Celery task's own
    # retry count — this tracks how many times THIS item specifically has
    # been attempted, which is what makes a re-delivered batch idempotent
    # (see Step 3: a batch worker checks this before re-processing an item
    # that's already SUCCESS/FAILED terminal).
    attempt_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

    bulk_action = relationship("BulkAction", back_populates="items")

    __table_args__ = (
        # The most important index in the schema: every batch-fetch,
        # progress query, and retry-scan filters on this pair.
        Index("ix_items_bulk_action_status", "bulk_action_id", "status"),
        # Supports keyset pagination when dispatching/streaming batches —
        # never OFFSET on a table this size.
        Index("ix_items_bulk_action_id_pk", "bulk_action_id", "id"),
    )
