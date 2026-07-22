import pytest

from app.core.exceptions import (
    BulkActionNotCancellableError,
    BulkActionNotFoundError,
    InvalidPayloadError,
    UnsupportedActionTypeError,
    UnsupportedEntityTypeError,
)
from app.enums.bulk_status import BulkActionStatus
from app.models.bulk_action import BulkAction
from app.services.bulk_action_service import BulkActionService

# An ID that can never collide with a real seeded contact - used so these
# validation-focused tests don't need the `contacts` fixture at all.
MISSING_CONTACT_ID = 999_999_999


@pytest.fixture()
def service(db_session):
    return BulkActionService(db_session)


class TestCreateBulkActionValidation:

    def test_unsupported_action_type(self, service):
        with pytest.raises(UnsupportedActionTypeError):
            service.create_bulk_action(
                action_type="bulk_frobnicate",
                entity_type="contact",
                entity_ids=[MISSING_CONTACT_ID],
                payload={},
            )

    def test_unsupported_entity_type(self, service):
        with pytest.raises(UnsupportedEntityTypeError):
            service.create_bulk_action(
                action_type="bulk_update",
                entity_type="widget",
                entity_ids=[MISSING_CONTACT_ID],
                payload={"status": "INACTIVE"},
            )

    def test_bulk_update_rejects_empty_payload(self, service):
        with pytest.raises(InvalidPayloadError):
            service.create_bulk_action(
                action_type="bulk_update",
                entity_type="contact",
                entity_ids=[MISSING_CONTACT_ID],
                payload={},
            )

    def test_bulk_update_rejects_payload_with_no_updatable_fields(self, service):
        with pytest.raises(InvalidPayloadError):
            service.create_bulk_action(
                action_type="bulk_update",
                entity_type="contact",
                entity_ids=[MISSING_CONTACT_ID],
                payload={"not_a_real_field": 1},
            )

    def test_bulk_update_accepts_valid_payload(self, service):
        bulk_action = service.create_bulk_action(
            action_type="bulk_update",
            entity_type="contact",
            entity_ids=[MISSING_CONTACT_ID],
            payload={"status": "INACTIVE"},
        )
        assert bulk_action.id is not None

    def test_bulk_assign_owner_requires_owner_id_field(self, service):
        with pytest.raises(InvalidPayloadError):
            service.create_bulk_action(
                action_type="bulk_assign_owner",
                entity_type="contact",
                entity_ids=[MISSING_CONTACT_ID],
                payload={},
            )

    def test_bulk_assign_owner_requires_integer_owner_id(self, service):
        with pytest.raises(InvalidPayloadError):
            service.create_bulk_action(
                action_type="bulk_assign_owner",
                entity_type="contact",
                entity_ids=[MISSING_CONTACT_ID],
                payload={"owner_id": "not-an-int"},
            )

    def test_bulk_assign_owner_accepts_valid_payload(self, service):
        bulk_action = service.create_bulk_action(
            action_type="bulk_assign_owner",
            entity_type="contact",
            entity_ids=[MISSING_CONTACT_ID],
            payload={"owner_id": 7},
        )
        assert bulk_action.id is not None

    @pytest.mark.parametrize("action_type", ["bulk_delete", "bulk_archive", "bulk_export"])
    def test_no_payload_actions_accept_empty_payload(self, service, action_type):
        bulk_action = service.create_bulk_action(
            action_type=action_type,
            entity_type="contact",
            entity_ids=[MISSING_CONTACT_ID],
            payload={},
        )
        assert bulk_action.id is not None


class TestGetBulkAction:

    def test_raises_not_found(self, service):
        with pytest.raises(BulkActionNotFoundError):
            service.get_bulk_action(MISSING_CONTACT_ID)


class TestCancelBulkAction:

    def _create_with_status(self, db_session, status):
        bulk_action = BulkAction(
            action_type="bulk_update",
            entity_type="contact",
            status=status,
            payload={"status": "INACTIVE"},
        )
        db_session.add(bulk_action)
        db_session.commit()
        db_session.refresh(bulk_action)
        return bulk_action

    def test_raises_not_found(self, service):
        with pytest.raises(BulkActionNotFoundError):
            service.cancel_bulk_action(MISSING_CONTACT_ID)

    @pytest.mark.parametrize(
        "status",
        [BulkActionStatus.QUEUED, BulkActionStatus.RUNNING, BulkActionStatus.SCHEDULED],
    )
    def test_cancels_non_terminal_action(self, service, db_session, status):
        bulk_action = self._create_with_status(db_session, status)

        result = service.cancel_bulk_action(bulk_action.id)

        assert result.status == BulkActionStatus.CANCELLED

    @pytest.mark.parametrize(
        "status",
        [BulkActionStatus.COMPLETED, BulkActionStatus.FAILED, BulkActionStatus.CANCELLED],
    )
    def test_rejects_terminal_action(self, service, db_session, status):
        bulk_action = self._create_with_status(db_session, status)

        with pytest.raises(BulkActionNotCancellableError):
            service.cancel_bulk_action(bulk_action.id)
