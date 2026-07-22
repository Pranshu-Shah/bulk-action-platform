import structlog

from app.actions.base import BaseBulkAction, BulkActionResult
from app.entities.registry import EntityRegistry
from app.enums.bulk_item_status import BulkActionItemStatus
from app.enums.log_status import LogStatus
from app.repositories.bulk_action_item_repository import BulkActionItemRepository
from app.repositories.bulk_log_repository import BulkLogRepository

logger = structlog.get_logger(__name__)


class BulkUpdateAction(BaseBulkAction):

    def execute(
        self,
        db,
        items,
        entities_by_id,
        payload,
        bulk_action,
    ):
        item_repository = BulkActionItemRepository(db)
        log_repository = BulkLogRepository(db)

        # Which fields are updatable is a fact about the entity type, not
        # about this action - asked of the registered entity repository
        # rather than hardcoded, so a new entity_type doesn't require any
        # change here.
        updatable_fields = EntityRegistry.get_repository(
            bulk_action.entity_type, db
        ).get_updatable_fields()

        succeeded = 0
        failed = 0
        skipped = 0

        for item in items:

            entity = entities_by_id.get(item.contact_id)

            if entity is None:

                item_repository.mark_item_result(
                    item.id,
                    BulkActionItemStatus.SKIPPED,
                    "Entity not found",
                )

                log_repository.create(
                    bulk_action.id,
                    item.contact_id,
                    LogStatus.SKIPPED,
                    "Entity not found",
                )

                db.commit()
                skipped += 1
                continue

            try:

                # Commit per item, not once for the whole batch: SQLAlchemy
                # defers constraint checks (e.g. the unique email index) to
                # flush time, so a single bad item's IntegrityError would
                # otherwise only surface at a shared end-of-batch commit -
                # and take every other already-"succeeded" item in the
                # batch down with it. (A begin_nested()/SAVEPOINT-per-item
                # approach was tried first; on this SQLAlchemy version a
                # flush failure inside one leaves the whole Session
                # unusable for the rest of the batch, not just that item -
                # a real per-item commit/rollback is what actually isolates
                # failures correctly.)
                for field, value in payload.items():

                    if field not in updatable_fields:
                        continue

                    setattr(entity, field, value)

                item_repository.mark_item_result(
                    item.id,
                    BulkActionItemStatus.SUCCESS,
                )

                log_repository.create(
                    bulk_action.id,
                    entity.id,
                    LogStatus.SUCCESS,
                    "Updated successfully",
                )

                db.commit()
                succeeded += 1

            except Exception as e:

                db.rollback()

                logger.warning(
                    "item_failed",
                    item_id=item.id,
                    entity_id=entity.id,
                    error=str(e),
                )

                item_repository.mark_item_result(
                    item.id,
                    BulkActionItemStatus.FAILED,
                    str(e),
                )

                log_repository.create(
                    bulk_action.id,
                    entity.id,
                    LogStatus.FAILED,
                    str(e),
                )

                db.commit()
                failed += 1

        return BulkActionResult(
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
        )
