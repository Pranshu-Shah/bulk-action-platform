from sqlalchemy.orm import Session

from app.enums.bulk_status import BulkActionStatus
from app.models.bulk_action import BulkAction


class BulkActionRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(self, bulk_action: BulkAction):
        self.db.add(bulk_action)
        self.db.commit()
        self.db.refresh(bulk_action)
        return bulk_action

    def get(self, bulk_action_id: int):
        return (
            self.db.query(BulkAction)
            .filter(BulkAction.id == bulk_action_id)
            .first()
        )

    def list(
        self,
        offset: int = 0,
        limit: int = 20,
        status: BulkActionStatus | None = None,
        action_type: str | None = None,
        account_id: int | None = None,
        sort: str = "desc",
    ) -> tuple[list[BulkAction], int]:
        """
        Plain OFFSET pagination - unlike bulk_action_items/bulk_logs,
        bulk_actions has one row per job, not one per contact, so it never
        reaches a scale where OFFSET is a problem.
        """
        query = self.db.query(BulkAction)

        if status is not None:
            query = query.filter(BulkAction.status == status)

        if action_type is not None:
            query = query.filter(BulkAction.action_type == action_type)

        if account_id is not None:
            query = query.filter(BulkAction.account_id == account_id)

        total = query.count()

        descending = sort != "asc"
        order = BulkAction.id.desc() if descending else BulkAction.id.asc()

        items = (
            query.order_by(order)
            .offset(offset)
            .limit(limit)
            .all()
        )

        return items, total

    def update(self, bulk_action):
        self.db.add(bulk_action)

    def commit(self):
        self.db.commit()
