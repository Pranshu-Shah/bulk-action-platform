from sqlalchemy.orm import Session

from app.models.bulk_action_stats import BulkActionStats


class BulkActionStatsRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(self, bulk_action_id: int, total: int) -> BulkActionStats:
        stats = BulkActionStats(
            bulk_action_id=bulk_action_id,
            total=total,
        )
        self.db.add(stats)
        self.db.commit()
        return stats

    def get(self, bulk_action_id: int) -> BulkActionStats | None:
        return (
            self.db.query(BulkActionStats)
            .filter(BulkActionStats.bulk_action_id == bulk_action_id)
            .first()
        )

    def increment(
        self,
        bulk_action_id: int,
        succeeded: int = 0,
        failed: int = 0,
        skipped: int = 0,
    ) -> None:
        """
        SQL-side `col = col + n` update, safe under concurrent batch
        workers incrementing the same bulk action's single stats row -
        a Python read-modify-write here would lose updates across workers.
        """
        processed = succeeded + failed + skipped

        if not processed:
            return

        self.db.query(BulkActionStats).filter(
            BulkActionStats.bulk_action_id == bulk_action_id
        ).update(
            {
                "processed": BulkActionStats.processed + processed,
                "succeeded": BulkActionStats.succeeded + succeeded,
                "failed": BulkActionStats.failed + failed,
                "skipped": BulkActionStats.skipped + skipped,
            },
            synchronize_session=False,
        )
        self.db.commit()

    def is_complete(self, bulk_action_id: int) -> bool:
        stats = self.get(bulk_action_id)
        return bool(stats) and stats.processed >= stats.total
