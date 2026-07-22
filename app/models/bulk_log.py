from sqlalchemy import Enum, ForeignKey, Integer, Text

from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.enums.log_status import LogStatus
from app.models.base import TimestampMixin


class BulkLog(Base, TimestampMixin):
    __tablename__ = "bulk_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    bulk_action_id: Mapped[int] = mapped_column(
        ForeignKey("bulk_actions.id"),
        index=True,
    )

    entity_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    status: Mapped[LogStatus] = mapped_column(
        Enum(LogStatus),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    bulk_action = relationship(
        "BulkAction",
        back_populates="logs",
    )