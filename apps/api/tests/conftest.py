"""Shared fixtures.

Unit tests never need a database. Tests marked `integration` need a reachable
PostgreSQL (the docker-compose one) and skip themselves when it is absent, so
`pytest` is always green on a fresh checkout and fully exercised once
`docker compose up` has run.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from secondbrain.config import get_settings
from secondbrain.db.engine import get_engine, reset_engine_cache

UNREACHABLE_DATABASE_URL = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere"


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Reset cached settings/engine around a test that changes the environment."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()
    reset_engine_cache()
    yield monkeypatch
    get_settings.cache_clear()
    reset_engine_cache()


@pytest.fixture
def client_without_db(settings_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An app whose database is guaranteed unreachable."""
    settings_env.setenv("DATABASE_URL", UNREACHABLE_DATABASE_URL)
    from secondbrain.main import create_app

    with TestClient(create_app()) as client:
        yield client


_reachable: bool | None = None


def _database_reachable() -> bool:
    """Probe the database once per test session; skipping is otherwise slow on Windows."""
    global _reachable
    if os.environ.get("TEST_DATABASE_URL"):
        os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    get_settings.cache_clear()
    reset_engine_cache()
    if _reachable is None:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("select 1"))
            _reachable = True
        except SQLAlchemyError:
            _reachable = False
    return _reachable


@pytest.fixture
def db_client(settings_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An app against the real local database; skips when it is not running."""
    if not _database_reachable():
        pytest.skip("PostgreSQL not reachable — start it with `docker compose up -d`")
    from secondbrain.main import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def db_session(settings_env: pytest.MonkeyPatch):
    """A real session with rollback-on-exit; skips when the database is absent."""
    if not _database_reachable():
        pytest.skip("PostgreSQL not reachable — start it with `docker compose up -d`")
    from secondbrain.db.engine import get_session_factory

    with get_session_factory()() as session:
        yield session
        session.rollback()
