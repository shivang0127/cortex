from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from secondbrain.config import Settings, get_settings
from secondbrain.db.engine import get_engine
from secondbrain.schemas.health import HealthReport
from secondbrain.services.health import build_health_report

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthReport, summary="Liveness + database status")
def health(
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> HealthReport:
    """Always returns 200. Read `status` / `database.reachable` for the verdict."""
    return build_health_report(settings, engine)
