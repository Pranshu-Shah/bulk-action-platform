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
| GET    | `/bulk-actions`                    | Paginated list, filterable by status/action_type/account_id, sortable |
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

For running this locally. For deploying it to a real environment, see
[DEPLOYMENT.md](DEPLOYMENT.md).

Requires a local PostgreSQL and Redis instance.

```
DATABASE_URL=postgresql://<user>:<password>@localhost:5432/<db>
REDIS_URL=redis://localhost:6379/0
BATCH_SIZE=500
LOG_LEVEL=INFO
JSON_LOGS=false
RATE_LIMIT_PER_MINUTE=10000
```

Put those in `.env` (everything after `BATCH_SIZE` is optional and
defaults to the value shown).

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

59 tests: unit tests for the service's validation/cancellation/scheduling
logic, each action handler, de-duplication, and rate limiting, plus
integration tests exercising the full HTTP API end to end. Tests run
against the same Postgres used for local
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
- Still effectively single-tenant in practice: `account_id` exists on
  `bulk_actions` and powers rate limiting (see "Optional enhancements"),
  but it's just an unauthenticated integer supplied in the request body
  — no accounts/users table, no verification that the caller actually
  owns that account. Rate limiting works, but isn't a real security
  boundary until an auth layer exists to back it.
- No authentication or authorization on any endpoint — the `account_id`
  caveat above is a direct consequence of this.

## Optional enhancements

All three enhancements beyond the core spec are implemented. Scheduling
is opt-in via a request field that defaults to "off"; de-duplication
runs automatically whenever an entity type defines a dedup key, no field
needed. Rate limiting is the one **mandatory** field (see below) - every
request must declare an `account_id`. 16 additional tests cover them
(`tests/unit/test_rate_limiter.py`, `tests/unit/test_deduplication.py`,
`TestScheduling` in `tests/unit/test_bulk_action_service.py`).

### Rate limiting (per-account, N events/minute)

`BulkActionCreate.account_id: int` is **required** on every request -
not optional. An earlier version made it opt-in, but that defeats the
spec's own intent ("no account should be able to exceed a rate limit"):
if omitting the field just skips rate limiting entirely, the limit isn't
actually enforced on anyone who doesn't choose to participate.
Requiring it at the HTTP boundary closes that gap - every external
caller must declare an account. (`BulkActionService.create_bulk_action`
keeps `account_id` optional at the Python level, so internal/service-
level callers aren't forced through the same gate - only the untrusted
public API enforces it.)

`process_bulk_action_batch` checks a Redis-backed fixed-window counter
(`app/core/rate_limiter.py`, `RateLimiter.try_consume`) before
processing each batch, reserving `len(pending)` units of that account's
current-minute budget (`RATE_LIMIT_PER_MINUTE`, default 10,000). The
check happens *before* `item_repo.bulk_mark_status(..., RUNNING)` -
otherwise a rate-limited batch would leave its items stuck in `RUNNING`
while waiting for the next window. If the reservation would exceed
budget, it's rolled back (so a denied request doesn't still eat into
the budget) and the batch backs off:

```python
if bulk_action.account_id is not None:
    if len(pending) > limiter.limit_per_minute:
        # can never fit, no matter how many times it retries - fail fast
        ...mark all pending items FAILED, update stats, return...
    if not limiter.try_consume(bulk_action.account_id, len(pending)):
        raise self.retry(countdown=60, max_retries=None)
```

Two things worth calling out, both found by testing this against its
own edge cases rather than just the happy path:

1. **A batch permanently larger than the limit must fail fast, not
   retry forever.** The first version only had the `try_consume` check -
   if a single batch's size (`BATCH_SIZE`) itself exceeds
   `RATE_LIMIT_PER_MINUTE`, *every* retry attempt reserves the same
   too-large amount against a freshly-reset budget and gets denied again,
   forever, with no error ever surfaced - a bulk action silently stuck
   for good. The size check above catches this specific case and fails
   those items immediately with a clear message instead. Configure
   `BATCH_SIZE <= RATE_LIMIT_PER_MINUTE` for any account that will
   actually be rate-limited, to stay in the "temporarily over budget,
   retries and succeeds later" case rather than this one.
2. `max_retries=None` on the genuine retry call matters: the task's own
   `retry_kwargs={"max_retries": 3}` is what governs actual processing
   errors (via `autoretry_for`), but a sustained-but-eventually-clearing
   rate limit should keep backing off indefinitely rather than giving up
   and marking the batch `FAILED` after 3 minutes.

**Honest caveat**: there's still no authentication layer, so
`account_id`, while now mandatory, is entirely client-supplied and
unverified - a caller can still supply any account_id it wants,
including someone else's. Making it required stops the "just omit it"
bypass, but this isn't a real security boundary until an auth layer
exists and `account_id` is derived from a verified identity instead of
accepted at face value.

### De-duplication by email

`BaseEntityRepository` gained one more method, with a default that
opts an entity type out entirely: `get_dedup_key(entity) -> object |
None`. `ContactRepository` returns `entity.email`. In
`dispatch_bulk_action`, the same pass that checks which `entity_ids`
actually exist also tracks dedup keys seen so far
(`app/core/utils.register_or_flag_duplicate`); the first entity for a
given key proceeds normally, any later one in the same request is
logged `SKIPPED` ("Duplicate entity...") with no item row created - the
same treatment a missing ID already gets, just a different reason.

**Testing note worth knowing**: `contacts.email` has a real database
unique constraint, so two genuine contacts can never actually share an
email - there's no way to construct that scenario with real rows. The
core algorithm is unit-tested directly against plain fake objects
(sidestepping the constraint entirely), and the full
`dispatch_bulk_action` wiring is proven with a test repository that
forcibly reports a duplicate key for two real contacts regardless of
their actual (distinct) emails - confirming the end-to-end log/skip/stats
behavior without fighting the database's own guarantee.

### Scheduling

`BulkActionCreate.scheduled_at: datetime | None = None`. In
`create_bulk_action`, a future timestamp starts the action as
`SCHEDULED` instead of `QUEUED` and dispatches via Celery's
`apply_async(eta=scheduled_at)` instead of `.delay()`; a past or omitted
timestamp behaves exactly as before. This was the cheapest of the three
to build: `BulkActionStatus.SCHEDULED` and its place in
`CANCELLABLE_STATUSES` already existed, and `dispatch_bulk_action`
already checks `if bulk_action.status == CANCELLED: return` as its very
first line - so cancelling a scheduled-but-not-yet-started action
already worked correctly the moment `eta` fires, no new code needed
there.

**Testing note**: `task_always_eager=True` (used in the test suite)
makes Celery ignore `eta` and run immediately, so the test asserts on
the `apply_async` call arguments directly (via a monkeypatched stub)
rather than on real delayed execution.
