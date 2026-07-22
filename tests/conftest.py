import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.core.dependencies import get_db
from app.main import app
from app.models.contact import Contact
from app.workers.celery_app import celery


@pytest.fixture()
def db_session(monkeypatch):
    """
    Wraps the whole test in one real-DB transaction + SAVEPOINT
    (SQLAlchemy 2.0's join_transaction_mode="create_savepoint"), rolled
    back at the end - so the app code's own `session.commit()` calls
    (which happen constantly: every repository, every Celery task) don't
    escape the outer transaction. Tests run against the same Postgres
    used for local dev, per the team's choice not to stand up a
    separate test DB or containers for this.

    `app.workers.tasks` imports `SessionLocal` at module scope and calls
    it directly (Celery tasks aren't part of FastAPI's DI, so there's no
    dependency to override) - patched here so a task's `SessionLocal()`
    call binds to this same connection instead of opening a fresh one
    that would commit for real and escape the rollback.
    """
    connection = engine.connect()
    trans = connection.begin()

    TestSessionLocal = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )

    session = TestSessionLocal()

    monkeypatch.setattr("app.workers.tasks.SessionLocal", TestSessionLocal)

    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def celery_eager():
    """
    Runs `.delay()` calls inline, in-process, against the real DB/real
    task code - instead of round-tripping through Redis to a separate
    worker process that wouldn't share this test's transaction anyway.
    Not a mock of business logic: the real dispatch/batch/handler code
    and real SQL still execute, just without the message-broker hop.
    """
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = True

    yield

    celery.conf.task_always_eager = False
    celery.conf.task_eager_propagates = False


@pytest.fixture()
def client(db_session):

    def override_get_db():
        # In production, every HTTP request gets a genuinely fresh
        # Session with an empty identity map, so it always sees current
        # data. Here, `db_session` is deliberately reused across every
        # simulated request in a test (that's what makes the whole-test
        # rollback work) - but without this, an object loaded during an
        # earlier request (e.g. the POST that creates a BulkAction) stays
        # cached and stale for a later request (e.g. the GET that checks
        # its status), even after a *different* session - such as a
        # Celery task's own SessionLocal() - has since committed real
        # changes to that same row. expire_all() forces the next query to
        # reload current data instead of returning the stale cached
        # object, without ending the transaction the rollback relies on.
        db_session.expire_all()
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def contacts(db_session):
    """Five ACTIVE contacts, ids assigned by the DB, rolled back after the test."""
    created = [
        Contact(name=f"Test Contact {i}", email=f"test-contact-{i}@example.com", status="ACTIVE", age=30 + i)
        for i in range(5)
    ]

    db_session.add_all(created)
    db_session.commit()

    for contact in created:
        db_session.refresh(contact)

    return created
