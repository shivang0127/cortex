"""Worker stages of the ingestion pipeline (ARCHITECTURE.md §6).

    POST /v1/documents ──▶ ingest.parse ──▶ ingest.chunk ──▶ status = ready ──▶ embed.chunks
       (register, sync)      (worker)          (worker)                          (worker)

Each stage is one job and commits its own output, so a failure in chunking is
retried from chunking — the parse is never redone. `ingest.parse` stores the
document's structure (headings, page/segment offsets) in `documents.meta`, which
is exactly what lets `ingest.chunk` run on its own.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from secondbrain.config import get_settings
from secondbrain.db.models import Chunk, Document
from secondbrain.pipeline.chunk import ChunkingConfig, chunk_document
from secondbrain.pipeline.parse import FetchError, ParseError, parse_file, parse_url
from secondbrain.pipeline.parse.base import Heading, ParsedDocument
from secondbrain.providers.embedding import (
    EmbeddingError,
    EmbeddingsDisabled,
    embeddings_enabled,
    get_embedding_provider,
)
from secondbrain.queue import queue
from secondbrain.queue.registry import NonRetryableJobError, register
from secondbrain.services import embeddings, storage
from secondbrain.services.documents import CHUNK_JOB, PARSE_JOB, utcnow
from secondbrain.services.embeddings import EMBED_JOB, DimensionMismatch

log = logging.getLogger(__name__)

REGISTRATION_META_KEYS = {"filename", "size_bytes", "user_title"}


def _document(session: Session, payload: dict[str, Any]) -> Document:
    try:
        document_id = uuid.UUID(str(payload["document_id"]))
    except (KeyError, ValueError) as exc:
        raise NonRetryableJobError(f"payload has no valid document_id: {payload}") from exc
    document = session.get(Document, document_id, with_for_update=True)
    if document is None:
        raise NonRetryableJobError(f"document {document_id} no longer exists")
    return document


def mark_document_failed(
    session: Session, payload: dict[str, Any], error: str, final: bool
) -> None:
    """Failure hook: record the error; only a final failure flips the status."""
    document_id = payload.get("document_id")
    if not document_id:
        return
    values: dict[str, Any] = {"error": error[:2000], "updated_at": utcnow()}
    if final:
        values["status"] = "failed"
    session.execute(
        update(Document).where(Document.id == uuid.UUID(str(document_id))).values(**values)
    )


# ── Stage 1: parse ────────────────────────────────────────────────────────


def _structure_meta(parsed: ParsedDocument) -> dict[str, Any]:
    return {
        "headings": [[h.level, h.text, h.offset] for h in parsed.headings],
        "page_offsets": parsed.page_offsets,
        "segment_offsets": [[o, s] for o, s in parsed.segment_offsets],
    }


@register(PARSE_JOB, on_failure=mark_document_failed)
def parse_stage(session: Session, payload: dict[str, Any]) -> None:
    settings = get_settings()
    document = _document(session, payload)
    document.status = "processing"
    document.error = None

    try:
        if document.storage_path:
            data = storage.read_original(settings, document.storage_path)
            parsed = parse_file(
                document.kind, data, filename=document.meta.get("filename") or document.title
            )
        elif document.origin_uri:
            parsed = parse_url(document.kind, document.origin_uri)
        else:
            raise NonRetryableJobError("document has neither a stored file nor an origin URL")
    except ParseError as exc:
        raise NonRetryableJobError(str(exc)) from exc
    except FetchError as exc:
        raise RuntimeError(str(exc)) from exc  # retryable: the queue backs off and tries again
    except FileNotFoundError as exc:
        raise NonRetryableJobError(f"managed copy is missing: {exc}") from exc

    if not parsed.text.strip():
        raise NonRetryableJobError("no text could be extracted from the source")

    document.raw_text = parsed.text
    user_title = document.meta.get("user_title")
    document.title = user_title or parsed.title or document.title
    # Registration-time metadata is kept; everything the previous parse derived is
    # replaced, so a reprocess never carries stale values forward.
    document.meta = {
        **{k: v for k, v in document.meta.items() if k in REGISTRATION_META_KEYS},
        **parsed.meta,
        "structure": _structure_meta(parsed),
        "text_sha256": storage.sha256_hex(parsed.text.encode("utf-8")),
        "char_count": len(parsed.text),
        "parsed_at": utcnow().isoformat(),
    }
    queue.enqueue(session, CHUNK_JOB, {"document_id": str(document.id)})
    log.info(
        "parsed %s: %d chars, %d headings", document.id, len(parsed.text), len(parsed.headings)
    )


# ── Stage 2: chunk ────────────────────────────────────────────────────────


def _parsed_from_document(document: Document) -> ParsedDocument:
    structure = document.meta.get("structure", {})
    return ParsedDocument(
        title=document.title,
        text=document.raw_text or "",
        headings=[
            Heading(int(lvl), str(txt), int(off)) for lvl, txt, off in structure.get("headings", [])
        ],
        page_offsets=[int(o) for o in structure.get("page_offsets", [])],
        segment_offsets=[(int(o), float(s)) for o, s in structure.get("segment_offsets", [])],
    )


def chunking_config() -> ChunkingConfig:
    s = get_settings()
    return ChunkingConfig(
        target_tokens=s.chunk_target_tokens,
        max_tokens=s.chunk_max_tokens,
        overlap_ratio=s.chunk_overlap_ratio,
        parent_max_tokens=s.chunk_parent_max_tokens,
    )


@register(CHUNK_JOB, on_failure=mark_document_failed)
def chunk_stage(session: Session, payload: dict[str, Any]) -> None:
    document = _document(session, payload)
    if not document.raw_text:
        raise NonRetryableJobError("document has no text to chunk; run ingest.parse first")

    specs = chunk_document(_parsed_from_document(document), chunking_config())

    # Supersede rather than delete: anything a later phase attached to the old
    # chunks (evidence) keeps pointing at real rows. Their embeddings are derived
    # data (L4) and only ever serve search, so those go.
    now = utcnow()
    superseded_ids = list(
        session.execute(
            select(Chunk.id).where(Chunk.document_id == document.id, Chunk.superseded_at.is_(None))
        ).scalars()
    )
    embeddings.delete_embeddings_for_chunks(session, superseded_ids)
    session.execute(update(Chunk).where(Chunk.id.in_(superseded_ids)).values(superseded_at=now))

    parents: dict[int, Chunk] = {}
    for spec in specs:  # parents precede their children in ordinal order
        chunk = Chunk(
            document_id=document.id,
            ordinal=spec.ordinal,
            text=spec.text,
            heading_path=spec.heading_path,
            page_start=spec.page_start,
            page_end=spec.page_end,
            char_start=spec.char_start,
            char_end=spec.char_end,
            token_count=spec.token_count,
        )
        if spec.parent_ordinal is None:
            parents[spec.ordinal] = chunk
        else:
            chunk.parent = parents[spec.parent_ordinal]
        session.add(chunk)
    session.flush()

    retrieval = sum(1 for s in specs if s.parent_ordinal is not None)
    document.meta = {
        **document.meta,
        "chunk_count": retrieval,
        "section_count": len(parents),
        "chunked_at": now.isoformat(),
    }
    document.status = "ready"
    document.error = None
    log.info("chunked %s: %d sections, %d retrieval chunks", document.id, len(parents), retrieval)
    if retrieval and embeddings_enabled():
        queue.enqueue(session, EMBED_JOB, {"document_id": str(document.id)})


# ── Stage 3: embed ────────────────────────────────────────────────────────


@register(EMBED_JOB)
def embed_stage(session: Session, payload: dict[str, Any]) -> None:
    """Embed the document's retrieval chunks with the configured model (idempotent)."""
    settings = get_settings()
    document = _document(session, payload)
    try:
        provider = get_embedding_provider()
    except EmbeddingsDisabled as exc:
        raise NonRetryableJobError(str(exc)) from exc
    try:
        written = embeddings.embed_document(
            session,
            document.id,
            provider,
            expected_dimension=settings.embedding_dimension,
            force=bool(payload.get("force")),
            batch_size=settings.embedding_batch_size,
        )
    except DimensionMismatch as exc:
        raise NonRetryableJobError(str(exc)) from exc
    except EmbeddingError as exc:
        raise RuntimeError(str(exc)) from exc  # model download / runtime trouble: retry later
    log.info("embedded %s: %d chunks with %s", document.id, written, provider.model_id)
