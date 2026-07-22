from sqlalchemy.orm import Session

from app.workers.tasks import dispatch_bulk_action
from app.actions.registry import ActionRegistry
from app.core.exceptions import (
    BulkActionNotCancellableError,
    BulkActionNotFoundError,
    InvalidPayloadError,
    UnsupportedActionTypeError,
    UnsupportedEntityTypeError,
)
from app.entities.registry import EntityRegistry
from app.enums.bulk_status import BulkActionStatus
from app.enums.log_status import LogStatus
from app.models.bulk_action import BulkAction
from app.repositories.bulk_action_repository import BulkActionRepository
from app.repositories.bulk_action_stats_repository import BulkActionStatsRepository
from app.repositories.bulk_log_repository import BulkLogRepository

from app.actions.constants import (
    ASSIGN_OWNER_FIELD,
    FIELD_UPDATE_ACTIONS,
)

# A bulk action can only be cancelled while it hasn't reached a terminal
# state yet.
CANCELLABLE_STATUSES = (
    BulkActionStatus.QUEUED,
    BulkActionStatus.SCHEDULED,
    BulkActionStatus.RUNNING,
)


class BulkActionService:

    def __init__(self, db: Session):
        self.db = db
        self.repository = BulkActionRepository(db)
        self.stats_repository = BulkActionStatsRepository(db)
        self.log_repository = BulkLogRepository(db)

    def create_bulk_action(
        self,
        action_type: str,
        entity_type: str,
        entity_ids: list[int],
        payload: dict,
    ) -> BulkAction:

        # Validate action type - ActionRegistry is the source of truth for
        # what's supported, not a separately maintained constant.
        if action_type not in ActionRegistry.supported_actions():
            raise UnsupportedActionTypeError(action_type)

        # Validate entity type - same principle, via EntityRegistry.
        if entity_type not in EntityRegistry.supported_types():
            raise UnsupportedEntityTypeError(entity_type)

        # Payload requirements differ per action type: bulk_update needs a
        # set of entity fields to overwrite (which fields are updatable is
        # a fact about the entity type, asked of EntityRegistry rather
        # than hardcoded here); bulk_assign_owner needs an owner_id;
        # bulk_delete/bulk_archive/bulk_export need no payload at all, so
        # they're not validated here.
        if action_type in FIELD_UPDATE_ACTIONS:

            if not payload:
                raise InvalidPayloadError("Payload cannot be empty.")

            updatable_fields = EntityRegistry.get_repository(
                entity_type, self.db
            ).get_updatable_fields()

            valid_fields = [
                field
                for field in payload
                if field in updatable_fields
            ]

            if not valid_fields:
                raise InvalidPayloadError(
                    f"At least one valid field is required. Allowed fields: {sorted(updatable_fields)}"
                )

        elif action_type == "bulk_assign_owner":

            if ASSIGN_OWNER_FIELD not in payload:
                raise InvalidPayloadError(f"Payload must contain '{ASSIGN_OWNER_FIELD}'.")

            if not isinstance(payload[ASSIGN_OWNER_FIELD], int):
                raise InvalidPayloadError(f"'{ASSIGN_OWNER_FIELD}' must be an integer.")

        # Create Bulk Action record. Target contact IDs are not stored on
        # this row - they're passed straight to the dispatcher task, which
        # snapshots them into `bulk_action_items`.
        bulk_action = BulkAction(
            action_type=action_type,
            entity_type=entity_type,
            status=BulkActionStatus.QUEUED,
            payload=payload,
        )

        bulk_action = self.repository.create(bulk_action)

        # Send task to Celery
        dispatch_bulk_action.delay(
            bulk_action.id,
            entity_ids,
        )

        return bulk_action

    def get_bulk_action(self, bulk_action_id: int) -> BulkAction:

        bulk_action = self.repository.get(bulk_action_id)

        if not bulk_action:
            raise BulkActionNotFoundError(bulk_action_id)

        return bulk_action

    def list_bulk_actions(
        self,
        offset: int = 0,
        limit: int = 20,
        status: BulkActionStatus | None = None,
        action_type: str | None = None,
        sort: str = "desc",
    ):
        return self.repository.list(
            offset=offset,
            limit=limit,
            status=status,
            action_type=action_type,
            sort=sort,
        )

    def get_stats(self, bulk_action_id: int):

        self.get_bulk_action(bulk_action_id)

        stats = self.stats_repository.get(bulk_action_id)

        if not stats:
            # Not dispatched yet (or dispatch hasn't reached the stats-row
            # creation step) - a legitimate transient state, not an error.
            return {
                "bulk_action_id": bulk_action_id,
                "total": 0,
                "processed": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
            }

        return stats

    def get_progress(self, bulk_action_id: int):

        bulk_action = self.get_bulk_action(bulk_action_id)
        stats = self.stats_repository.get(bulk_action_id)

        total = stats.total if stats else 0
        processed = stats.processed if stats else 0
        percent_complete = round((processed / total) * 100, 2) if total else 0.0

        return {
            "bulk_action_id": bulk_action_id,
            "status": bulk_action.status,
            "total": total,
            "processed": processed,
            "percent_complete": percent_complete,
        }

    def cancel_bulk_action(self, bulk_action_id: int) -> BulkAction:

        bulk_action = self.get_bulk_action(bulk_action_id)

        if bulk_action.status not in CANCELLABLE_STATUSES:
            raise BulkActionNotCancellableError(bulk_action_id, bulk_action.status)

        bulk_action.status = BulkActionStatus.CANCELLED
        self.repository.commit()

        return bulk_action

    def get_logs(
        self,
        bulk_action_id: int,
        after_id: int = 0,
        limit: int = 100,
        status: LogStatus | None = None,
    ):

        self.get_bulk_action(bulk_action_id)

        return self.log_repository.get_logs(
            bulk_action_id,
            after_id=after_id,
            limit=limit,
            status=status,
        )
