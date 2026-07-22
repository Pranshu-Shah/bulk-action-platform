import pytest

from app.actions.bulk_archive import BulkArchiveAction
from app.actions.bulk_assign_owner import BulkAssignOwnerAction
from app.actions.bulk_delete import BulkDeleteAction
from app.actions.bulk_export import BulkExportAction
from app.actions.bulk_update import BulkUpdateAction
from app.actions.constants import ARCHIVED_STATUS, DELETED_STATUS
from app.enums.bulk_item_status import BulkActionItemStatus
from app.enums.bulk_status import BulkActionStatus
from app.enums.log_status import LogStatus
from app.models.bulk_action import BulkAction
from app.models.bulk_action_item import BulkActionItem
from app.models.bulk_log import BulkLog


@pytest.fixture()
def bulk_action(db_session):
    action = BulkAction(
        action_type="bulk_update",
        entity_type="contact",
        status=BulkActionStatus.RUNNING,
        payload={},
    )
    db_session.add(action)
    db_session.commit()
    db_session.refresh(action)
    return action


def make_items(db_session, bulk_action, contacts):
    items = [
        BulkActionItem(
            bulk_action_id=bulk_action.id,
            contact_id=contact.id,
            status=BulkActionItemStatus.RUNNING,
        )
        for contact in contacts
    ]
    db_session.add_all(items)
    db_session.commit()
    for item in items:
        db_session.refresh(item)
    return items


def logs_for(db_session, bulk_action):
    return (
        db_session.query(BulkLog)
        .filter(BulkLog.bulk_action_id == bulk_action.id)
        .all()
    )


class TestBulkUpdateAction:

    def test_updates_all_contacts(self, db_session, bulk_action, contacts):
        items = make_items(db_session, bulk_action, contacts)
        entities_by_id = {c.id: c for c in contacts}

        result = BulkUpdateAction().execute(
            db=db_session,
            items=items,
            entities_by_id=entities_by_id,
            payload={"status": "INACTIVE", "age": 99},
            bulk_action=bulk_action,
        )

        assert result.succeeded == len(contacts)
        assert result.failed == 0
        assert result.skipped == 0

        for contact in contacts:
            db_session.refresh(contact)
            assert contact.status == "INACTIVE"
            assert contact.age == 99

        for item in items:
            db_session.refresh(item)
            assert item.status == BulkActionItemStatus.SUCCESS

        assert len(logs_for(db_session, bulk_action)) == len(contacts)

    def test_ignores_non_updatable_fields(self, db_session, bulk_action, contacts):
        items = make_items(db_session, bulk_action, contacts[:1])
        entities_by_id = {contacts[0].id: contacts[0]}

        BulkUpdateAction().execute(
            db=db_session,
            items=items,
            entities_by_id=entities_by_id,
            payload={"status": "INACTIVE", "not_a_real_field": "x"},
            bulk_action=bulk_action,
        )

        db_session.refresh(contacts[0])
        assert contacts[0].status == "INACTIVE"
        assert not hasattr(contacts[0], "not_a_real_field")

    def test_missing_contact_is_skipped(self, db_session, bulk_action, contacts):
        items = make_items(db_session, bulk_action, contacts)
        # Simulate a contact deleted between item creation and processing:
        # the item row is real (FK-valid), but the resolved-entities map
        # passed to execute() doesn't include it - exactly what tasks.py
        # would look like if EntityRegistry's get_by_ids no longer found it.
        entities_by_id = {c.id: c for c in contacts[1:]}

        result = BulkUpdateAction().execute(
            db=db_session,
            items=items,
            entities_by_id=entities_by_id,
            payload={"status": "INACTIVE"},
            bulk_action=bulk_action,
        )

        assert result.succeeded == len(contacts) - 1
        assert result.skipped == 1

        missing_item = items[0]
        db_session.refresh(missing_item)
        assert missing_item.status == BulkActionItemStatus.SKIPPED

    def test_one_bad_item_does_not_take_down_the_rest_of_the_batch(
        self, db_session, bulk_action, contacts,
    ):
        """
        The SAVEPOINT regression test: contacts[0] is updated to an email
        that collides with contacts[1]'s existing email (a real unique-
        constraint violation). Without per-item begin_nested(), this
        IntegrityError would only surface at the final db.commit(),
        silently reverting every other "successful" item in the same
        batch too. With the fix, only contacts[0]'s item should fail -
        everyone else should still succeed.
        """
        items = make_items(db_session, bulk_action, contacts)
        entities_by_id = {c.id: c for c in contacts}

        colliding_email = contacts[1].email

        # execute() applies the *same* payload to every item, so we can't
        # target one contact's email via payload alone - instead pre-set
        # contacts[0]'s email before calling execute(), and have the
        # payload update a harmless field, forcing a flush-time conflict
        # only for that one item's pending email + status change together.
        contacts[0].email = colliding_email

        result = BulkUpdateAction().execute(
            db=db_session,
            items=items,
            entities_by_id=entities_by_id,
            payload={"status": "INACTIVE"},
            bulk_action=bulk_action,
        )

        assert result.failed == 1
        assert result.succeeded == len(contacts) - 1

        db_session.refresh(items[0])
        assert items[0].status == BulkActionItemStatus.FAILED

        for item in items[1:]:
            db_session.refresh(item)
            assert item.status == BulkActionItemStatus.SUCCESS

        for contact in contacts[1:]:
            db_session.refresh(contact)
            assert contact.status == "INACTIVE"


class TestBulkDeleteAction:

    def test_soft_deletes_contacts(self, db_session, bulk_action, contacts):
        items = make_items(db_session, bulk_action, contacts)
        entities_by_id = {c.id: c for c in contacts}

        result = BulkDeleteAction().execute(
            db=db_session,
            items=items,
            entities_by_id=entities_by_id,
            payload={},
            bulk_action=bulk_action,
        )

        assert result.succeeded == len(contacts)

        for contact in contacts:
            db_session.refresh(contact)
            assert contact.status == DELETED_STATUS

        # Row physically still exists (soft delete, not a real DELETE).
        assert all(c.id is not None for c in contacts)


class TestBulkArchiveAction:

    def test_archives_contacts(self, db_session, bulk_action, contacts):
        items = make_items(db_session, bulk_action, contacts)
        entities_by_id = {c.id: c for c in contacts}

        result = BulkArchiveAction().execute(
            db=db_session,
            items=items,
            entities_by_id=entities_by_id,
            payload={},
            bulk_action=bulk_action,
        )

        assert result.succeeded == len(contacts)

        for contact in contacts:
            db_session.refresh(contact)
            assert contact.status == ARCHIVED_STATUS


class TestBulkAssignOwnerAction:

    def test_assigns_owner(self, db_session, bulk_action, contacts):
        items = make_items(db_session, bulk_action, contacts)
        entities_by_id = {c.id: c for c in contacts}

        result = BulkAssignOwnerAction().execute(
            db=db_session,
            items=items,
            entities_by_id=entities_by_id,
            payload={"owner_id": 42},
            bulk_action=bulk_action,
        )

        assert result.succeeded == len(contacts)

        for contact in contacts:
            db_session.refresh(contact)
            assert contact.owner_id == 42


class TestBulkExportAction:

    def test_writes_snapshot_to_logs(self, db_session, bulk_action, contacts):
        items = make_items(db_session, bulk_action, contacts)
        entities_by_id = {c.id: c for c in contacts}

        result = BulkExportAction().execute(
            db=db_session,
            items=items,
            entities_by_id=entities_by_id,
            payload={},
            bulk_action=bulk_action,
        )

        assert result.succeeded == len(contacts)

        logs = logs_for(db_session, bulk_action)
        assert len(logs) == len(contacts)
        assert all(log.status == LogStatus.SUCCESS for log in logs)
        assert all("Exported:" in log.message for log in logs)

        # Read-only: contacts themselves are untouched.
        for contact in contacts:
            db_session.refresh(contact)
            assert contact.status == "ACTIVE"
