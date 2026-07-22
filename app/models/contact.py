from sqlalchemy import Integer, String

from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    age: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Nullable: most contacts have no owner yet. No FK to a users/owners
    # table - none exists in this single-tenant app - this is just an
    # opaque identifier BulkAssignOwnerAction writes into.
    owner_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )