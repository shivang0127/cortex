from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DatabaseHealth(BaseModel):
    reachable: bool
    server_version: str | None = None
    pgvector_version: str | None = None
    migration_revision: str | None = None
    error: str | None = None


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    version: str
    environment: str
    timestamp: datetime
    database: DatabaseHealth
