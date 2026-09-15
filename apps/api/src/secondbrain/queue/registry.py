"""Maps a job `type` string to the function that performs it.

A handler receives the worker's database session and the job's payload. It
must not commit: the worker commits the handler's writes together with the job's
completion, or rolls both back together.

A job type may also register an `on_failure` hook. It runs in its own
transaction after the handler's has been rolled back, so a stage can leave a
durable trace of what went wrong (e.g. mark a document `failed`).

Raise `NonRetryableJobError` from a handler when retrying cannot help — a
corrupt file will still be corrupt in thirty seconds; a flaky network will not.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

JobHandler = Callable[[Session, dict[str, Any]], None]
FailureHook = Callable[[Session, dict[str, Any], str, bool], None]  # (…, error, final)


class NonRetryableJobError(Exception):
    """The job failed for a reason another attempt will not fix."""


@dataclass(frozen=True)
class Registration:
    handler: JobHandler
    on_failure: FailureHook | None = None


_registry: dict[str, Registration] = {}


def register(
    job_type: str, *, on_failure: FailureHook | None = None
) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        if job_type in _registry:
            raise ValueError(f"job type {job_type!r} already registered")
        _registry[job_type] = Registration(fn, on_failure)
        return fn

    return decorator


def get_handler(job_type: str) -> JobHandler | None:
    reg = _registry.get(job_type)
    return reg.handler if reg else None


def get_failure_hook(job_type: str) -> FailureHook | None:
    reg = _registry.get(job_type)
    return reg.on_failure if reg else None


def registered_types() -> list[str]:
    return sorted(_registry)


def load_handlers() -> None:
    """Import every module that registers handlers. Called by the worker at start-up."""
    import secondbrain.pipeline.stages  # noqa: F401


@register("system.ping")
def ping(session: Session, payload: dict[str, Any]) -> None:
    log.info("pong: %s", payload.get("message", ""))
