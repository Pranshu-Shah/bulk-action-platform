# Loom Video Script/Outline

A suggested ~8-10 minute walkthrough: architecture explanation + live
demo. This is a structure to talk through in your own voice, not a
script to read verbatim.

## 1. Intro (30s)

"This is a bulk action platform for a CRM — it runs bulk operations
(update, delete, archive, assign-owner, export) against CRM entities,
built to scale to roughly a million targets per job. Stack: FastAPI,
PostgreSQL via SQLAlchemy, Celery + Redis for the async queue, structlog
for logging, pytest for tests. The reference entity is Contact, but the
architecture is entity-agnostic by design."

## 2. Architecture (2-3 min)

Show the ASCII flow diagram from the README while you talk through it:

- "The API layer only does cheap work: validate the request, insert one
  `bulk_actions` row, and hand off to a Celery task. Everything
  expensive happens asynchronously, off the request path."
- "A dispatcher task snapshots the target IDs into an item table — one
  row per entity per job — and splits them into batches."
- "Batch worker tasks then run in parallel, across however many Celery
  workers are online. That's the horizontal-scaling story: add more
  worker processes or machines pulling from the same Redis queue, with
  no code change."
- "Two registries drive the extensibility: `ActionRegistry` maps
  `action_type` to a handler class — adding a new bulk action is one new
  file. `EntityRegistry` maps `entity_type` to a repository class — the
  same pattern on the entity side, so the pipeline code never hardcodes
  a specific entity."
- A couple of the deeper design decisions worth calling out:
  - "Item-level tracking gives real idempotency — a retried batch skips
    items already in a terminal state instead of reprocessing
    everything."
  - "Each handler commits per item, not once per batch, so one bad row
    (say, a duplicate email) can't revert every other successful item in
    that batch."
  - "`bulk_delete` is a soft delete — the item table is a permanent audit
    trail with a real foreign key to the entity table, so a hard delete
    would break referential integrity for anything ever touched by a
    bulk action."

## 3. Live demo (4-5 min)

Have three terminals visible: `uvicorn`, the Celery worker, and
Postman/`/docs`. Seed contacts first if needed
(`python -m app.commands.seed_contacts`).

Suggested sequence:
1. **Create a bulk_update** targeting a handful of contacts — show the
   response (id + status=QUEUED).
2. **Switch to the worker terminal** — show the structlog lines:
   `dispatch_started` → `batch_started` → `batch_completed`, and point
   out the structured fields (bulk_action_id, duration, counts).
3. **GET /bulk-actions/{id}** — status is now COMPLETED.
4. **GET /bulk-actions/{id}/stats** — succeeded/failed/skipped counts.
5. **GET /bulk-actions/{id}/logs** — per-entity outcome log; show the
   `?status=` filter narrowing to one outcome.
6. **Create a bulk action including a nonexistent ID** — show it still
   completes, with that one entry `SKIPPED` in the logs ("Entity not
   found") rather than failing the whole job.
7. **Create a larger bulk action and immediately call cancel** — show
   either a clean cancellation or, if it completed first, the 409
   response and explain why (already in a terminal state).
8. **Run the load test** (`python -m app.commands.load_test 5000`) live,
   or show a recording, and point at the throughput figure.

## 4. Testing (1 min)

"43 automated tests: unit tests for the handlers and service validation
logic, integration tests for the full API and async pipeline. They run
against the same Postgres as local dev, each wrapped in a transaction
that rolls back afterward, so nothing persists — and Celery runs in
eager mode, so the real dispatcher/batch-worker/handler code executes
inline, against real SQL, with only the broker round-trip skipped." Run
`pytest` on screen.

## 5. Design tradeoffs and what's next (1 min)

- "The architecture is entity-agnostic at the code layer — a pluggable
  entity registry alongside the action registry — though the item
  table's schema still names its foreign key after Contact
  specifically. Generalizing that further is a deliberate next step, not
  an oversight, and the README lays out exactly what it would take."
- "Three enhancements — per-account rate limiting, email
  de-duplication, and scheduling a bulk action for a future time — are
  designed and documented but not yet implemented; happy to walk through
  the design for any of them."

## 6. Wrap up (30s)

"That's the platform end to end — happy to go deeper into any part of
it."
