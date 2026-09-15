"""PostgreSQL-backed job queue (ARCHITECTURE.md §1 — "not Redis + Celery").

The whole queue is `SELECT … FOR UPDATE SKIP LOCKED` on the `jobs` table. Two
workers can never claim the same row; a crashed worker's job is returned to the
queue by `requeue_stale`. Job state and the rows a handler writes commit in the
same transaction (see `secondbrain.worker`), so a completed job with no output —
or output with no completed job — cannot exist.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from secondbrain.db.models.job import Job


def enqueue(
    session: Session,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 100,
    run_after: datetime | None = None,
    max_attempts: int = 3,
) -> Job:
    """Add a job. Commits nothing — the caller owns the transaction."""
    job = Job(
        type=job_type,
        payload=payload or {},
        priority=priority,
        max_attempts=max_attempts,
        run_after=run_after or datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


def claim(session: Session, worker_id: str, job_types: list[str] | None = None) -> Job | None:
    """Atomically take the next runnable job, or return None if there is none.

    Must be called inside a transaction that is committed promptly: the row is
    marked `running` and the lock released on commit, before the handler runs.
    """
    stmt = (
        select(Job)
        .where(Job.status == "queued", Job.run_after <= datetime.now(UTC))
        .order_by(Job.priority, Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job_types:
        stmt = stmt.where(Job.type.in_(job_types))

    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        return None

    job.status = "running"
    job.locked_at = datetime.now(UTC)
    job.locked_by = worker_id
    job.attempts += 1
    session.flush()
    return job


def complete(session: Session, job: Job) -> None:
    job.status = "succeeded"
    job.finished_at = datetime.now(UTC)
    job.locked_at = None
    job.locked_by = None
    session.flush()


def fail(
    session: Session,
    job: Job,
    error: str,
    *,
    retry_delay: timedelta | None = None,
    retry: bool = True,
) -> bool:
    """Record a failure. Retries with backoff until `max_attempts`, then marks `failed`.

    Returns True when the failure is final (no further attempt will be made).
    """
    job.last_error = error[:4000]
    job.locked_at = None
    job.locked_by = None
    if retry and job.attempts < job.max_attempts:
        if retry_delay is None:  # explicit: timedelta(0) is falsy but means "retry now"
            retry_delay = timedelta(seconds=30 * 2 ** (job.attempts - 1))
        delay = retry_delay
        job.status = "queued"
        job.run_after = datetime.now(UTC) + delay
    else:
        job.status = "failed"
        job.finished_at = datetime.now(UTC)
    session.flush()
    return job.status == "failed"


def requeue_stale(session: Session, older_than: timedelta) -> int:
    """Return jobs abandoned by a dead worker to the queue. Returns the count."""
    cutoff = datetime.now(UTC) - older_than
    result = session.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < cutoff)
        .values(status="queued", locked_at=None, locked_by=None, last_error="stale: requeued")
    )
    return result.rowcount or 0


def get(session: Session, job_id: uuid.UUID) -> Job | None:
    return session.get(Job, job_id)
