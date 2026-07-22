from collections.abc import Sequence

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import chunk_list
from app.enums.bulk_item_status import BulkActionItemStatus
from app.models.bulk_action_item import BulkActionItem


class BulkActionItemRepository:

    def __init__(self, db: Session):
        self.db = db

    def bulk_create(
        self,
        bulk_action_id: int,
        contact_ids: Sequence[int],
        chunk_size: int | None = None,
    ) -> int:
        """
        Snapshot the bulk action's targets into `bulk_action_items`, one row
        per contact, all starting QUEUED. Inserted in chunks rather than one
        statement for all rows, since a single job can target ~1M contacts.
        """
        chunk_size = chunk_size or settings.BATCH_SIZE
        created = 0

        for batch in chunk_list(list(contact_ids), chunk_size):
            rows = [
                {
                    "bulk_action_id": bulk_action_id,
                    "contact_id": contact_id,
                    "status": BulkActionItemStatus.QUEUED,
                }
                for contact_id in batch
            ]

            self.db.execute(insert(BulkActionItem), rows)
            created += len(rows)

        self.db.commit()

        return created

    def iter_id_batches(
        self,
        bulk_action_id: int,
        statuses: Sequence[BulkActionItemStatus] = (BulkActionItemStatus.QUEUED,),
        batch_size: int | None = None,
    ):
        """
        Keyset-paginate item IDs for a bulk action, oldest-id-first, never
        via OFFSET (this table can hold ~1M rows per job). Intended for the
        dispatcher to carve work into per-batch Celery tasks without loading
        every item into memory at once.
        """
        batch_size = batch_size or settings.BATCH_SIZE
        last_id = 0

        while True:
            ids = [
                row.id
                for row in (
                    self.db.query(BulkActionItem.id)
                    .filter(
                        BulkActionItem.bulk_action_id == bulk_action_id,
                        BulkActionItem.status.in_(statuses),
                        BulkActionItem.id > last_id,
                    )
                    .order_by(BulkActionItem.id)
                    .limit(batch_size)
                    .all()
                )
            ]

            if not ids:
                return

            yield ids

            if len(ids) < batch_size:
                return

            last_id = ids[-1]

    def get_by_ids(
        self,
        bulk_action_id: int,
        item_ids: Sequence[int],
    ) -> list[BulkActionItem]:
        """Fetch a specific batch's item rows, scoped to the bulk action."""
        return (
            self.db.query(BulkActionItem)
            .filter(
                BulkActionItem.bulk_action_id == bulk_action_id,
                BulkActionItem.id.in_(item_ids),
            )
            .all()
        )

    def bulk_mark_status(
        self,
        item_ids: Sequence[int],
        status: BulkActionItemStatus,
    ) -> None:
        """Uniform status update for a whole batch (e.g. QUEUED -> RUNNING)."""
        if not item_ids:
            return

        self.db.query(BulkActionItem).filter(
            BulkActionItem.id.in_(item_ids)
        ).update(
            {"status": status},
            synchronize_session=False,
        )

    def mark_item_result(
        self,
        item_id: int,
        status: BulkActionItemStatus,
        error_message: str | None = None,
    ) -> None:
        """
        Per-item terminal status update with its own error message. Bumps
        attempt_count so retried deliveries can tell how many times this
        specific item has been attempted.
        """
        self.db.query(BulkActionItem).filter(
            BulkActionItem.id == item_id
        ).update(
            {
                "status": status,
                "error_message": error_message,
                "attempt_count": BulkActionItem.attempt_count + 1,
            },
            synchronize_session=False,
        )

    def commit(self):
        self.db.commit()