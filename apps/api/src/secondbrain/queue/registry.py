"""Maps a job `type` string to the function that performs it.

A handler receives the worker's database session and the job's payload. It
must not commit: the worker commits the handler's writes together with the job's
completion, or rolls both back together.

Phase 0 registers only `system.ping`, which exists so the queue can be
exercised end-to-end. Ingestion handlers arrive in Phase 1.
"""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

JobHandler = Callable[[Session, dict[str, Any]], None]

_handlers: dict[str, JobHandler] = {}


def register(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        if job_type in _handlers:
            raise ValueError(f"job type {job_type!r} already registered")
        _handlers[job_type] = fn
        return fn

    return decorator


def get_handler(job_type: str) -> JobHandler | None:
    return _handlers.get(job_type)


def registered_types() -> list[str]:
    return sorted(_handlers)


@register("system.ping")
def ping(session: Session, payload: dict[str, Any]) -> None:
    log.info("pong: %s", payload.get("message", ""))
