from fastapi import FastAPI

from app.api.bulk_actions import router as bulk_action_router
from app.api.exception_handlers import register_exception_handlers
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.middleware import RequestLoggingMiddleware

configure_logging(log_level=settings.LOG_LEVEL, json_logs=settings.JSON_LOGS)

app = FastAPI(
    title="Bulk Action Platform",
    version="1.0.0",
)
app.add_middleware(RequestLoggingMiddleware)
register_exception_handlers(app)
app.include_router(bulk_action_router)


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }
