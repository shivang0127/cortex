"""Aggregates every v1 router under one prefix. New resources register here."""

from fastapi import APIRouter

from secondbrain.api.v1 import documents, embeddings, health, jobs, search, subjects

router = APIRouter(prefix="/v1")
router.include_router(health.router)
router.include_router(documents.router)
router.include_router(subjects.router)
router.include_router(jobs.router)
router.include_router(search.router)
router.include_router(embeddings.router)
