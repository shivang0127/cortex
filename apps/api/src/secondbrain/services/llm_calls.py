"""Writing the `llm_calls` observability rows.

Deliberately best-effort: a logging failure must never turn a good answer into
an error, so `record` swallows its own exceptions and says so in the log.
"""

import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from secondbrain.config import Settings
from secondbrain.db.models import LlmCall
from secondbrain.providers.llm.base import Message
from secondbrain.services.rag import AnswerResult

log = logging.getLogger(__name__)


def record(
    session: Session,
    settings: Settings,
    *,
    task: str,
    result: AnswerResult,
    messages: list[Message] | None = None,
    status: str | None = None,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    payloads = settings.llm_log_payloads
    call = LlmCall(
        task=task,
        provider=result.provider_id or settings.llm_provider,
        model=result.model_id or settings.llm_model,
        prompt_version=result.prompt_version,
        prompt_hash=result.prompt_hash or "",
        status=status or ("refused" if result.refused else "succeeded"),
        error=error,
        request=(
            {"messages": [{"role": m.role, "content": m.content} for m in messages]}
            if payloads and messages
            else None
        ),
        response={"answer": result.answer} if payloads else None,
        meta={
            "grounded": result.grounded,
            "citations": len(result.citations),
            "sources": len(result.sources),
            "retrieval_hits": result.retrieval_hits,
            "sources_above_floor": result.sources_above_floor,
            "context_tokens": result.context_tokens,
            "truncated_output": result.truncated_output,
            **(meta or {}),
        },
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=None,  # local models cost time, not money
        latency_ms=result.latency_ms,
    )
    try:
        session.add(call)
        session.commit()
    except SQLAlchemyError:
        log.warning("could not record llm_call for %s", task, exc_info=True)
        session.rollback()


def record_failure(
    session: Session,
    settings: Settings,
    *,
    task: str,
    error: str,
    prompt_hash: str = "",
    latency_ms: int | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    call = LlmCall(
        task=task,
        provider=settings.llm_provider,
        model=settings.llm_model,
        prompt_version=None,
        prompt_hash=prompt_hash,
        status="failed",
        error=error[:2000],
        meta=meta or {},
        latency_ms=latency_ms,
    )
    try:
        session.add(call)
        session.commit()
    except SQLAlchemyError:
        log.warning("could not record failed llm_call for %s", task, exc_info=True)
        session.rollback()
