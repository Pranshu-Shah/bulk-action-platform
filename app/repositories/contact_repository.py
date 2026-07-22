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
            # Deterministic order (lowest ID first) so de-duplication by
            # email has a stable, predictable "which one wins" rule
            # instead of arbitrary DB return order.
            .order_by(Contact.id)
            .all()
        )

    def get_updatable_fields(self) -> set[str]:
        return UPDATABLE_FIELDS

    def get_dedup_key(self, entity) -> object | None:
        return entity.email

    def save(self):
        self.db.commit()
