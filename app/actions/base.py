from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.bulk_action import BulkAction
from app.models.bulk_action_item import BulkActionItem


@dataclass
class BulkActionResult:
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


class BaseBulkAction(ABC):

    @abstractmethod
    def execute(
        self,
        db: Session,
        items: list[BulkActionItem],
        entities_by_id: dict[int, Any],
        payload: dict,
        bulk_action: BulkAction,
    ) -> BulkActionResult:
        """
        Process one already-RUNNING batch of items. `entities_by_id` maps
        item.contact_id -> the resolved entity, fetched through
        EntityRegistry for bulk_action.entity_type - never assume it's a
        Contact specifically. Implementations must mark each item's
        terminal status (success/failed/skipped) via
        BulkActionItemRepository and return the batch's outcome counts so
        the caller can update BulkActionStats.
        """
        pass
