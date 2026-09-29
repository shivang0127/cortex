"""Aggregates every v1 router under one prefix. New resources register here."""

from fastapi import APIRouter

from secondbrain.api.v1 import ask, documents, embeddings, health, jobs, llm, search, subjects

router = APIRouter(prefix="/v1")
router.include_router(health.router)
router.include_router(documents.router)
router.include_router(subjects.router)
router.include_router(jobs.router)
router.include_router(search.router)
router.include_router(embeddings.router)
router.include_router(ask.router)
router.include_router(llm.router)
