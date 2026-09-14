"""Queue semantics. Everything here needs a real PostgreSQL (SKIP LOCKED has no
in-memory stand-in) and is skipped until one is reachable."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from secondbrain.queue import queue, registry

pytestmark = pytest.mark.integration


def test_ping_handler_is_registered() -> None:
    # Pure unit check — but kept with the queue tests it explains.
    assert registry.get_handler("system.ping") is not None
    assert "system.ping" in registry.registered_types()


def test_enqueue_claim_complete(db_session: Session) -> None:
    job = queue.enqueue(db_session, "system.ping", {"message": "hi"})
    assert job.status == "queued"

    claimed = queue.claim(db_session, "worker-test", ["system.ping"])
    assert claimed is not None and claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.locked_by == "worker-test"

    queue.complete(db_session, claimed)
    assert claimed.status == "succeeded"
    assert claimed.finished_at is not None


def test_claim_respects_run_after_and_priority(db_session: Session) -> None:
    later = queue.enqueue(
        db_session, "system.ping", {}, run_after=datetime.now(UTC) + timedelta(hours=1)
    )
    low = queue.enqueue(db_session, "system.ping", {"p": "low"}, priority=200)
    high = queue.enqueue(db_session, "system.ping", {"p": "high"}, priority=10)

    first = queue.claim(db_session, "w", ["system.ping"])
    assert first is not None and first.id == high.id
    second = queue.claim(db_session, "w", ["system.ping"])
    assert second is not None and second.id == low.id
    assert queue.claim(db_session, "w", ["system.ping"]) is None  # `later` is not due
    assert later.status == "queued"


def test_fail_retries_then_gives_up(db_session: Session) -> None:
    queue.enqueue(db_session, "system.ping", {}, max_attempts=2)

    claimed = queue.claim(db_session, "w", ["system.ping"])
    assert claimed is not None
    queue.fail(db_session, claimed, "boom", retry_delay=timedelta(0))
    assert claimed.status == "queued" and claimed.last_error == "boom"

    claimed = queue.claim(db_session, "w", ["system.ping"])
    assert claimed is not None and claimed.attempts == 2
    queue.fail(db_session, claimed, "boom again")
    assert claimed.status == "failed"
    assert claimed.finished_at is not None


def test_requeue_stale_returns_abandoned_jobs(db_session: Session) -> None:
    job = queue.enqueue(db_session, "system.ping", {})
    claimed = queue.claim(db_session, "dead-worker", ["system.ping"])
    assert claimed is not None
    claimed.locked_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.flush()

    assert queue.requeue_stale(db_session, timedelta(minutes=15)) == 1
    db_session.refresh(job)
    assert job.status == "queued" and job.locked_by is None
