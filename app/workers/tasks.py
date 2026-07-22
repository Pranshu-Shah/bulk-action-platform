import time
from datetime import datetime, UTC

import structlog

from app.actions.registry import ActionRegistry
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.rate_limiter import RateLimiter
from app.core.utils import chunk_list, register_or_flag_duplicate
from app.entities.registry import EntityRegistry
from app.enums.bulk_item_status import BulkActionItemStatus
from app.enums.bulk_status import BulkActionStatus
from app.enums.log_status import LogStatus
from app.repositories.bulk_action_item_repository import BulkActionItemRepository
from app.repositories.bulk_action_repository import BulkActionRepository
from app.repositories.bulk_action_stats_repository import BulkActionStatsRepository
from app.repositories.bulk_log_repository import BulkLogRepository
from app.workers.celery_app import celery

logger = structlog.get_logger(__name__)

TERMINAL_ITEM_STATUSES = (
    BulkActionItemStatus.SUCCESS,
    BulkActionItemStatus.FAILED,
    BulkActionItemStatus.SKIPPED,
)


@celery.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def dispatch_bulk_action(self, bulk_action_id: int, entity_ids: list[int]):
    """
    Snapshots the bulk action's targets into `bulk_action_items`, then fans
    the work out across N `process_bulk_action_batch` tasks so batches run
    in parallel across Celery workers - replacing the old design where
    `process_bulk_action` ran an entire job serially, single task, single
    worker.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        bulk_action_id=bulk_action_id,
        task="dispatch_bulk_action",
    )

    start = time.perf_counter()
    db = SessionLocal()

    try:
        bulk_repo = BulkActionRepository(db)
        item_repo = BulkActionItemRepository(db)
        stats_repo = BulkActionStatsRepository(db)
        log_repo = BulkLogRepository(db)

        bulk_action = bulk_repo.get(bulk_action_id)

        if not bulk_action:
            logger.warning("dispatch_skipped", reason="bulk_action_not_found")
            return

        if bulk_action.status == BulkActionStatus.CANCELLED:
            logger.info("dispatch_skipped", reason="already_cancelled")
            return

        logger.info("dispatch_started", entity_id_count=len(entity_ids))

        bulk_action.status = BulkActionStatus.RUNNING
        bulk_action.started_at = datetime.now(UTC)
        bulk_repo.commit()

        entity_repo = EntityRegistry.get_repository(bulk_action.entity_type, db)

        # bulk_action_items.contact_id has a real FK to contacts, so an
        # item can only be created for an ID that actually exists.
        # Existence is checked in chunks (never one query for the whole
        # ~1M-ID list) and anything not found is logged SKIPPED here,
        # directly, with no item row at all - it will never reach a batch
        # worker to be marked SKIPPED the normal way.
        #
        # De-duplication rides along in the same pass: entity types that
        # define a dedup key (e.g. Contact -> email) have every entity
        # after the first occurrence of a given key treated the same way
        # as a missing ID - logged SKIPPED, no item row - rather than
        # reprocessing the same underlying record twice in one job.
        found_ids = set()
        seen_dedup_keys = set()
        duplicate_ids = []

        for chunk in chunk_list(entity_ids, settings.BATCH_SIZE):
            for entity in entity_repo.get_by_ids(chunk):
                if register_or_flag_duplicate(entity, seen_dedup_keys, entity_repo.get_dedup_key):
                    duplicate_ids.append(entity.id)
                else:
                    found_ids.add(entity.id)

        duplicate_id_set = set(duplicate_ids)
        missing_ids = [
            entity_id for entity_id in entity_ids
            if entity_id not in found_ids and entity_id not in duplicate_id_set
        ]

        item_repo.bulk_create(bulk_action_id, list(found_ids))
        stats_repo.create(bulk_action_id, total=len(entity_ids))

        if missing_ids or duplicate_ids:

            for missing_id in missing_ids:
                log_repo.create(
                    bulk_action_id,
                    missing_id,
                    LogStatus.SKIPPED,
                    "Entity not found",
                )

            for duplicate_id in duplicate_ids:
                log_repo.create(
                    bulk_action_id,
                    duplicate_id,
                    LogStatus.SKIPPED,
                    "Duplicate entity (already targeted by this bulk action)",
                )

            db.commit()
            stats_repo.increment(
                bulk_action_id,
                skipped=len(missing_ids) + len(duplicate_ids),
            )

        batch_count = 0

        for batch_ids in item_repo.iter_id_batches(bulk_action_id):
            process_bulk_action_batch.delay(bulk_action_id, batch_ids)
            batch_count += 1

        # Edge case: if every entity_id was missing, zero batches are ever
        # dispatched, so process_bulk_action_batch's own completion check
        # never runs for this action - it would sit in RUNNING forever
        # despite being fully (if trivially) done. Same check as the batch
        # worker's, done here too as a safety net for that zero-batch case.
        if stats_repo.is_complete(bulk_action_id) and bulk_action.status != BulkActionStatus.CANCELLED:
            bulk_action.status = BulkActionStatus.COMPLETED
            bulk_action.completed_at = datetime.now(UTC)
            bulk_repo.commit()

        logger.info(
            "dispatch_completed",
            item_count=len(found_ids),
            missing_count=len(missing_ids),
            duplicate_count=len(duplicate_ids),
            batch_count=batch_count,
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    except Exception as e:

        logger.error(
            "dispatch_failed",
            error=str(e),
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )

        db.rollback()

        bulk_action = BulkActionRepository(db).get(bulk_action_id)

        if bulk_action:
            bulk_action.status = BulkActionStatus.FAILED
            bulk_action.completed_at = datetime.now(UTC)
            db.commit()

        raise

    finally:
        db.close()


@celery.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def process_bulk_action_batch(self, bulk_action_id: int, batch_item_ids: list[int]):
    """
    Processes one batch of items. Idempotent: items already in a terminal
    state - from a prior delivery of this same batch, e.g. after a retry -
    are skipped instead of reprocessed, so Celery's
    `autoretry_for=(Exception,)` doesn't double-count stats or duplicate
    logs when only part of a batch actually failed.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        bulk_action_id=bulk_action_id,
        task="process_bulk_action_batch",
        batch_size=len(batch_item_ids),
    )

    start = time.perf_counter()
    db = SessionLocal()

    try:
        bulk_repo = BulkActionRepository(db)
        item_repo = BulkActionItemRepository(db)
        stats_repo = BulkActionStatsRepository(db)

        bulk_action = bulk_repo.get(bulk_action_id)

        if not bulk_action:
            logger.warning("batch_skipped", reason="bulk_action_not_found")
            return

        # Cooperative cancellation: bail out before doing any work on this
        # batch if the action has been cancelled since it was enqueued.
        if bulk_action.status == BulkActionStatus.CANCELLED:
            logger.info("batch_skipped", reason="cancelled")
            return

        items = item_repo.get_by_ids(bulk_action_id, batch_item_ids)
        pending = [
            item for item in items
            if item.status not in TERMINAL_ITEM_STATUSES
        ]

        if not pending:
            logger.info("batch_skipped", reason="already_processed")
            return

        # Rate limiting: only enforced when this bulk action has an
        # account_id at all. Checked before marking items RUNNING -
        # otherwise a rate-limited batch would leave its items stuck in
        # RUNNING while waiting for the next window. max_retries=None on
        # this specific retry call means a sustained rate limit keeps
        # retrying indefinitely, rather than being subject to the task's
        # normal retry_kwargs={"max_retries": 3} (which is still what
        # applies to genuine processing errors via autoretry_for).
        if bulk_action.account_id is not None:
            if not RateLimiter().try_consume(bulk_action.account_id, len(pending)):
                logger.info(
                    "batch_rate_limited",
                    account_id=bulk_action.account_id,
                    pending_count=len(pending),
                )
                raise self.retry(countdown=60, max_retries=None)

        logger.info("batch_started", pending_count=len(pending))

        item_repo.bulk_mark_status(
            [item.id for item in pending],
            BulkActionItemStatus.RUNNING,
        )
        db.commit()

        entity_repo = EntityRegistry.get_repository(bulk_action.entity_type, db)

        entities_by_id = {
            entity.id: entity
            for entity in entity_repo.get_by_ids(
                [item.contact_id for item in pending]
            )
        }

        action = ActionRegistry.get_action(bulk_action.action_type)

        result = action.execute(
            db=db,
            items=pending,
            entities_by_id=entities_by_id,
            payload=bulk_action.payload,
            bulk_action=bulk_action,
        )

        stats_repo.increment(
            bulk_action_id,
            succeeded=result.succeeded,
            failed=result.failed,
            skipped=result.skipped,
        )

        is_complete = stats_repo.is_complete(bulk_action_id)

        if is_complete and bulk_action.status != BulkActionStatus.CANCELLED:
            bulk_action.status = BulkActionStatus.COMPLETED
            bulk_action.completed_at = datetime.now(UTC)
            bulk_repo.commit()

        logger.info(
            "batch_completed",
            succeeded=result.succeeded,
            failed=result.failed,
            skipped=result.skipped,
            bulk_action_completed=is_complete,
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    finally:
        db.close()
