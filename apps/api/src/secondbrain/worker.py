"""Background worker: the `secondbrain-worker` entrypoint.

A plain loop: claim a job, run its handler, commit or roll back, repeat. It is a
separate process from the API so long-running work survives API reloads and can
be stopped independently (ARCHITECTURE.md §1).
"""

import logging
import os
import signal
import socket
import time
import traceback
from datetime import timedelta

from sqlalchemy.exc import OperationalError

from secondbrain import __version__
from secondbrain.config import Settings, get_settings
from secondbrain.db.engine import get_session_factory
from secondbrain.main import configure_logging
from secondbrain.queue import queue, registry
from secondbrain.queue.registry import NonRetryableJobError

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings, worker_id: str | None = None) -> None:
        self.settings = settings
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.session_factory = get_session_factory()
        self._stop = False

    def stop(self, *_: object) -> None:
        log.info("stop requested; finishing current job")
        self._stop = True

    def run_forever(self) -> None:
        registry.load_handlers()
        log.info(
            "worker %s v%s online; handles: %s",
            self.worker_id,
            __version__,
            ", ".join(registry.registered_types()) or "(nothing)",
        )
        while not self._stop:
            try:
                worked = self.run_once()
            except OperationalError as exc:
                # Database down or restarting: keep polling rather than dying.
                log.warning("database unavailable, retrying: %s", str(exc).splitlines()[0])
                worked = False
            if not worked:
                time.sleep(self.settings.worker_poll_interval_seconds)
        log.info("worker %s stopped", self.worker_id)

    def run_once(self) -> bool:
        """Claim and run at most one job. Returns True if a job was processed."""
        # 1. Claim in a short transaction so the row lock is released immediately.
        with self.session_factory.begin() as session:
            queue.requeue_stale(session, timedelta(seconds=self.settings.worker_stale_job_seconds))
            job = queue.claim(session, self.worker_id, registry.registered_types())
            if job is None:
                return False
            job_id, job_type, payload, attempts = job.id, job.type, dict(job.payload), job.attempts

        log.info("job %s %s attempt %d starting", job_id, job_type, attempts)
        handler = registry.get_handler(job_type)

        # 2. Run the handler and the completion mark in ONE transaction.
        try:
            with self.session_factory.begin() as session:
                job = queue.get(session, job_id)
                assert job is not None
                if handler is None:
                    raise RuntimeError(f"no handler registered for job type {job_type!r}")
                handler(session, payload)
                queue.complete(session, job)
            log.info("job %s %s succeeded", job_id, job_type)
        except Exception as exc:  # noqa: BLE001 — a handler bug must not kill the worker
            retry = not isinstance(exc, NonRetryableJobError)
            error = traceback.format_exc()
            log.exception("job %s %s failed (retry=%s)", job_id, job_type, retry)
            # 3. Record the failure separately: the handler's transaction rolled back.
            with self.session_factory.begin() as session:
                job = queue.get(session, job_id)
                final = queue.fail(session, job, error, retry=retry) if job is not None else True
                hook = registry.get_failure_hook(job_type)
                if hook is not None:
                    hook(session, payload, f"{type(exc).__name__}: {exc}", final)
        return True


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    worker = Worker(settings)
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
