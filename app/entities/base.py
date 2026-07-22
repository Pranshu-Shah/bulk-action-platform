from abc import ABC, abstractmethod

from sqlalchemy.orm import Session


class BaseEntityRepository(ABC):
    """
    The entity-side counterpart to BaseBulkAction: one implementation per
    entity_type, resolved through EntityRegistry. Action handlers and the
    dispatcher/batch worker depend only on this interface, never on a
    concrete entity model/repository directly - that's what makes adding
    a new entity_type (Company, Lead, ...) a matter of writing one new
    class and registering it, not touching the dispatch/batch/handler
    code.
    """

    entity_type: str

    def __init__(self, db: Session):
        self.db = db

    @abstractmethod
    def get_by_ids(self, ids: list[int]) -> list:
        """Return the existing entities for the given IDs (missing IDs are silently omitted)."""

    @abstractmethod
    def get_updatable_fields(self) -> set[str]:
        """Field names bulk_update is allowed to overwrite on this entity type."""
