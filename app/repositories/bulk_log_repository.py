from sqlalchemy.orm import Session

from app.enums.log_status import LogStatus
from app.models.bulk_log import BulkLog


class BulkLogRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        bulk_action_id: int,
        entity_id: int,
        status,
        message: str,
    ):
        log = BulkLog(
            bulk_action_id=bulk_action_id,
            entity_id=entity_id,
            status=status,
            message=message,
        )

        self.db.add(log)

    def get_logs(
        self,
        bulk_action_id: int,
        after_id: int = 0,
        limit: int = 100,
        status: LogStatus | None = None,
    ):
        """Cursor-paginated (never `.all()` unbounded — a job can log ~1M rows)."""
        query = self.db.query(BulkLog).filter(
            BulkLog.bulk_action_id == bulk_action_id,
            BulkLog.id > after_id,
        )

        if status is not None:
            query = query.filter(BulkLog.status == status)

        return (
            query
            .order_by(BulkLog.id)
            .limit(limit)
            .all()
        )