import structlog

from app.actions.base import BaseBulkAction, BulkActionResult
from app.enums.bulk_item_status import BulkActionItemStatus
from app.enums.log_status import LogStatus
from app.repositories.bulk_action_item_repository import BulkActionItemRepository
from app.repositories.bulk_log_repository import BulkLogRepository

logger = structlog.get_logger(__name__)


class BulkExportAction(BaseBulkAction):
    """
    Placeholder export: no file/storage layer or download endpoint exists
    yet, so "exporting" means writing each entity's snapshot into
    BulkLog rather than producing a downloadable file. Revisit once
    storage requirements are decided.
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
                snapshot = (
                    f"name={entity.name}, email={entity.email}, "
                    f"status={entity.status}, age={entity.age}"
                )

                item_repository.mark_item_result(
                    item.id,
                    BulkActionItemStatus.SUCCESS,
                )

                log_repository.create(
                    bulk_action.id,
                    entity.id,
                    LogStatus.SUCCESS,
                    f"Exported: {snapshot}",
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
