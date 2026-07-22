from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class BulkActionStats(Base, TimestampMixin):
    __tablename__ = "bulk_action_stats"

    # The PK IS the FK: this is a strict one-to-one with bulk_actions,
    # so no separate surrogate key is needed.
    bulk_action_id: Mapped[int] = mapped_column(
        ForeignKey("bulk_actions.id"),
        primary_key=True,
    )

    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    bulk_action = relationship("BulkAction", back_populates="stats")
