"""Engine and session factory.

One engine per process, created lazily so importing the package never opens a
connection. The API hands sessions to routes through `get_session`; the worker
uses `get_session_factory()` directly because it owns its own transaction
boundaries (see `secondbrain.queue`).
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from secondbrain.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,  # drop dead connections (e.g. after a `docker compose restart`)
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed when the request ends."""
    with get_session_factory()() as session:
        yield session


def reset_engine_cache() -> None:
    """Dispose and forget the cached engine. Used by tests that change settings."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
