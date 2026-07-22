from sqlalchemy.orm import Session

from app.entities.base import BaseEntityRepository
from app.repositories.contact_repository import ContactRepository


class EntityRegistry:
    """
    Maps entity_type -> its BaseEntityRepository implementation. Mirrors
    ActionRegistry, but registers classes (not singleton instances):
    unlike actions, a repository is bound to a specific db session, so a
    fresh instance is created per lookup.
    """

    repositories = {
        "contact": ContactRepository,
    }

    @classmethod
    def get_repository(cls, entity_type: str, db: Session) -> BaseEntityRepository:

        repository_cls = cls.repositories.get(entity_type)

        if not repository_cls:
            raise ValueError(
                f"Unsupported entity type: {entity_type}"
            )

        return repository_cls(db)

    @classmethod
    def supported_types(cls) -> set[str]:
        return set(cls.repositories.keys())
