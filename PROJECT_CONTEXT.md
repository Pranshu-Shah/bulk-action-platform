# PROJECT_CONTEXT.md — Handoff Notes for Claude Code

This project is being built incrementally with staged approvals. Read this
whole file before making changes — it captures decisions already made in a
prior planning conversation so they aren't re-litigated.

## Working rules (carried over, please continue following these)
- Work incrementally, one module/milestone at a time.
- Stop and summarize after each milestone for review before continuing.
- Explain architectural decisions, don't just produce code silently.
- Follow SOLID / clean architecture: API -> Service -> Repository -> Model.
  Services must not raise HTTPException directly (that's an existing bug —
  see "Known issues to fix" below) — services raise domain exceptions,
  the API layer translates them to HTTP responses.
- Prefer explicit, readable code over cleverness.

## Stack (already decided, do not change without discussion)
- FastAPI + Pydantic v2 (validation, Swagger via FastAPI's built-in OpenAPI)
- PostgreSQL + SQLAlchemy 2.0 (sync, psycopg2) + Alembic for migrations
- Celery + Redis for the queue/worker layer
- structlog for structured logging — **wired in for real as of Step 6**
  (see below), no longer just an unused dependency.
- pytest for testing — **42 tests exist as of Step 6** (unit + integration).
- **No Docker.** (Reversed 2026-07-22, see Step 6 below — `Dockerfile` and
  `docker-compose.yml` were removed.) Postgres/Redis run as native local
  installs; dev and tests connect to them directly via `DATABASE_URL`/
  `REDIS_URL` in `.env`.

## Scope decisions already made
- **Single-tenant for now.** No `account_id` on any table. This is a
  deliberate, explicit decision — document it as a "future extension
  point" in the README rather than silently building multi-tenancy in.
- **BulkActionStats is a separate table**, not denormalized counters on
  BulkAction (this was an explicit choice over the alternative).
- **Bulk action item targets are snapshotted at creation time** into a
  `bulk_action_items` table (one row per contact per bulk action) — NOT
  stored as a JSON array column. This was the single biggest fix from the
  code review (see below).

## What existed before this session (context on the codebase)
This repo started as a partially-built FastAPI + Celery bulk-actions
platform for managing Contacts (single entity type so far). A code review
was done and found:

**Good, kept as-is:**
- Layered folder structure (api/services/repositories/models) — real, not
  just named.
- `app/actions/` handler registry pattern (`BaseBulkAction` + `ActionRegistry`)
  is a correct Open-Closed implementation. Keep this pattern for new
  handlers (delete, assign_owner, export, archive — only `bulk_update`
  exists today).
- Celery/FastAPI/Alembic wiring, docker-compose Postgres/Redis services.

**Critical issues found (in priority order for fixing):**
1. `bulk_actions.entity_ids` was a JSON array column — not indexable,
   not paginable, forced full in-memory loading of up to 1M IDs.
   **FIX IN PROGRESS**: replaced with a real `bulk_action_items` table
   (see "Progress so far" below — this part is DONE).
2. `process_bulk_action` Celery task processed an ENTIRE bulk action
   serially in one task on one worker — no batch-level fan-out across
   workers. **NOT YET FIXED** — needs a dispatcher task (chunks items,
   enqueues N batch tasks) + a batch-worker task, per the plan below.
3. No idempotency — Celery's `autoretry_for=(Exception,), max_retries=3`
   means a failed task reprocesses ALL items from scratch on retry,
   double-counting stats and duplicating logs. **NOT YET FIXED** — needs
   each item's status checked before (re)processing it.
4. No pagination anywhere (`get_logs()` does `.all()` unbounded).
   **NOT YET FIXED**.
5. Service layer raises `HTTPException` directly (layering violation).
   **NOT YET FIXED**.
6. Only 3 of 7 planned API endpoints exist (create, get, get-logs — no
   list, stats, progress, cancel). **NOT YET FIXED**.
7. Only 1 of 5 planned handlers exists (bulk_update only).
   **NOT YET FIXED**.
8. Dockerfile and README are empty (0 bytes). docker-compose.yml only has
   postgres/redis, no app/worker services. **NOT YET FIXED**.
9. Zero tests exist. `app/commands/test_bulk_update.py` looks like a test
   but is a standalone broken script (passes `bulk_action=None` into a
   handler that does `bulk_action.success_count += 1` — will crash).
   **NOT YET FIXED**.
10. structlog is installed but never used. **NOT YET FIXED**.

## Progress so far (Step 1 of the migration plan — DONE)
Schema redesign, done in the prior session, already reflected in this
repo's files:
- Added `app/models/bulk_action_item.py` (`BulkActionItem`: bigint PK,
  `bulk_action_id` FK, `contact_id` FK, `status` enum, `error_message`,
  `attempt_count`).
- Added `app/models/bulk_action_stats.py` (`BulkActionStats`: one row per
  bulk action, `total`/`processed`/`succeeded`/`failed`/`skipped`
  counters, PK = FK to bulk_actions).
- Added `app/enums/bulk_item_status.py` (`BulkActionItemStatus`).
- Extended `app/enums/bulk_status.py` with `SCHEDULED`, `CANCELLED`.
- Updated `app/models/bulk_action.py`: removed `entity_ids` JSON column
  and the 5 denormalized counter columns, added `idempotency_key`
  (nullable, unique when present), added `items`/`stats` relationships.
- New Alembic migration:
  `alembic/versions/a1b2c3d4e5f6_item_level_tracking.py`, chained onto
  the existing head `6f0023417fda`. This migration was hand-written, not
  generated by `alembic revision --autogenerate`, because the authoring
  environment had no live DB connection.

  **Verified against a real Postgres (2026-07-22) — `alembic upgrade
  head` now runs clean, all four migrations apply in order.** It didn't
  on the first two attempts: the migration created the
  `bulk_action_item_status` enum type explicitly (for `checkfirst=True`
  safety), then also embedded that same type in the `bulk_action_items`
  column list passed to `op.create_table(...)` — which makes SQLAlchemy
  try to auto-create the type a *second* time during table creation,
  failing with `DuplicateObject`. Adding `create_type=False` didn't fix
  it on the first retry either, because that flag is silently ignored on
  the generic `sa.Enum` (it's not a real attribute there) — it only takes
  effect on the Postgres-specific `sqlalchemy.dialects.postgresql.ENUM`.
  Fixed by switching that column's type to `postgresql.ENUM(...,
  create_type=False)`, verified with a mock-engine DDL dry run before
  asking for a retry. Since the whole migration runs in one transaction,
  the two failed attempts rolled back cleanly with no manual cleanup
  needed.

**Known consequence of Step 1**: the following files still reference the
columns that were just removed (`entity_ids`, `total_records`, etc.) and
will currently break. This is expected — fixing them is Step 2 and Step 3
below, not a mistake to "undo":
- `app/repositories/contact_repository.py`
- `app/repositories/bulk_action_repository.py`
- `app/services/bulk_action_service.py`
- `app/schemas/bulk_action.py`
- `app/actions/bulk_update.py`
- `app/workers/tasks.py`

## Remaining migration plan (Steps 2-6)

**Step 2 — Repository layer rework — DONE**
- Added `app/repositories/bulk_action_item_repository.py`
  (`BulkActionItemRepository`): `bulk_create` snapshots contact IDs into
  `bulk_action_items` in chunks (via `app/core/utils.chunk_list`, Core
  `insert()` so per-row defaults apply); `iter_id_batches` keyset-paginates
  item IDs (never OFFSET) for the future dispatcher; `get_by_ids` fetches
  a specific batch's rows scoped to the bulk action; `bulk_mark_status`
  does a uniform status update across a batch (e.g. QUEUED -> RUNNING);
  `mark_item_result` does a per-item terminal update (status, error
  message, `attempt_count + 1`) for idempotent retry tracking.
- Reworked `ContactRepository.get_contacts_in_batches`: now takes
  `bulk_action_id` (+ optional `statuses`, `batch_size`) instead of a raw
  `entity_ids` list, and keyset-paginates a LEFT JOIN of
  `bulk_action_items` to `contacts`. Yields `(BulkActionItem, Contact |
  None)` pairs per batch. Deliberately a LEFT JOIN, not INNER: an item
  whose `contact_id` no longer resolves still needs to surface (as
  `Contact is None`) so a future handler can mark it SKIPPED, instead of
  the old "diff the ID set after the loop" approach silently depending on
  a full in-memory list.
- `BulkLogRepository.get_logs` now takes `after_id`/`limit` and orders by
  id instead of unbounded `.all()`.
- **Not touched in this step (still broken, as noted above — Steps 3-5
  will fix):** `BulkActionRepository` (no changes needed for this step),
  `bulk_action_service.py`, `bulk_update.py`, `workers/tasks.py`,
  `schemas/bulk_action.py` — these still reference `entity_ids` /
  `total_records` / etc. and still won't run end-to-end yet. That wiring
  is Steps 3-5, not a regression from this step.

**Step 3 — Queue/worker engine rewrite (the core fix) — DONE**
- `app/workers/tasks.py` rewritten: `process_bulk_action` is gone,
  replaced by `dispatch_bulk_action(bulk_action_id, entity_ids)` (marks
  the action RUNNING, calls `BulkActionItemRepository.bulk_create` to
  snapshot targets into `bulk_action_items`, creates the `BulkActionStats`
  row with `total`, then loops `iter_id_batches` enqueueing one
  `process_bulk_action_batch.delay(bulk_action_id, batch_ids)` per chunk)
  and `process_bulk_action_batch(bulk_action_id, batch_item_ids)` (loads
  that batch's items, filters out anything already terminal, marks the
  rest RUNNING, resolves their contacts, calls the registered action,
  then atomically increments `BulkActionStats` and flips the bulk action
  to COMPLETED once `processed >= total`).
- Idempotency: `TERMINAL_ITEM_STATUSES = (SUCCESS, FAILED, SKIPPED)` -
  `process_bulk_action_batch` skips any item already in one of these
  (handles Celery's `autoretry_for=(Exception,)` re-delivering a
  partially-completed batch without double-counting stats/logs).
- Cooperative cancellation: both tasks check
  `bulk_action.status == CANCELLED` and bail before doing work.

  **Two decisions made along the way, beyond the literal bullet list
  above:**
  1. **Pulled forward Step 4's interface change, not its full scope.**
     Meaningful per-item idempotency requires the action handler to
     operate on `BulkActionItem` rows (so it can mark each one's terminal
     status itself), not a raw contact-ID list - so `BaseBulkAction.execute()`
     was changed now to `execute(db, items, contacts_by_id, payload,
     bulk_action) -> BulkActionResult(succeeded, failed, skipped)`, and
     `BulkUpdateAction` was ported to it (including the "contact not
     found" case, now a LEFT JOIN miss - `contacts_by_id.get(item.contact_id)
     is None` - instead of the old post-loop set-difference). What's
     **still open** from Step 4: the four new handlers (delete,
     assign_owner, archive, export) are not added yet.
  2. **Added `BulkActionStatsRepository`** (not explicitly listed in the
     plan text, but required the moment `BulkAction`'s counter columns
     were removed in Step 1 in favor of the separate stats table -
     otherwise nothing would ever populate it). `increment()` does a
     SQL-side `col = col + n` update rather than Python read-modify-write,
     since multiple batch workers update the same bulk action's one stats
     row concurrently.
- **Minimal, narrow fix to `bulk_action_service.py`** (not the full Step 5
  rework): `create_bulk_action` no longer constructs `BulkAction(...)`
  with the removed `entity_ids`/`total_records` kwargs, and now calls
  `dispatch_bulk_action.delay(bulk_action.id, entity_ids)` instead of the
  deleted `process_bulk_action` task. This was necessary just to make the
  new dispatcher reachable at all - it does **not** touch the
  `HTTPException`-raising (still there, still Step 5) or add any
  endpoints.
- **Still broken, unchanged, expected** - Step 5 territory:
  `app/schemas/bulk_action.py` (`BulkActionStatusResponse` still reads
  `total_records`/`processed_records`/`success_count`/etc. off `BulkAction`,
  which no longer has them - `GET /bulk-actions/{id}` will 500 until the
  schema is repointed at `BulkActionStats`), and `app/api/bulk_actions.py`
  (missing list/stats/progress/cancel endpoints). `POST /bulk-actions`
  (create) and the Celery dispatch/batch pipeline are unaffected by that
  and should work end-to-end once run against a real Postgres/Redis.

**Step 4 — Handler interface update — DONE**
(Interface change + `BulkUpdateAction` port already landed in Step 3,
out of necessity. This step added the remaining four handlers.)
- `app/actions/bulk_delete.py` (`BulkDeleteAction`): `db.delete(contact)`
  per item; missing contact -> SKIPPED.
- `app/actions/bulk_archive.py` (`BulkArchiveAction`): sets
  `contact.status = ARCHIVED_STATUS` ("ARCHIVED", new constant in
  `app/actions/constants.py`) - fits the existing unconstrained
  `String(50)` status column, no schema change needed.
- `app/actions/bulk_assign_owner.py` (`BulkAssignOwnerAction`): sets
  `contact.owner_id = payload["owner_id"]`.
  **Required a schema change** (confirmed with the user first): `Contact`
  had no owner concept at all. Added a nullable, indexed `owner_id`
  (plain `Integer`, no FK - no users/owners table exists, single-tenant
  scope decision still holds) via
  `alembic/versions/c3d4e5f6a7b8_add_contact_owner_id.py`, chained onto
  `a1b2c3d4e5f6` (now head). Hand-written like the previous migration,
  but **verified clean** as part of the same `alembic upgrade head` run
  that fixed `a1b2c3d4e5f6` (see note above) - plain `ADD COLUMN` + index,
  no issues. `alembic heads`/`alembic history` confirm a single linear
  chain, no forks.
- `app/actions/bulk_export.py` (`BulkExportAction`): **confirmed
  log-only with the user** - no file/storage layer or download endpoint
  exists, so it writes each contact's field snapshot into `BulkLog`
  instead of producing a downloadable file. Explicitly a placeholder,
  documented as such in the class docstring; revisit once storage/export
  requirements are actually decided.
- All four registered in `ActionRegistry.actions`.

  **Necessary follow-on fix, same "keep it reachable" principle as
  Step 3's service touch:** `app/actions/constants.py` gained
  `FIELD_UPDATE_ACTIONS = {"bulk_update"}`,
  `NO_PAYLOAD_ACTIONS = {"bulk_delete", "bulk_archive", "bulk_export"}`,
  and `ASSIGN_OWNER_FIELD = "owner_id"`. `bulk_action_service.py`'s
  payload validation (still raising `HTTPException` directly - that move
  to domain exceptions is still Step 5, untouched here) now branches per
  action type instead of universally requiring an `UPDATABLE_FIELDS`
  match: `bulk_update` keeps the old "non-empty payload + at least one
  updatable field" rule; `bulk_assign_owner` requires an integer
  `owner_id`; `bulk_delete`/`bulk_archive`/`bulk_export` require nothing.
  Without this, the three no-payload actions and assign_owner would have
  been un-creatable through the API despite having working handlers.

## Manual verification findings (2026-07-22, before Step 5)

End-to-end testing (real Postgres/Redis/Celery worker, seeded contacts)
surfaced two real bugs in the Step 2-4 work, both now fixed:

1. **Missing-contact IDs crashed the dispatcher, not the batch worker.**
   `bulk_action_items.contact_id` has a real FK to `contacts` (from
   Step 1, predates this refactor). The Step 2/3/4 design assumed a
   missing contact would surface as a LEFT JOIN miss *during batch
   processing* (`contacts_by_id.get(item.contact_id) is None`) - but you
   can't even create an item row for a contact_id that doesn't exist, so
   `BulkActionItemRepository.bulk_create` failed immediately on
   `IntegrityError` before any of that logic ran.
   **Fix**: `dispatch_bulk_action` (`app/workers/tasks.py`) now checks
   which requested `entity_ids` actually exist - chunked (never one
   query for up to ~1M ids) via `ContactRepository.get_contacts_by_ids`
   - *before* calling `bulk_create`. IDs that don't exist get a `SKIPPED`
   `bulk_log` entry written directly, with no item row ever created for
   them, and count toward `stats.skipped` immediately. The
   LEFT-JOIN-miss handling in the four action handlers is **not** dead
   code from this - it's still the correct defensive path for a contact
   deleted *during* processing (a real race with concurrent bulk
   actions), just no longer reachable for "this ID never existed."
2. **The dispatcher's failure-recovery path didn't roll back before
   re-querying.** `dispatch_bulk_action`'s `except Exception:` block
   tried to mark the bulk action `FAILED` by querying the database again
   on the *same* session that had just thrown - but Postgres poisons a
   transaction after any error, so that recovery query itself failed
   with `InFailedSqlTransaction`, silently defeating the FAILED-marking
   entirely. Fixed by adding `db.rollback()` before the recovery query.
   This was independent of bug #1 and more serious in effect: without
   it, **any** DB-level failure during dispatch left the action stuck in
   `RUNNING` forever instead of `FAILED`.
3. **`BulkDeleteAction`'s hard `db.delete(contact)` always violates the
   same FK**, for any contact ever touched by any bulk action - item rows
   are a permanent audit trail, never deleted, so the FK blocks deleting
   their referenced contact indefinitely. **Confirmed with the user**:
   made it a soft delete - `contact.status = DELETED_STATUS` ("DELETED",
   new constant in `app/actions/constants.py`), same pattern as
   `BulkArchiveAction`'s `ARCHIVED_STATUS`. No schema change. (Considered
   and rejected: relaxing the FK to nullable + `ON DELETE SET NULL`,
   which would make deletes "real" but silently null out
   `bulk_action_items.contact_id` for *any* historical item referencing
   that contact, including from unrelated past bulk actions - a bigger,
   more destructive change for a feature not yet built beyond this one
   handler.)

**Known consequence of the soft-delete choice, not yet addressed
anywhere**: a "DELETED" contact is still a normal row - `ContactRepository`
methods don't filter it out, so a later bulk action can still target and
act on a "deleted" contact. No endpoint currently lists/browses contacts,
so this has no user-facing effect yet, but worth remembering if a contact
list/filter endpoint is ever added.

**All 11 manual test cases passed after the three fixes above** (9 happy/
edge-case scenarios covering all five action types + missing-contact
handling + multi-batch fan-out, plus 2 negative-validation checks for
unsupported action type and empty payload). Steps 2-4 are considered
verified against a real Postgres/Redis/Celery worker as of 2026-07-22.

**Step 5 — Service + API layer — DONE**
- New `app/core/exceptions.py`: `BulkActionError` (base) and
  `BulkActionNotFoundError`, `UnsupportedActionTypeError`,
  `UnsupportedEntityTypeError`, `InvalidPayloadError`,
  `BulkActionNotCancellableError`. `bulk_action_service.py` no longer
  imports `fastapi` at all - every `raise HTTPException(...)` became a
  domain exception.
- New `app/api/exception_handlers.py`, wired into `app/main.py` via
  `register_exception_handlers(app)` (FastAPI's global
  `add_exception_handler`, not per-endpoint try/except): `NotFound` -> 404,
  `NotCancellable` -> 409, everything else (`UnsupportedActionType`,
  `UnsupportedEntityType`, `InvalidPayload`, and a catch-all for the base
  `BulkActionError`) -> 400. This is the actual API-layer-translates-
  domain-exceptions boundary the project rules asked for.
- `schemas/bulk_action.py`: `BulkActionStatusResponse` repointed - no
  longer reads the removed `total_records`/`processed_records`/
  `success_count`/etc. off `BulkAction`. Added
  `BulkActionStatsResponse` (full breakdown, backed by
  `BulkActionStats` - or a zeroed stand-in if the action hasn't been
  dispatched yet, which is a legitimate transient state, not a 404),
  `BulkActionProgressResponse` (status + total + processed +
  `percent_complete`, a lighter-weight computed view distinct from the
  full stats endpoint), `PaginatedBulkActions`, `PaginatedBulkLogs`.
- `BulkActionRepository.list(...)`: added, plain OFFSET pagination -
  deliberately *not* keyset. Unlike `bulk_action_items`/`bulk_logs` (up
  to ~1M rows per job), `bulk_actions` is one row per job, never reaches
  a scale where OFFSET is a real cost; keyset there would be needless
  complexity for no benefit. Filters on `status`/`action_type`, sorts by
  `id` asc/desc (id order = creation order, no separate tiebreaker
  needed).
- New endpoints in `app/api/bulk_actions.py`: `GET /bulk-actions`
  (paginated, filterable by status/action_type, sortable), `GET
  /bulk-actions/{id}/stats`, `GET /bulk-actions/{id}/progress`, `POST
  /bulk-actions/{id}/cancel` (rejects with 409 if the action is already
  in a terminal state - only `QUEUED`/`SCHEDULED`/`RUNNING` are
  cancellable; the actual stop-in-flight-processing behavior was already
  built in Step 3's cooperative cancellation checks in both Celery
  tasks, this endpoint just flips the status flag they read).
  `GET /bulk-actions/{id}/logs` now exposes the `after_id`/`limit` cursor
  params Step 2 added to `BulkLogRepository.get_logs`, wrapped in
  `PaginatedBulkLogs` with a `next_after_id` cursor for the client to
  continue from.
- Verified route registration via `original_router.routes` introspection
  (this environment's installed FastAPI/Starlette - 0.139.2/1.3.1 - is a
  much newer major version than commonly documented; its top-level
  `app.routes` lazily represents included routers as an opaque
  `_IncludedRouter` object rather than flattening them, which looked like
  a broken router at first glance and needed the extra introspection
  step to confirm it wasn't). Did not run a full request through
  `TestClient` - that needs the `httpx2` package, which isn't in
  `requirements.txt` and wasn't added without asking first.

**Step 6 — Cross-cutting — DONE**
- **Docker dropped entirely, confirmed with the user.** `Dockerfile` and
  `docker-compose.yml` deleted. Postgres/Redis are native local installs
  (already how this environment has been running since Step 2's manual
  verification) - nothing to containerize. The original plan's "fill in
  Dockerfile / add app+worker services to docker-compose.yml" bullet is
  dropped, not deferred.
- **structlog wired in for real**: `app/core/logging.py`
  (`configure_logging`) wires structlog into stdlib logging with a
  console renderer for local dev / JSON for `JSON_LOGS=true`; called from
  both `app/main.py` (API) and `app/workers/celery_app.py` (via the
  `setup_logging` signal, so worker logs go through the same
  formatter). `app/core/middleware.py` (`RequestLoggingMiddleware`) logs
  every request with a `request_id` (also returned as an `X-Request-ID`
  header), method, path, status, duration. `app/workers/tasks.py` binds
  `bulk_action_id`/task name/batch size as structlog contextvars and logs
  each task's start/skip/complete/fail with counts and duration. Each of
  the five action handlers logs a `warning`-level `item_failed` event
  (item_id, contact_id, error) - success-path per-item logging was
  deliberately left out at that granularity (up to `BATCH_SIZE` per
  batch) in favor of the handler returning aggregate succeeded/failed/
  skipped counts, which the batch worker logs once per batch.
- **Two real bugs found while writing tests for this step** (beyond the
  logging/testing scope itself - both are correctness fixes, not test
  infrastructure):
  1. **Zero-batch completion edge case**: if every requested `entity_id`
     was missing, `dispatch_bulk_action` never enqueues any batch task,
     so nothing ever calls the completion check - the action would sit
     in `RUNNING` forever despite being fully (if trivially) done. Fixed
     by running the same `stats_repo.is_complete(...)` check in
     `dispatch_bulk_action` itself, right after the batch-dispatch loop,
     as a safety net for that case. Covered by
     `tests/integration/test_bulk_actions_api.py::
     test_all_missing_contact_ids_still_completes`.
  2. **Per-item atomicity**: all five action handlers committed once for
     the whole batch, not per item. Since SQLAlchemy defers constraint
     checks to flush time, a single bad item's `IntegrityError` at that
     shared end-of-batch commit would revert every other already-
     "succeeded" item in the batch too - directly undermining the
     item-level idempotency this whole project is built around. A
     `begin_nested()`/SAVEPOINT-per-item fix was tried first and
     verified NOT to work on this SQLAlchemy version (2.0.51): a flush
     failure inside one leaves the whole `Session` in a
     `PendingRollbackError` state for the rest of the batch, confirmed
     via isolated repro scripts against both a plain production-style
     session and the test-fixture session - not a test-only artifact.
     Fixed by committing (and rolling back on failure) after **every
     item**, not once per batch, across all five handlers. Trade-off:
     more DB round-trips per batch instead of one - accepted, since
     batches already run in parallel across workers and correctness
     matters more here than shaving round-trips. Covered by
     `tests/unit/test_actions.py::TestBulkUpdateAction::
     test_one_bad_item_does_not_take_down_the_rest_of_the_batch`
     (deliberately forces a unique-email collision on one item in a
     5-item batch and asserts the other four still succeed).
- **42 tests, all passing** (`pytest` - 3 fixture smoke tests, 19 service
  validation/cancellation unit tests, 8 action-handler unit tests, 12 API
  integration tests). **Confirmed with the user**: no testcontainers, no
  docker-compose test profile (both assumed Docker) - tests run against
  the *same* native Postgres used for dev, each test wrapped in a
  transaction that's rolled back afterward (SQLAlchemy 2.0's
  `join_transaction_mode="create_savepoint"`, so the app code's own
  internal `session.commit()` calls - now happening per-item, per the fix
  above - don't escape the outer rollback; `app.workers.tasks.
  SessionLocal` is monkeypatched per-test to bind to the same connection,
  since Celery tasks aren't part of FastAPI's DI and open their own
  session). Celery's `.delay()` calls use `task_always_eager=True` in
  tests - runs the real dispatcher/batch-worker/handler code and real SQL
  inline, in-process, rather than through a real Redis broker + separate
  worker process; this isn't a mock of business logic, it just skips the
  message-transport hop, which isn't what these tests are meant to
  verify anyway. Verified no test data leaks into the real dev DB
  (checked directly via SQL after a full test run).
- **README.md written**: architecture (including the two bugs above and
  why the fixes are what they are), setup, testing approach, scaling
  notes, known limitations. Dropped the Postman collection from the
  original plan - the OpenAPI/Swagger docs FastAPI already serves at
  `/docs` cover the same need without a second artifact to keep in sync.

## Post-Step-6: assignment spec-compliance pass (2026-07-22)

The user shared the actual assignment PDF (Shipmnts "Bulk Action
Platform for CRM Application") partway through Step 6. Comparing it
against what had been built surfaced real gaps against the *required*
(non-optional) spec, separate from the three optional enhancements
(rate limiting, de-duplication, scheduling) - the user explicitly asked
to set those three aside and do the necessary items first. This section
covers that necessary-items pass; the three optional enhancements
remain **not started**, discussed only (see "Known limitations" in
README for the implementation sketch + effort estimate for each).

**Gaps identified against the required spec:**
1. Postman collection - required deliverable, had been dropped in Step 6
   in favor of Swagger/OpenAPI docs. Wrong call for a graded assignment
   with an explicit deliverables list.
2. `GET /logs` - spec says "fetch **and filter** logs"; only cursor
   pagination existed, no filter.
3. Load testing ("thousands of entities per minute") - required, never
   actually run or documented.
4. Entity-agnostic architecture - spec calls this "crucial" even though
   only Contact needs to work. The codebase was Contact-specific
   throughout (handlers imported `Contact`/`ContactRepository` directly,
   `UPDATABLE_FIELDS` was a bulk_update-owned constant) despite having an
   `entity_type` field that was otherwise vestigial.
5. Loom video - required, but a human deliverable; offered to draft a
   script/outline, can't record it.

**Work done (all committed, 43 tests passing throughout):**
- `GET /bulk-actions/{id}/logs` now accepts `status` (SUCCESS/FAILED/
  SKIPPED) as a query param, filtered in `BulkLogRepository.get_logs`.
- New `postman/Bulk Action Platform.postman_collection.json` - 15
  requests, one per action type (with example bodies) plus list/stats/
  progress/cancel/logs and a couple of deliberate error-case examples
  (unsupported action type, not-found). Hand-authored rather than
  generated from `/openapi.json`, so each action type gets its own
  realistic example body instead of one generic schema-derived example.
- **Entity-agnostic refactor - "repository abstraction layer" scope,
  confirmed with the user** (the alternative considered and rejected:
  full schema generalization - renaming `bulk_action_items.contact_id`
  to a generic `entity_id` and dropping its FK - which would've been
  ~5-7h and touched a column threaded through Steps 2-6's already-tested
  code, for a second entity type that doesn't actually exist yet):
  - New `app/entities/` package: `BaseEntityRepository` (ABC:
    `get_by_ids`, `get_updatable_fields`) and `EntityRegistry` (maps
    `entity_type` -> repository *class*, instantiated per lookup with a
    `db` session - unlike `ActionRegistry`'s singleton action instances,
    a repository can't be a shared singleton since it's bound to a
    session).
  - `ContactRepository` now implements `BaseEntityRepository`
    (`entity_type = "contact"`); `get_contacts_by_ids` renamed to the
    interface's `get_by_ids`; the long-dead, never-called
    `get_contacts_in_batches` method (flagged as unused back in Step 3's
    verification) was deleted while in the file anyway.
  - `UPDATABLE_FIELDS` moved from `app/actions/constants.py` into
    `contact_repository.py` - it's a fact about the Contact entity, not
    about the `bulk_update` action, and is now served via
    `get_updatable_fields()` instead of a direct import.
  - `SUPPORTED_ACTIONS`/`SUPPORTED_ENTITY_TYPES` constants removed
    entirely - `bulk_action_service.py`'s validation now asks
    `ActionRegistry.supported_actions()` / `EntityRegistry.
    supported_types()` (both new classmethods, added for symmetry),
    so what's "supported" has exactly one source of truth: what's
    actually registered.
  - `BaseBulkAction.execute()`'s `contacts_by_id: dict[int, Contact]`
    param renamed to `entities_by_id: dict[int, Any]`; all five handlers
    and both Celery tasks updated to match, going through
    `EntityRegistry.get_repository(bulk_action.entity_type, db)` instead
    of importing `ContactRepository` directly.
  - **Consciously not generalized further** (documented in README, not
    silently left inconsistent): `bulk_action_items.contact_id` keeps its
    name and its FK to `contacts` specifically; `bulk_assign_owner` still
    writes `entity.owner_id`; `bulk_export`'s snapshot still reads
    `name`/`email`/`age` directly. Generalizing these further without a
    real second entity to generalize against would be guessing at an
    interface, not building one - flagged as the honest remaining gap in
    "Known limitations" instead.
- **Load test**: new `app/commands/load_test.py` - POSTs one
  `bulk_update` against N existing contacts through the real running API
  (not the service layer directly), polls `/progress`, reports
  entities/minute. Run for real: started a throwaway `uvicorn` +
  `celery worker` (`--pool=threads --concurrency=8`) in the background,
  targeted 5000 contacts, then stopped both processes afterward.
  **Result: 5000 contacts / 50 batches / 36.6s / 0 failures -> ~8200
  entities/minute**, comfortably clearing "thousands per minute" on a
  single worker process. Verified structlog output was clean and
  readable throughout the run (`batch_started`/`batch_completed` lines
  with duration/counts, no errors).
- README updated: new "Entities are pluggable too" architecture section,
  Postman collection reference (replacing the old "we dropped this"
  note), load test results, and an expanded "Known limitations" section
  documenting the entity-agnostic scope boundary and all three deferred
  optional enhancements with brief implementation sketches.

**Not done, by explicit agreement**: rate limiting, de-duplication,
scheduling (the three optional enhancements - discussed with effort
estimates, none started), Loom video (human deliverable), full
schema-level entity generalization (see above).

## Optional enhancements branch (`feature/optional-enhancements`, 2026-07-22)

Built on a dedicated branch, off `main`, per explicit user request - none
of this has touched `main` or the deployed Render app. All three
enhancements from README "Optional enhancements" (previously just a
design sketch) are now actually implemented and tested (59 tests, up
from 43). Two real bugs were found and fixed via the user's own sharp
questions after the initial implementation, not by the user testing
first this time - both are exactly the kind of thing worth recording:

1. **Rate limiter infinite-retry bug** (user asked: "if we keep the
   limit at 5 and send 10, won't it fail forever since 10>5 regardless
   of which minute?"). Correct catch - the original implementation only
   checked "does this reservation fit `try_consume`," with no upper
   bound on batch size vs. the configured limit. If a single batch's
   size permanently exceeds `RATE_LIMIT_PER_MINUTE`, every retry
   attempt reserves the same too-large amount against a freshly-reset
   budget and gets denied again, forever - a bulk action silently stuck
   with no error ever surfaced. Fixed: `process_bulk_action_batch` now
   checks `len(pending) > limiter.limit_per_minute` first and, if so,
   fails those items immediately with a clear message instead of
   retrying - see `app/workers/tasks.py`. New test:
   `test_batch_fails_fast_when_permanently_over_limit`; the old
   over-budget test was renamed/fixed to actually test the *temporary*
   retry case (batch size fits, account's cumulative usage doesn't) -
   `test_batch_retries_when_temporarily_over_budget`.
2. **`account_id` optionality defeated its own purpose** (user asked:
   "shouldn't account_id be compulsory if we want to actually apply rate
   limiting?"). Also correct - if omitting the field just skips rate
   limiting entirely, the spec's "no account should be able to exceed a
   rate limit" isn't actually enforced on anyone who doesn't opt in.
   Fixed: `BulkActionCreate.account_id` is now required (no default) -
   enforced at the HTTP/Pydantic boundary specifically, since that's the
   untrusted edge. `BulkActionService.create_bulk_action` deliberately
   kept `account_id` optional at the Python level, so internal/
   service-level callers (and all the service-level unit tests) aren't
   forced through the same gate - only `tests/integration/
   test_bulk_actions_api.py` (11 POST bodies) and the Postman
   collection's Create examples needed `account_id` added.

**A third, unrelated bug surfaced independently while chasing test
failures from the `account_id`-required change** (not something either
of the two questions above was about - found while debugging why
`test_bulk_update_runs_end_to_end` started failing after adding
`account_id` to its request body): `tests/conftest.py`'s `client`
fixture reuses the same `db_session` object across every simulated HTTP
request within one test (that's what makes the whole-test rollback
design work) - but unlike production, where every request gets a
genuinely fresh Session with an empty identity map, a `BulkAction`
object loaded during an earlier simulated request (e.g. the `POST`)
stayed cached and stale for a later one (e.g. the `GET` checking its
status), even after a *different* Session - the Celery task's own
`SessionLocal()` - had since committed real changes to that same row.
Confirmed via raw SQL on the same connection that the actual committed
data was always correct (`COMPLETED`) - this was purely a test-fixture
artifact, never a production bug, and would never manifest outside
tests since real requests never share a session. It was intermittent
depending on test execution order (whichever test happened to be first
to touch a cold Redis connection - a ~2s one-time client warm-up delay,
unrelated to the actual bug - was the one that surfaced it, which is
why it looked like a flake at first). Fixed with one line:
`db_session.expire_all()` at the top of `override_get_db()`, forcing
each simulated request to see current data without ending the
transaction the rollback relies on.

## Next immediate action
Everything above is done and verified (59 tests passing on the feature
branch, real load test previously run on `main`). Nothing is currently
blocking. Remaining candidates for further work, none started, none
promised:
- Merge `feature/optional-enhancements` into `main` (and redeploy),
  whenever the user wants to - not done automatically.
- Loom video - needs the user; a script/outline can be drafted on
  request.
- Wire `idempotency_key` into `create_bulk_action` (column exists,
  unused).
- Filter out soft-deleted (`status="DELETED"`) contacts from being
  re-targeted by a later bulk action, if that's ever desired.
- Real file export + download endpoint for `bulk_export`, if/when
  storage requirements are decided (currently a log-only placeholder).
- Auth/authz - there is currently none on any endpoint. `account_id` is
  mandatory now but still entirely unverified/client-supplied - the
  next real step for rate limiting to become a real security boundary.
- Full schema-level entity generalization (generic `entity_id`, no
  single-table FK) - only worth doing once a second entity type is real.
