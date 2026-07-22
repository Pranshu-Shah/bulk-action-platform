from typing import Any

from pydantic import BaseModel, Field
from app.enums.bulk_status import BulkActionStatus
from app.enums.log_status import LogStatus

from datetime import datetime



class BulkActionCreate(BaseModel):
    action_type: str
    entity_type: str

    entity_ids: list[int] = Field(min_length=1)

    payload: dict[str, Any]


class BulkActionResponse(BaseModel):
    id: int
    status: str

    class Config:
        from_attributes = True

class BulkActionStatusResponse(BaseModel):

    id: int

    action_type: str
    entity_type: str

    status: BulkActionStatus

    started_at: datetime | None
    completed_at: datetime | None

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PaginatedBulkActions(BaseModel):
    items: list[BulkActionStatusResponse]
    total: int
    limit: int
    offset: int


class BulkActionStatsResponse(BaseModel):

    bulk_action_id: int

    total: int
    processed: int
    succeeded: int
    failed: int
    skipped: int

    class Config:
        from_attributes = True


class BulkActionProgressResponse(BaseModel):

    bulk_action_id: int
    status: BulkActionStatus

    total: int
    processed: int
    percent_complete: float

    class Config:
        from_attributes = True


class BulkLogResponse(BaseModel):

    entity_id: int

    status: LogStatus

    message: str

    class Config:
        from_attributes = True


class PaginatedBulkLogs(BaseModel):
    items: list[BulkLogResponse]
    next_after_id: int | None
