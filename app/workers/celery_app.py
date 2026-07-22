from celery import Celery
from celery.signals import setup_logging

from app.core.config import settings
from app.core.logging import configure_logging

celery = Celery(
    "bulk_actions",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery.conf.update(
    task_track_started=True,
)

# Discover tasks
celery.autodiscover_tasks(["app.workers"])


@setup_logging.connect
def _configure_worker_logging(**kwargs):
    """
    Replaces Celery's own logging setup with ours, so worker logs go
    through the same structlog processors/formatter as the API - one
    consistent log shape everywhere, not two.
    """
    configure_logging(log_level=settings.LOG_LEVEL, json_logs=settings.JSON_LOGS)