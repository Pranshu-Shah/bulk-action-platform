from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, JSON, String

from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.enums.bulk_status import BulkActionStatus
from app.models.base import TimestampMixin


class BulkAction(Base, TimestampMixin):
    __tablename__ = "bulk_actions"

    id: Mapped[int] = mapped_column(primary_key=True)

    action_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    entity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    status: Mapped[BulkActionStatus] = mapped_column(
        Enum(BulkActionStatus),
        default=BulkActionStatus.QUEUED,
        nullable=False,
    )

    payload: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    # Client-supplied key to prevent duplicate submission on double-click /
    # network retry. Nullable + only unique when present (see migration).
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    logs = relationship(
        "BulkLog",
        back_populates="bulk_action",
        cascade="all, delete-orphan",
    )

    items = relationship(
        "BulkActionItem",
        back_populates="bulk_action",
        cascade="all, delete-orphan",
    )

    stats = relationship(
        "BulkActionStats",
        back_populates="bulk_action",
        uselist=False,
        cascade="all, delete-orphan",
    )