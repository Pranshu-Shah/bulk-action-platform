from app.entities.base import BaseEntityRepository
from app.models.contact import Contact

# Which Contact fields bulk_update is allowed to overwrite. Lives here,
# not in app/actions/constants.py - it's a fact about the Contact entity,
# not about the bulk_update action.
UPDATABLE_FIELDS = {
    "name",
    "email",
    "status",
    "age",
}


class ContactRepository(BaseEntityRepository):

    entity_type = "contact"

    def get_by_ids(
        self,
        ids: list[int],
    ):
        return (
            self.db.query(Contact)
            .filter(Contact.id.in_(ids))
            .all()
        )

    def get_updatable_fields(self) -> set[str]:
        return UPDATABLE_FIELDS

    def save(self):
        self.db.commit()
