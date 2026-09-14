"""Health reporting.

Deliberately tolerant: a health check must *describe* a broken database, not
fail because of it, so the dashboard can show "database unreachable" instead
of a blank page.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from secondbrain import __version__
from secondbrain.config import Settings
from secondbrain.schemas.health import DatabaseHealth, HealthReport

log = logging.getLogger(__name__)


def check_database(engine: Engine) -> DatabaseHealth:
    try:
        with engine.connect() as conn:
            server_version = conn.execute(text("select version()")).scalar_one()
            pgvector_version = conn.execute(
                text("select extversion from pg_extension where extname = 'vector'")
            ).scalar_one_or_none()
            migration_revision = None
            if _has_alembic_table(conn):  # absent until `alembic upgrade head` has run
                migration_revision = conn.execute(
                    text("select version_num from alembic_version")
                ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        # First line only: psycopg errors are multi-line and include the DSN.
        message = str(exc).splitlines()[0]
        log.warning("database health check failed: %s", message)
        return DatabaseHealth(reachable=False, error=message)

    return DatabaseHealth(
        reachable=True,
        server_version=server_version.split(" on ")[0],
        pgvector_version=pgvector_version,
        migration_revision=migration_revision,
    )


def _has_alembic_table(conn) -> bool:
    return bool(
        conn.execute(text("select to_regclass('public.alembic_version') is not null")).scalar_one()
    )


def build_health_report(settings: Settings, engine: Engine) -> HealthReport:
    database = check_database(engine)
    return HealthReport(
        status="ok" if database.reachable and database.pgvector_version else "degraded",
        app=settings.app_name,
        version=__version__,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
        database=database,
    )
