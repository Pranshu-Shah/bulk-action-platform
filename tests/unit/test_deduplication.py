from app.core.utils import register_or_flag_duplicate
from app.entities.registry import EntityRegistry
from app.enums.bulk_status import BulkActionStatus
from app.models.bulk_action import BulkAction
from app.models.bulk_log import BulkLog
from app.repositories.contact_repository import ContactRepository
from app.workers import tasks as tasks_module


class FakeEntity:
    def __init__(self, id, key):
        self.id = id
        self.key = key


class TestRegisterOrFlagDuplicate:
    """
    Pure-algorithm test: Contact.email has a real DB-level unique
    constraint, so two real contacts can never actually share an email -
    there's no way to construct that scenario with real rows. Testing
    the extracted function directly against plain fake objects proves
    the de-duplication logic itself is correct, independent of that
    constraint.
    """

    def test_first_occurrence_is_not_a_duplicate(self):
        seen = set()
        assert register_or_flag_duplicate(FakeEntity(1, "a"), seen, lambda e: e.key) is False

    def test_second_occurrence_of_same_key_is_a_duplicate(self):
        seen = set()
        get_key = lambda e: e.key  # noqa: E731

        assert register_or_flag_duplicate(FakeEntity(1, "a"), seen, get_key) is False
        assert register_or_flag_duplicate(FakeEntity(2, "a"), seen, get_key) is True
        assert register_or_flag_duplicate(FakeEntity(3, "b"), seen, get_key) is False

    def test_none_key_is_never_a_duplicate(self):
        seen = set()
        get_key = lambda e: None  # noqa: E731

        assert register_or_flag_duplicate(FakeEntity(1, None), seen, get_key) is False
        assert register_or_flag_duplicate(FakeEntity(2, None), seen, get_key) is False


class TestContactRepositoryDedupKey:

    def test_dedup_key_is_email(self, db_session, contacts):
        repo = ContactRepository(db_session)
        assert repo.get_dedup_key(contacts[0]) == contacts[0].email


class TestDispatchDeduplicationWiring:
    """
    Proves the full dispatch_bulk_action wiring - not just the extracted
    algorithm - correctly logs a duplicate as SKIPPED with no item row,
    without double-counting it as "missing" too. Uses real contacts (so
    bulk_action_items' FK to contacts is satisfied) wrapped in a
    repository that reports a forced, artificial dedup-key collision
    between two of them - the only way to exercise this end to end, since
    real contacts can never actually share an email.
    """

    def test_duplicate_is_skipped_not_double_logged(self, db_session, monkeypatch, contacts):
        forced_duplicate_ids = {contacts[0].id, contacts[1].id}

        class DedupingContactRepository(ContactRepository):
            def get_dedup_key(self, entity):
                if entity.id in forced_duplicate_ids:
                    return "forced-duplicate-key"
                return super().get_dedup_key(entity)

        monkeypatch.setitem(
            EntityRegistry.repositories, "contact", DedupingContactRepository,
        )

        bulk_action = BulkAction(
            action_type="bulk_export",
            entity_type="contact",
            status=BulkActionStatus.QUEUED,
            payload={},
        )
        db_session.add(bulk_action)
        db_session.commit()
        db_session.refresh(bulk_action)

        tasks_module.dispatch_bulk_action(
            bulk_action.id,
            [contacts[0].id, contacts[1].id, contacts[2].id],
        )

        logs = (
            db_session.query(BulkLog)
            .filter(BulkLog.bulk_action_id == bulk_action.id)
            .all()
        )

        duplicate_logs = [log for log in logs if "Duplicate" in log.message]
        assert len(duplicate_logs) == 1
        assert duplicate_logs[0].entity_id == contacts[1].id

        # The "missing entity" path must not also fire for the duplicate -
        # it was found, just skipped for a different reason.
        not_found_logs = [log for log in logs if "not found" in log.message.lower()]
        assert len(not_found_logs) == 0

        db_session.refresh(bulk_action)
        assert bulk_action.status == BulkActionStatus.COMPLETED
