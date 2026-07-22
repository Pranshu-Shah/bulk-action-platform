from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    BulkActionError,
    BulkActionNotCancellableError,
    BulkActionNotFoundError,
    InvalidPayloadError,
    UnsupportedActionTypeError,
    UnsupportedEntityTypeError,
)


async def not_found_handler(request: Request, exc: BulkActionNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def conflict_handler(request: Request, exc: BulkActionNotCancellableError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def bad_request_handler(request: Request, exc: BulkActionError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def register_exception_handlers(app):
    app.add_exception_handler(BulkActionNotFoundError, not_found_handler)
    app.add_exception_handler(BulkActionNotCancellableError, conflict_handler)
    app.add_exception_handler(UnsupportedActionTypeError, bad_request_handler)
    app.add_exception_handler(UnsupportedEntityTypeError, bad_request_handler)
    app.add_exception_handler(InvalidPayloadError, bad_request_handler)
    # Catch-all for any domain error not explicitly mapped above.
    app.add_exception_handler(BulkActionError, bad_request_handler)
