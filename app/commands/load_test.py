"""
Simple load test: create one bulk_update targeting N existing contacts
against a running API + Celery worker, poll /progress until COMPLETED,
and report throughput (entities/minute).

Usage:
    python -m app.commands.load_test [entity_count]

Requires uvicorn and a Celery worker already running (this hits the real
HTTP API, not the service layer directly - it's measuring the whole
pipeline: API -> dispatch -> batch fan-out -> handler -> stats).
"""
import sys
import time

import httpx2 as httpx
from sqlalchemy import text

from app.core.database import engine

BASE_URL = "http://localhost:8000"
DEFAULT_ENTITY_COUNT = 5000
POLL_INTERVAL_SECONDS = 1


def get_contact_ids(limit: int) -> list[int]:
    conn = engine.connect()
    try:
        rows = conn.execute(
            text("SELECT id FROM contacts ORDER BY id LIMIT :limit"),
            {"limit": limit},
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def run(entity_count: int) -> None:
    contact_ids = get_contact_ids(entity_count)

    if not contact_ids:
        print("No contacts found - run `python -m app.commands.seed_contacts` first.")
        return

    print(f"Targeting {len(contact_ids)} contacts")

    client = httpx.Client(base_url=BASE_URL, timeout=30)

    start = time.perf_counter()

    response = client.post(
        "/bulk-actions",
        json={
            "action_type": "bulk_update",
            "entity_type": "contact",
            "entity_ids": contact_ids,
            "payload": {"status": "ACTIVE"},
        },
    )
    response.raise_for_status()
    bulk_action_id = response.json()["id"]
    print(f"Created bulk action {bulk_action_id}")

    while True:
        progress = client.get(f"/bulk-actions/{bulk_action_id}/progress").json()
        status = progress["status"]
        percent = progress["percent_complete"]
        print(f"  status={status} percent_complete={percent}%")

        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            break

        time.sleep(POLL_INTERVAL_SECONDS)

    elapsed = time.perf_counter() - start

    stats = client.get(f"/bulk-actions/{bulk_action_id}/stats").json()

    print()
    print(f"Elapsed: {elapsed:.2f}s")
    print(
        f"total={stats['total']} succeeded={stats['succeeded']} "
        f"failed={stats['failed']} skipped={stats['skipped']}"
    )

    if elapsed > 0:
        entities_per_minute = (stats["processed"] / elapsed) * 60
        print(f"Throughput: {entities_per_minute:.0f} entities/minute")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ENTITY_COUNT
    run(count)
