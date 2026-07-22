# Bulk Action Platform

A scalable bulk-action platform for a CRM application. It runs bulk
operations — update, delete, archive, assign-owner, export — against
CRM entities, built to handle up to roughly a million targets in a
single job with parallel processing, detailed per-entity logging, and
robust error handling. The reference entity implemented end-to-end is
**Contact**, but the architecture is deliberately entity-agnostic: adding
a new entity type or a new bulk action is a matter of registering one
new class, not modifying the pipeline.

## Tech stack

- **FastAPI + Pydantic v2** — API layer and request/response validation, with Swagger/OpenAPI docs served at `/docs`.
- **PostgreSQL + SQLAlchemy 2.0 + Alembic** — persistence and schema migrations.
- **Celery + Redis** — the asynchronous queue and worker layer that does the actual bulk processing.
- **structlog** — structured, contextual logging across the API and workers.
- **pytest** — unit and integration tests, run against a real Postgres instance.

No Docker: Postgres and Redis run as native local services, and the
app/tests connect to them directly via `.env`.

## Architecture

Strictly layered, one direction of dependency: **API → Service →
Repository → Model**. The service layer never raises `HTTPException` —
it raises domain exceptions (`app/core/exceptions.py`), and the API
layer translates those to HTTP responses in exactly one place
(`app/api/exception_handlers.py`), rather than scattering try/except
across endpoints.

```
POST /bulk-actions
      |
      v
BulkActionService.create_bulk_action()      (validates via ActionRegistry.supported_actions() /
                                              EntityRegistry.supported_types(), creates the BulkAction row)
      |
      v
dispatch_bulk_action.delay()                (Celery task)
      |
      +--> EntityRegistry.get_repository(entity_type) checks which entity_ids actually exist
      +--> snapshots the found IDs into bulk_action_items
      +--> creates the BulkActionStats row
      +--> chunks items into N batches, enqueues one task per batch
      |
      v
process_bulk_action_batch.delay()  x N      (Celery tasks, run in parallel across workers)
      |
      +--> marks its batch's items RUNNING
      +--> EntityRegistry.get_repository(entity_type) resolves the entities
      +--> ActionRegistry.get_action(action_type) resolves the handler, which processes
      |    each item and commits per item
      +--> updates BulkActionStats; flips the action to COMPLETED once fully processed
```

The API layer only ever does cheap, fast work: validate the request,
write one row, hand off to a Celery task. Every expensive step —
touching up to a million entities — happens asynchronously, fanned out
across workers, with every stage explicitly chunked so nothing ever
loads a full job into memory at once.

### Dispatcher + parallel batch workers, not one task per job

A single bulk action is never processed by one task end to end. The
**dispatcher** task's only job is to snapshot the targets and split the
work into batches; the actual processing happens in
`process_bulk_action_batch`, and many of these run concurrently across
however many Celery workers are online. This is the horizontal-scaling
story: add more worker processes or machines consuming the same Redis
queue, and throughput increases with zero code changes.

### Item-level tracking, not a JSON array

`bulk_action_items` is a real table — one row per targeted entity per
bulk action — rather than a JSON array column on `bulk_actions`. This is
what makes batching, pagination, and per-item retry possible at scale: a
JSON array of up to a million IDs can't be indexed, paged, or partially
retried.

### Idempotency

Celery tasks auto-retry on any exception. Without item-level status
tracking, a retried batch would reprocess everything from scratch,
double-counting stats and duplicating logs. Instead, each item is marked
`RUNNING` before processing, and a batch worker skips any item already
in a terminal state (`SUCCESS` / `FAILED` / `SKIPPED`) on redelivery —
so a retry only touches the work that didn't finish.

### Per-item atomicity within a batch

Every action handler commits **after each item**, not once for the whole
batch. SQLAlchemy defers constraint checks (e.g. a unique-index
violation) to flush time, so a single bad item's `IntegrityError` at a
shared end-of-batch commit would silently revert every other
already-succeeded item in that same batch — the opposite of what
item-level idempotency is meant to guarantee. Committing per item
trades some throughput (more round-trips per batch) for a hard
correctness guarantee: one bad row can never take down its neighbors.
(A `begin_nested()`/SAVEPOINT-per-item approach was evaluated and
rejected — on this SQLAlchemy version, a flush failure inside a
savepoint leaves the whole `Session` unusable for the remainder of the
batch, which defeats the purpose. A real per-item `commit()`/`rollback()`
is what actually isolates failures correctly.)

### Action handlers are pluggable (Open-Closed)

`ActionRegistry` (`app/actions/registry.py`) maps an `action_type`
string to a `BaseBulkAction` implementation. Adding a new bulk action
means writing one new handler class and registering it — the
dispatcher, batch worker, and API layer never change.

| action_type          | Effect                                                              |
|----------------------|----------------------------------------------------------------------|
| `bulk_update`         | Overwrites the given fields (`name`/`email`/`status`/`age`) on each entity |
| `bulk_delete`         | **Soft delete** — sets `status="DELETED"` (see below)               |
| `bulk_archive`        | Sets `status="ARCHIVED"`                                             |
| `bulk_assign_owner`   | Sets `owner_id` (payload: `{"owner_id": <int>}`)                     |
| `bulk_export`         | Writes a snapshot of each entity into `bulk_logs` (see below)        |

### Entities are pluggable too (Open-Closed on the other axis)

`EntityRegistry` (`app/entities/registry.py`) maps an `entity_type`
string to a `BaseEntityRepository` implementation
(`app/entities/base.py`), which defines the interface every entity
repository must satisfy: `get_by_ids(ids)` and `get_updatable_fields()`.
`ContactRepository` implements this interface. The dispatcher, batch
worker, and every action handler resolve their target through
`EntityRegistry.get_repository(bulk_action.entity_type, db)` — none of
them import `Contact` directly. Adding a second entity type (Company,
Lead, ...) means writing one new repository implementing the same
interface and registering it; the dispatch, batching, and handler code
is untouched. This mirrors `ActionRegistry` exactly, but registers
repository *classes* rather than singleton instances, since a repository
is bound to a database session and can't be shared across requests the
way a stateless action handler can.

The one place this pluggability doesn't yet extend into the schema:
`bulk_action_items.contact_id` is a named column with a real foreign key
to `contacts` specifically. Supporting a second entity type for real
would mean either dropping that FK in favor of a generic `entity_id`
with integrity enforced at the application layer, or giving each entity
type its own item-tracking table behind a shared interface — a schema
decision intentionally left open until a second entity actually needs
it (see "Known limitations").

### Soft delete, not a hard delete

`bulk_action_items` is a permanent audit trail — rows are never removed,
by design, so that every bulk action's history remains queryable
indefinitely. Because of this, `bulk_delete` sets `status="DELETED"`
rather than issuing a real `DELETE`, which would violate the item
table's foreign key for any entity ever touched by any bulk action. This
uses the same mechanism as `bulk_archive`, just a different status
value.

### Domain exceptions, not HTTPException in the service layer

The service layer raises typed domain exceptions
(`BulkActionNotFoundError`, `UnsupportedActionTypeError`,
`InvalidPayloadError`, `BulkActionNotCancellableError`, ...), registered
globally with FastAPI's exception-handler mechanism
(`app/api/exception_handlers.py`) rather than caught per-endpoint. This
keeps the service layer transport-agnostic and keeps HTTP-status
decisions in exactly one place.

## Supported endpoints

| Method | Path                              | Purpose                                      |
|--------|------------------------------------|-----------------------------------------------|
| POST   | `/bulk-actions`                    | Create and dispatch a bulk action              |
| GET    | `/bulk-actions`                    | Paginated list, filterable by status/action_type, sortable |
| GET    | `/bulk-actions/{id}`               | Bulk action detail (status, timestamps)        |
| GET    | `/bulk-actions/{id}/stats`         | Success/failure/skipped counts                 |
| GET    | `/bulk-actions/{id}/progress`      | Status + percent complete                      |
| POST   | `/bulk-actions/{id}/cancel`        | Cooperative cancellation of a running/queued action |
| GET    | `/bulk-actions/{id}/logs`          | Per-entity outcome log, cursor-paginated, filterable by status |

Full request/response schemas are served live at `/docs` once the app is
running. A ready-to-import Postman collection is provided at
`postman/Bulk Action Platform.postman_collection.json`, with one example
request per action type (plus deliberate error-case examples) and every
list/logs filter/pagination parameter pre-filled.

## Setup

Requires a local PostgreSQL and Redis instance.

```
DATABASE_URL=postgresql://<user>:<password>@localhost:5432/<db>
REDIS_URL=redis://localhost:6379/0
BATCH_SIZE=500
LOG_LEVEL=INFO
JSON_LOGS=false
```

Put those in `.env` (`LOG_LEVEL`/`JSON_LOGS` are optional and default to
`INFO`/`false`).

```
pip install -r requirements.txt
alembic upgrade head
python -m app.commands.seed_contacts     # optional: seeds 5000 sample contacts
```

Run the API and a worker in separate processes:

```
uvicorn app.main:app --reload
celery -A app.workers.celery_app.celery worker --loglevel=info --pool=threads --concurrency=4
```

(`--pool=threads` on Windows, since Celery's default `prefork` pool
needs `os.fork()`.)

## Testing

```
pytest
```

43 tests: unit tests for the service's validation/cancellation logic and
each action handler, plus integration tests exercising the full HTTP API
end to end. Tests run against the same Postgres used for local
development — each test is wrapped in a real transaction
(SQLAlchemy 2.0's `join_transaction_mode="create_savepoint"`) that's
rolled back afterward, so nothing persists even though the application
code's own `session.commit()` calls execute for real against Postgres
throughout. Celery is configured with `task_always_eager=True` for the
test run, so `.delay()` calls execute the real dispatcher, batch worker,
and handler code inline against the real database — only the Redis
broker round-trip is skipped, which isn't part of what these tests are
verifying.

## Load testing

`python -m app.commands.load_test [entity_count]` creates one
`bulk_update` against `entity_count` existing contacts through the live
HTTP API, polls `/progress` until completion, and reports throughput.

Benchmark: **5000 entities, 50 batches, ~37 seconds, zero failures — ≈
8,200 entities/minute** on a single worker process at
`--concurrency=8`. This comfortably exceeds the target of processing
thousands of entities per minute, and scales further, linearly, by
adding more worker processes or machines against the same queue.

## Scaling notes

- **Keyset pagination, never OFFSET**, everywhere the row count scales
  with job size. `bulk_action_items` and `bulk_logs` can hold up to ~1M
  rows per job; both are paginated by `id > last_seen_id`, never
  `OFFSET`. `BulkActionRepository.list()` — the list of *bulk actions*
  themselves, one row per job, never per-entity — is the one place that
  deliberately uses plain OFFSET, since that table never reaches a scale
  where it matters.
- **Atomic `col = col + n` updates** for `BulkActionStats` counters,
  since many batch workers update the same bulk action's single stats
  row concurrently; a Python-side read-modify-write would lose updates
  under concurrency.
- **Existence checks run in chunks**, never as one query for the whole
  entity_ids list — bounded by `BATCH_SIZE` regardless of how large the
  job is.
- **Horizontal scaling is a deployment concern, not a code change**: add
  Celery worker processes/machines against the same Redis broker to
  increase throughput.

## Known limitations

- **Entity-agnostic at the code layer, not yet at the schema layer.**
  See "Entities are pluggable too" above — a second entity type would
  need a schema decision on `bulk_action_items.contact_id` first.
- `bulk_export` writes a field snapshot into `bulk_logs` rather than
  producing a downloadable file — no file storage layer or download
  endpoint exists yet.
- A "DELETED" entity (from `bulk_delete`) remains a normal row; nothing
  currently filters it out of future queries, so a later bulk action
  could still target it. No endpoint currently lists/browses entities
  directly, so this has no user-facing effect today.
- `idempotency_key` exists on `bulk_actions` (nullable, unique when
  present) but isn't yet enforced at creation time — duplicate
  submissions (double-click, network retry) aren't deduplicated.
- Single-tenant: no `account_id` on any table. This is an explicit scope
  boundary, noted here as the extension point multi-tenancy or
  per-account rate limiting would build on.
- No authentication or authorization on any endpoint.

## Optional enhancements — implementation plan

Three enhancements beyond the core spec were scoped in code-level detail
below, not yet implemented.

### Rate limiting (per-account, N events/minute)

**Schema**: add `account_id: Mapped[int]` (indexed, not nullable) to
`BulkAction` via a new Alembic migration. `BulkActionCreate` gains a
required `account_id: int` — no auth layer exists yet, so it's accepted
directly in the request body, per the spec's "add an accountId for each
bulk action."

**New component** — `app/core/rate_limiter.py`, a Redis-backed
fixed-window counter:

```python
import time
import redis
from app.core.config import settings

redis_client = redis.from_url(settings.REDIS_URL)

class RateLimiter:
    def __init__(self, limit_per_minute: int = 10_000):
        self.limit_per_minute = limit_per_minute

    def try_consume(self, account_id: int, count: int) -> bool:
        bucket = f"rate:{account_id}:{int(time.time() // 60)}"
        pipe = redis_client.pipeline()
        pipe.incrby(bucket, count)
        pipe.expire(bucket, 90)
        new_total, _ = pipe.execute()
        if new_total > self.limit_per_minute:
            redis_client.decrby(bucket, count)  # roll back the reservation
            return False
        return True
```

**Wiring** — in `process_bulk_action_batch`, the check must run
*before* `item_repo.bulk_mark_status(..., RUNNING)`, not after,
otherwise a rate-limited batch leaves its items stuck in `RUNNING` while
waiting for the next window:

```python
if not RateLimiter().try_consume(bulk_action.account_id, len(pending)):
    logger.info("batch_rate_limited", account_id=bulk_action.account_id)
    raise process_bulk_action_batch.retry(countdown=60)
```

The task already declares `autoretry_for=(Exception,)`, so this reuses
the existing retry machinery rather than adding a second one. The
increment-then-rollback-on-overflow pattern avoids a race where two
concurrent batches both read a stale count and both believe they have
budget.

### De-duplication by email

This slots into the existing missing-ID handling in
`dispatch_bulk_action`, which already separates "target exists" from
"target doesn't exist" before creating item rows — the exact shape
needed here.

**Interface addition** — `BaseEntityRepository` gains one more optional
method:

```python
def get_dedup_key(self, entity) -> object | None:
    """Return the value duplicates are detected by, or None if this entity type has none."""
    return None
```

`ContactRepository` overrides it: `return entity.email`.

**Dispatch logic**, replacing the current `found_ids`-only loop:

```python
seen_keys = set()
duplicate_ids = []

for chunk in chunk_list(entity_ids, settings.BATCH_SIZE):
    for entity in entity_repo.get_by_ids(chunk):
        dedup_key = entity_repo.get_dedup_key(entity)
        if dedup_key is not None and dedup_key in seen_keys:
            duplicate_ids.append(entity.id)
            continue
        if dedup_key is not None:
            seen_keys.add(dedup_key)
        found_ids.add(entity.id)
```

`duplicate_ids` then flows through the same log-and-skip path
`missing_ids` already uses, just with the message `"Duplicate email"`
instead of `"Entity not found"` — no new item row, no batch ever sees
it. Entity types with no natural dedup key (`get_dedup_key` returning
`None`) skip this check entirely.

### Scheduling

**Schema**: add `scheduled_at: Mapped[datetime | None]` (nullable,
timezone-aware) to `BulkAction`. `BulkActionCreate` gains optional
`scheduled_at: datetime | None = None`.

**Service change** — in `create_bulk_action`:

```python
if scheduled_at and scheduled_at > datetime.now(UTC):
    bulk_action.status = BulkActionStatus.SCHEDULED
    bulk_action = self.repository.create(bulk_action)
    dispatch_bulk_action.apply_async(args=[bulk_action.id, entity_ids], eta=scheduled_at)
else:
    bulk_action.status = BulkActionStatus.QUEUED
    bulk_action = self.repository.create(bulk_action)
    dispatch_bulk_action.delay(bulk_action.id, entity_ids)
```

This is the cheapest of the three by a wide margin, because two things
already exist and need zero changes: `BulkActionStatus.SCHEDULED` is
already a valid enum value with `CANCELLABLE_STATUSES` already including
it, and `dispatch_bulk_action` already checks
`if bulk_action.status == CANCELLED: return` as its very first line —
so cancelling a scheduled-but-not-yet-started action is already correct
behavior the moment the `eta` fires, with no new code.

**Testing note**: `task_always_eager=True` (used in the test suite)
makes Celery ignore `eta` and run immediately — a scheduling test would
assert on the `apply_async` call arguments (that `eta` was passed
correctly) rather than on real delayed execution.
