import structlog

from app.actions.base import BaseBulkAction, BulkActionResult
from app.actions.constants import DELETED_STATUS
from app.enums.bulk_item_status import BulkActionItemStatus
from app.enums.log_status import LogStatus
from app.repositories.bulk_action_item_repository import BulkActionItemRepository
from app.repositories.bulk_log_repository import BulkLogRepository

logger = structlog.get_logger(__name__)


class BulkDeleteAction(BaseBulkAction):
    """
    Soft delete: bulk_action_items.contact_id has a permanent FK to
    contacts (an audit table - rows are never removed), so physically
    deleting a contact that's ever been touched by any bulk action would
    always violate that FK. Sets status instead, same pattern as
    BulkArchiveAction.
    """

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

                # Commit per item - see BulkUpdateAction for why: a shared
                # end-of-batch commit means one bad item's failure would
                # revert every other already-"succeeded" item too.
                entity.status = DELETED_STATUS

                item_repository.mark_item_result(
                    item.id,
                    BulkActionItemStatus.SUCCESS,
                )

                log_repository.create(
                    bulk_action.id,
                    entity.id,
                    LogStatus.SUCCESS,
                    "Deleted successfully",
                )

                db.commit()
                succeeded += 1

            except Exception as e:

                db.rollback()

                logger.warning(
                    "item_failed",
                    item_id=item.id,
                    entity_id=item.contact_id,
                    error=str(e),
                )

                item_repository.mark_item_result(
                    item.id,
                    BulkActionItemStatus.FAILED,
                    str(e),
                )

                log_repository.create(
                    bulk_action.id,
                    item.contact_id,
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
