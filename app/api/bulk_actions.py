from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.enums.bulk_status import BulkActionStatus
from app.enums.log_status import LogStatus
from app.schemas.bulk_action import (
    BulkActionCreate,
    BulkActionProgressResponse,
    BulkActionResponse,
    BulkActionStatsResponse,
    BulkActionStatusResponse,
    PaginatedBulkActions,
    PaginatedBulkLogs,
)
from app.services.bulk_action_service import BulkActionService

router = APIRouter(
    prefix="/bulk-actions",
    tags=["Bulk Actions"],
)


@router.post(
    "",
    response_model=BulkActionResponse,
)
def create_bulk_action(
    request: BulkActionCreate,
    db: Session = Depends(get_db),
):

    service = BulkActionService(db)

    return service.create_bulk_action(
        action_type=request.action_type,
        entity_type=request.entity_type,
        entity_ids=request.entity_ids,
        payload=request.payload,
        account_id=request.account_id,
        scheduled_at=request.scheduled_at,
    )


@router.get(
    "",
    response_model=PaginatedBulkActions,
)
def list_bulk_actions(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: BulkActionStatus | None = None,
    action_type: str | None = None,
    account_id: int | None = None,
    sort: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):

    service = BulkActionService(db)

    items, total = service.list_bulk_actions(
        offset=offset,
        limit=limit,
        status=status,
        action_type=action_type,
        account_id=account_id,
        sort=sort,
    )

    return PaginatedBulkActions(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{bulk_action_id}",
    response_model=BulkActionStatusResponse,
)
def get_bulk_action(
    bulk_action_id: int,
    db: Session = Depends(get_db),
):

    service = BulkActionService(db)

    return service.get_bulk_action(
        bulk_action_id,
    )


@router.get(
    "/{bulk_action_id}/stats",
    response_model=BulkActionStatsResponse,
)
def get_stats(
    bulk_action_id: int,
    db: Session = Depends(get_db),
):

    service = BulkActionService(db)

    return service.get_stats(
        bulk_action_id,
    )


@router.get(
    "/{bulk_action_id}/progress",
    response_model=BulkActionProgressResponse,
)
def get_progress(
    bulk_action_id: int,
    db: Session = Depends(get_db),
):

    service = BulkActionService(db)

    return service.get_progress(
        bulk_action_id,
    )


@router.post(
    "/{bulk_action_id}/cancel",
    response_model=BulkActionResponse,
)
def cancel_bulk_action(
    bulk_action_id: int,
    db: Session = Depends(get_db),
):

    service = BulkActionService(db)

    return service.cancel_bulk_action(
        bulk_action_id,
    )


@router.get(
    "/{bulk_action_id}/logs",
    response_model=PaginatedBulkLogs,
)
def get_logs(
    bulk_action_id: int,
    after_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status: LogStatus | None = None,
    db: Session = Depends(get_db),
):

    service = BulkActionService(db)

    logs = service.get_logs(
        bulk_action_id,
        after_id=after_id,
        limit=limit,
        status=status,
    )

    next_after_id = logs[-1].id if len(logs) == limit else None

    return PaginatedBulkLogs(
        items=logs,
        next_after_id=next_after_id,
    )
