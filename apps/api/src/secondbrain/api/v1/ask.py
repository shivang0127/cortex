"""Ask Second Brain: grounded answers with citations.

Generation runs **inside the request**, not through the worker. The queue
exists for durable, retryable, restart-surviving batch work (ingestion,
embedding); a question is interactive and worthless answered thirty seconds
after the user has gone. A local 4B model takes several seconds, which is why
the streaming endpoint exists rather than a spinner.
"""

import json
import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from secondbrain.config import Settings, get_settings
from secondbrain.db.engine import get_session, get_session_factory
from secondbrain.providers.embedding import EmbeddingsDisabled, get_embedding_provider
from secondbrain.providers.embedding.base import EmbeddingError
from secondbrain.providers.llm import LLMDisabled, get_llm_provider
from secondbrain.providers.llm.base import LLMError, LLMTimeout, LLMUnavailable
from secondbrain.schemas.rag import (
    AskRequest,
    AskResponse,
    CitationOut,
    GenerationInfo,
    RetrievalInfo,
    SourceOut,
)
from secondbrain.services import llm_calls, rag
from secondbrain.services.rag import AnswerResult, Source
from secondbrain.services.search import SearchFilters

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ask", tags=["ask"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# ── shared plumbing ───────────────────────────────────────────────────────


def _providers(settings: Settings, mode: str):
    """The LLM is always needed; the embedding provider only outside keyword mode."""
    try:
        llm = get_llm_provider()
    except LLMDisabled as exc:
        raise HTTPException(409, f"answering is disabled ({exc})") from exc
    embedder = None
    if mode != "keyword":
        try:
            embedder = get_embedding_provider()
        except EmbeddingsDisabled as exc:
            raise HTTPException(
                409, f"semantic retrieval is disabled ({exc}); ask with mode=keyword"
            ) from exc
    return llm, embedder


def _filters(body: AskRequest) -> SearchFilters:
    return SearchFilters(
        subject_id=body.subject_id, week=body.week, kind=body.kind, document_id=body.document_id
    )


def _source_out(source: Source) -> SourceOut:
    c, d, hit = source.chunk, source.document, source.hit
    return SourceOut(
        marker=source.marker,
        chunk_id=c.id,
        document_id=d.id,
        document_title=d.title,
        document_kind=d.kind,
        origin_uri=d.origin_uri,
        subjects=[s.name for s in d.subjects],
        week=d.week,
        heading_path=c.heading_path,
        page_start=c.page_start,
        page_end=c.page_end,
        char_start=c.char_start,
        char_end=c.char_end,
        ordinal=c.ordinal,
        text=source.text,
        truncated=source.truncated,
        score=hit.score,
        similarity=hit.similarity,
        semantic_rank=hit.semantic_rank,
        keyword_rank=hit.keyword_rank,
    )


def _citation_out(citation: rag.Citation) -> CitationOut:
    c, d = citation.source.chunk, citation.source.document
    return CitationOut(
        marker=citation.marker,
        chunk_id=c.id,
        document_id=d.id,
        document_title=d.title,
        heading_path=c.heading_path,
        page_start=c.page_start,
        page_end=c.page_end,
        char_start=c.char_start,
        char_end=c.char_end,
        text=citation.source.text,
    )


def _response(result: AnswerResult, settings: Settings, body: AskRequest, embedder) -> AskResponse:
    return AskResponse(
        question=result.question,
        answer=result.answer,
        refused=result.refused,
        grounded=result.grounded,
        citations=[_citation_out(c) for c in result.citations],
        sources=[_source_out(s) for s in result.sources],
        retrieval=RetrievalInfo(
            mode=body.mode or settings.rag_mode,
            embedding_model=embedder.model_id if embedder else None,
            hits=result.retrieval_hits,
            above_floor=result.sources_above_floor,
            min_similarity=settings.rag_min_similarity,
            top_k=body.top_k or settings.rag_top_k,
            context_tokens=result.context_tokens,
        ),
        generation=GenerationInfo(
            provider=result.provider_id,
            model=result.model_id,
            prompt_version=result.prompt_version,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            truncated=result.truncated_output,
        ),
    )


def _http_error(exc: LLMError) -> HTTPException:
    if isinstance(exc, LLMUnavailable):
        return HTTPException(503, f"the language model is unavailable: {exc}")
    if isinstance(exc, LLMTimeout):
        return HTTPException(504, f"the language model timed out: {exc}")
    return HTTPException(502, f"the language model failed: {exc}")


# ── endpoints ─────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=AskResponse,
    summary="Answer a question from your library, with citations",
)
def ask(session: SessionDep, settings: SettingsDep, body: AskRequest) -> AskResponse:
    if not body.question.strip():
        raise HTTPException(422, "question must not be blank")
    llm, embedder = _providers(settings, body.mode or settings.rag_mode)
    try:
        result = rag.answer(
            session,
            body.question,
            settings,
            embedding_provider=embedder,
            llm=llm,
            mode=body.mode,
            top_k=body.top_k,
            filters=_filters(body),
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except EmbeddingError as exc:
        raise HTTPException(503, f"embedding model unavailable: {exc}") from exc
    except LLMError as exc:
        llm_calls.record_failure(session, settings, task=rag.TASK, error=str(exc))
        raise _http_error(exc) from exc

    llm_calls.record(session, settings, task=rag.TASK, result=result, messages=result.messages)
    return _response(result, settings, body, embedder)


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post(
    "/stream",
    summary="The same answer, streamed as server-sent events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "`sources`, then `delta` events, then `result` (or `error`)",
        }
    },
)
def ask_stream(settings: SettingsDep, body: AskRequest) -> StreamingResponse:
    if not body.question.strip():
        raise HTTPException(422, "question must not be blank")
    llm, embedder = _providers(settings, body.mode or settings.rag_mode)

    def events() -> Iterator[str]:
        # Its own session: the generator outlives the request-scoped dependency.
        with get_session_factory()() as session:
            try:
                for kind, payload in rag.stream_answer(
                    session,
                    body.question,
                    settings,
                    embedding_provider=embedder,
                    llm=llm,
                    mode=body.mode,
                    top_k=body.top_k,
                    filters=_filters(body),
                    temperature=body.temperature,
                    max_tokens=body.max_tokens,
                ):
                    if kind == "sources":
                        yield _sse(
                            "sources",
                            [_source_out(s).model_dump(mode="json") for s in payload],
                        )
                    elif kind == "delta":
                        yield _sse("delta", {"text": payload})
                    else:
                        result: AnswerResult = payload  # type: ignore[assignment]
                        llm_calls.record(
                            session,
                            settings,
                            task=rag.TASK,
                            result=result,
                            messages=result.messages,
                        )
                        yield _sse(
                            "result",
                            _response(result, settings, body, embedder).model_dump(mode="json"),
                        )
            except (LLMError, EmbeddingError) as exc:
                log.warning("ask stream failed: %s", exc)
                llm_calls.record_failure(session, settings, task=rag.TASK, error=str(exc))
                status = 503 if isinstance(exc, LLMUnavailable | EmbeddingError) else 502
                if isinstance(exc, LLMTimeout):
                    status = 504
                yield _sse("error", {"status": status, "detail": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
