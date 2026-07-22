import time

import pytest
from celery.exceptions import Retry

from app.core.config import settings
from app.core.rate_limiter import RateLimiter, redis_client
from app.enums.bulk_item_status import BulkActionItemStatus
from app.enums.bulk_status import BulkActionStatus
from app.models.bulk_action import BulkAction
from app.models.bulk_action_item import BulkActionItem
from app.workers import tasks as tasks_module


@pytest.fixture()
def test_account_id():
    # Unique-ish per test run so parallel/repeated runs don't collide on
    # the same Redis bucket; cleaned up afterward regardless.
    account_id = int(time.time() * 1000) % 1_000_000_000
    yield account_id
    bucket = f"rate:{account_id}:{int(time.time() // 60)}"
    redis_client.delete(bucket)


class TestRateLimiter:

    def test_allows_under_budget(self, test_account_id):
        limiter = RateLimiter(limit_per_minute=100)
        assert limiter.try_consume(test_account_id, 50) is True
        assert limiter.try_consume(test_account_id, 40) is True

    def test_denies_over_budget(self, test_account_id):
        limiter = RateLimiter(limit_per_minute=100)
        assert limiter.try_consume(test_account_id, 80) is True
        assert limiter.try_consume(test_account_id, 30) is False

    def test_denial_rolls_back_the_reservation(self, test_account_id):
        limiter = RateLimiter(limit_per_minute=100)
        assert limiter.try_consume(test_account_id, 80) is True
        assert limiter.try_consume(test_account_id, 30) is False  # denied, should not consume
        # Budget should still show 80 used / 20 remaining, not 110 used.
        assert limiter.try_consume(test_account_id, 20) is True

    def test_no_account_id_configured_defaults_from_settings(self, test_account_id):
        limiter = RateLimiter()
        assert limiter.limit_per_minute == settings.RATE_LIMIT_PER_MINUTE


class TestBatchRateLimitingWiring:
    """
    Proves process_bulk_action_batch actually consults the rate limiter
    and backs off correctly - constructs a bulk action + items directly
    (bypassing dispatch_bulk_action) so this is isolated from dispatch's
    own exception handling, which would otherwise catch the Retry raised
    here too (an artifact of eager-mode's synchronous call chain, not a
    real production interaction - dispatch and batch tasks are entirely
    decoupled once actually queued through Redis).
    """

    def test_batch_retries_when_account_over_budget(
        self, db_session, monkeypatch, contacts, test_account_id,
    ):
        monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 1)

        bulk_action = BulkAction(
            action_type="bulk_export",
            entity_type="contact",
            status=BulkActionStatus.RUNNING,
            payload={},
            account_id=test_account_id,
        )
        db_session.add(bulk_action)
        db_session.commit()
        db_session.refresh(bulk_action)

        items = [
            BulkActionItem(
                bulk_action_id=bulk_action.id,
                contact_id=contact.id,
                status=BulkActionItemStatus.QUEUED,
            )
            for contact in contacts[:3]
        ]
        db_session.add_all(items)
        db_session.commit()
        for item in items:
            db_session.refresh(item)

        with pytest.raises(Retry):
            tasks_module.process_bulk_action_batch(
                bulk_action.id, [item.id for item in items],
            )

        # The rate-limit check runs before marking items RUNNING, so a
        # rate-limited batch must leave them untouched, ready to be
        # picked up cleanly on the next attempt.
        for item in items:
            db_session.refresh(item)
            assert item.status == BulkActionItemStatus.QUEUED

    def test_batch_proceeds_when_under_budget(
        self, db_session, monkeypatch, contacts, test_account_id,
    ):
        monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 1000)

        bulk_action = BulkAction(
            action_type="bulk_export",
            entity_type="contact",
            status=BulkActionStatus.RUNNING,
            payload={},
            account_id=test_account_id,
        )
        db_session.add(bulk_action)
        db_session.commit()
        db_session.refresh(bulk_action)

        items = [
            BulkActionItem(
                bulk_action_id=bulk_action.id,
                contact_id=contact.id,
                status=BulkActionItemStatus.QUEUED,
            )
            for contact in contacts[:3]
        ]
        db_session.add_all(items)
        db_session.commit()
        for item in items:
            db_session.refresh(item)

        tasks_module.process_bulk_action_batch(
            bulk_action.id, [item.id for item in items],
        )

        for item in items:
            db_session.refresh(item)
            assert item.status == BulkActionItemStatus.SUCCESS

    def test_no_account_id_is_never_rate_limited(
        self, db_session, monkeypatch, contacts,
    ):
        monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 1)  # would deny if it were checked

        bulk_action = BulkAction(
            action_type="bulk_export",
            entity_type="contact",
            status=BulkActionStatus.RUNNING,
            payload={},
            account_id=None,
        )
        db_session.add(bulk_action)
        db_session.commit()
        db_session.refresh(bulk_action)

        items = [
            BulkActionItem(
                bulk_action_id=bulk_action.id,
                contact_id=contact.id,
                status=BulkActionItemStatus.QUEUED,
            )
            for contact in contacts[:3]
        ]
        db_session.add_all(items)
        db_session.commit()
        for item in items:
            db_session.refresh(item)

        tasks_module.process_bulk_action_batch(
            bulk_action.id, [item.id for item in items],
        )

        for item in items:
            db_session.refresh(item)
            assert item.status == BulkActionItemStatus.SUCCESS
