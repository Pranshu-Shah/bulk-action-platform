import structlog

from app.actions.base import BaseBulkAction, BulkActionResult
from app.actions.constants import ASSIGN_OWNER_FIELD
from app.enums.bulk_item_status import BulkActionItemStatus
from app.enums.log_status import LogStatus
from app.repositories.bulk_action_item_repository import BulkActionItemRepository
from app.repositories.bulk_log_repository import BulkLogRepository

logger = structlog.get_logger(__name__)


class BulkAssignOwnerAction(BaseBulkAction):

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

        owner_id = payload[ASSIGN_OWNER_FIELD]

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
                entity.owner_id = owner_id

                item_repository.mark_item_result(
                    item.id,
                    BulkActionItemStatus.SUCCESS,
                )

                log_repository.create(
                    bulk_action.id,
                    entity.id,
                    LogStatus.SUCCESS,
                    f"Owner assigned: {owner_id}",
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
