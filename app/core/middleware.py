import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request, call_next):
        request_id = str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()

        try:
            response = await call_next(request)

        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)

            logger.error(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )

            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        response.headers["X-Request-ID"] = request_id

        return response
