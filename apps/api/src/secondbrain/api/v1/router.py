"""Aggregates every v1 router under one prefix. New resources register here."""

from fastapi import APIRouter

from secondbrain.api.v1 import health

router = APIRouter(prefix="/v1")
router.include_router(health.router)
