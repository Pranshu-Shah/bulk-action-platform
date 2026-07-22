# Deployment

This service has three moving parts that need to run independently:
a **web process** (FastAPI/uvicorn), a **worker process** (Celery), and
two **managed data stores** (Postgres, Redis). No Docker is required —
the instructions below target a PaaS with native Python support and
managed database add-ons (Render is used as the concrete example;
Railway, Fly.io, and similar platforms follow the same shape almost
exactly).

## 1. Provision the data stores

- **Postgres**: create a managed Postgres instance on the platform.
  Copy its connection string — this becomes `DATABASE_URL`.
- **Redis**: create a managed Redis instance the same way — this
  becomes `REDIS_URL`.

Both are used exactly as they would be locally; nothing in the
application code is platform-specific.

## 2. Web service (API)

Point the platform at this GitHub repo and configure:

- **Build command**: `pip install -r requirements.txt`
- **Start command**:
  `alembic upgrade head && python -m app.commands.seed_contacts && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Health check path**: `/health`

A dedicated pre-deploy/release-phase step (a paid feature on some
platforms' free tiers) isn't required — `alembic upgrade head` and the
seed script are both idempotent, so chaining them into the start
command is safe to run on every boot: migrations are a no-op once the
DB is at head, and `seed_contacts` skips entirely once the `contacts`
table has any rows (see `app/commands/seed_contacts.py`). Only the
**web service** runs this chain — the worker service (below) starts
Celery directly, so two processes never race to run migrations/seed
against a fresh database at the same time.

If the platform *does* offer a pre-deploy/release-phase command, moving
just `alembic upgrade head` there instead is slightly cleaner (keeps the
start command minimal) — either approach is correct.

## 3. Worker service (Celery)

A second service, same repo, same environment variables, different
start command:

```
celery -A app.workers.celery_app.celery worker --loglevel=info --concurrency=4
```

**Note on `--pool`**: locally this project runs `--pool=threads`
because Celery's default `prefork` pool needs `os.fork()`, which
doesn't exist on Windows. Linux-based PaaS platforms don't have that
restriction — `prefork` (the default, just omit `--pool` entirely) gives
real OS-process parallelism instead of thread-based concurrency, and is
generally the better choice for a production Linux deployment. Use
`--pool=threads` only if there's a specific reason to prefer it (e.g.
memory constraints — prefork's separate processes use more memory than
threads sharing one process).

## 4. Environment variables (both services)

```
DATABASE_URL=<from the managed Postgres instance>
REDIS_URL=<from the managed Redis instance>
BATCH_SIZE=500
LOG_LEVEL=INFO
JSON_LOGS=true
```

`JSON_LOGS=true` in production — structlog then emits JSON lines instead
of the human-readable console format, which is what most log
aggregation tools (the platform's own log viewer, or anything it forwards
to) expect.

Never commit real values for these — `.env` is gitignored specifically
so secrets live only in the platform's environment/secret manager, not
in source control. `.env.example` documents the shape without real
values.

## 5. First deploy checklist

1. Push to GitHub (`main` branch).
2. Create the Postgres and Redis add-ons; note their connection strings.
3. Create the web service pointing at the repo; set env vars; start
   command includes the migrate-then-seed-then-serve chain from step 2
   above.
4. Create the worker service pointing at the same repo; same env vars;
   start command is just the `celery` command from step 3 above (no
   migrate/seed chain).
5. Deploy. Confirm `GET /health` returns `{"status": "healthy"}`, and
   that contacts exist (e.g. via `GET /docs` and creating a bulk action
   against a small ID range) — the web service's first boot log should
   show `Inserted 5000 contacts` (or `already has N rows, skipping seed`
   on every boot after that).

## Scaling in production

- **More worker capacity**: increase the worker service's instance
  count (or `--concurrency`) — batches are already designed to run in
  parallel across however many workers are consuming the queue; this
  requires no code or schema changes.
- **More web capacity**: increase the web service's instance count.
  The API layer does no heavy lifting itself (it only validates and
  enqueues), so it scales independently of worker capacity.
- **Database connection limits**: each worker concurrency slot opens
  its own DB session. If worker concurrency is increased substantially,
  check the managed Postgres plan's max-connections limit against
  `(number of worker instances) × (concurrency per instance)`, and raise
  SQLAlchemy's pool size (`create_engine(..., pool_size=..., max_overflow=...)`
  in `app/core/database.py`) if needed.

## Before exposing this publicly

None of the following exist yet (see README "Known limitations") and
are worth addressing before real external traffic hits this service:
authentication/authorization on every endpoint, and per-account rate
limiting (see README "Optional enhancements" for the design).
