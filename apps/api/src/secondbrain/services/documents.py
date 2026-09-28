"""Document registration and library queries.

Registration is the synchronous half of ingestion (ARCHITECTURE.md §6,
`ingest.register`): hash, dedupe, copy the original into managed storage,
apply the user's metadata, and enqueue the first worker stage. Everything that
can take more than a moment — fetching, parsing, chunking — happens in the
worker (`secondbrain.pipeline.stages`).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from secondbrain.config import Settings
from secondbrain.db.models import Chunk, Document, Job, Subject, document_subjects
from secondbrain.pipeline.parse import (
    ParseError,
    detect_file_kind,
    detect_url_kind,
    stem_of,
)
from secondbrain.queue import queue
from secondbrain.services import storage
from secondbrain.services.embeddings import embedded_count_subquery
from secondbrain.services.subjects import get_or_create_subjects

PARSE_JOB = "ingest.parse"
CHUNK_JOB = "ingest.chunk"


class UnsupportedSource(ValueError):
    """The upload or URL cannot be ingested. Maps to HTTP 415 / 422."""


class DocumentNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ImportOptions:
    subjects: list[str]
    week: int | None = None
    title: str | None = None


@dataclass(frozen=True)
class ImportResult:
    document: Document
    job: Job | None  # None when the import was a duplicate and nothing was enqueued
    duplicate: bool


def _apply_options(session: Session, document: Document, options: ImportOptions) -> None:
    """User-supplied metadata always wins and is recorded as such (`classified_by`)."""
    if options.subjects:
        subjects = get_or_create_subjects(session, options.subjects)
        present = {s.id for s in document.subjects}
        document.subjects.extend(s for s in subjects if s.id not in present)
    if options.week is not None:
        document.week = options.week
    if options.title:
        document.title = options.title.strip()
    if options.subjects or options.week is not None:
        document.classified_by = "user"


def _enqueue_parse(session: Session, document: Document) -> Job:
    document.status = "pending"
    document.error = None
    return queue.enqueue(session, PARSE_JOB, {"document_id": str(document.id)})


def _existing_by_hash(session: Session, content_hash: str) -> Document | None:
    return session.execute(
        select(Document).where(Document.content_hash == content_hash)
    ).scalar_one_or_none()


def register_file(
    session: Session,
    settings: Settings,
    *,
    filename: str,
    data: bytes,
    options: ImportOptions,
) -> ImportResult:
    if not data:
        raise UnsupportedSource("the uploaded file is empty")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise UnsupportedSource(f"file exceeds the {settings.max_upload_mb} MB upload limit")
    try:
        kind = detect_file_kind(filename, data[:16])
    except ParseError as exc:
        raise UnsupportedSource(str(exc)) from exc

    content_hash = storage.sha256_hex(data)
    if existing := _existing_by_hash(session, content_hash):
        # Re-importing the same bytes is a no-op — but new subjects/week still apply.
        _apply_options(session, existing, options)
        session.flush()
        return ImportResult(existing, None, duplicate=True)

    extension = PurePosixPath(filename).suffix
    relative = storage.store_original(settings, data, content_hash, extension)
    document = Document(
        kind=kind,
        title=options.title.strip() if options.title else stem_of(filename),
        origin_uri=filename,
        storage_path=relative,
        content_hash=content_hash,
        meta={"filename": PurePosixPath(filename).name, "size_bytes": len(data)},
    )
    session.add(document)
    session.flush()
    _apply_options(session, document, options)
    if options.title:
        document.meta = {**document.meta, "user_title": options.title.strip()}
    job = _enqueue_parse(session, document)
    return ImportResult(document, job, duplicate=False)


def register_url(
    session: Session, settings: Settings, *, url: str, options: ImportOptions
) -> ImportResult:
    try:
        kind = detect_url_kind(url)
    except ValueError as exc:
        raise UnsupportedSource(str(exc)) from exc

    url = url.strip()
    content_hash = storage.url_content_hash(url)
    if existing := _existing_by_hash(session, content_hash):
        _apply_options(session, existing, options)
        session.flush()
        return ImportResult(existing, None, duplicate=True)

    document = Document(
        kind=kind,
        title=options.title.strip() if options.title else url,
        origin_uri=url,
        storage_path=None,
        content_hash=content_hash,
        meta={"user_title": options.title.strip()} if options.title else {},
    )
    session.add(document)
    session.flush()
    _apply_options(session, document, options)
    job = _enqueue_parse(session, document)
    return ImportResult(document, job, duplicate=False)


# ── Queries ──────────────────────────────────────────────────────────────


def live_chunk_count_subquery():
    return (
        select(Chunk.document_id, func.count(Chunk.id).label("n"))
        .where(Chunk.superseded_at.is_(None), Chunk.parent_id.is_not(None))
        .group_by(Chunk.document_id)
        .subquery()
    )


def _like_pattern(query: str) -> str:
    r"""Substring pattern for ILIKE with the user's `%`, `_` and `\` taken literally."""
    escaped = query.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def list_documents(
    session: Session,
    *,
    query: str | None = None,
    subject_id: uuid.UUID | None = None,
    week: int | None = None,
    status: str | None = None,
    kind: str | None = None,
    embedding_model: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[tuple[Document, int, int]], int]:
    """Documents newest first with (retrieval-chunk count, embedded count), plus the total.

    `query` is a case-insensitive substring lookup over the title and the original
    filename/URL — a way to find a document you know, not content search (that is
    Phase 2). ILIKE over a few thousand rows is instant; no index needed yet.
    """
    counts = live_chunk_count_subquery()
    embedded = embedded_count_subquery(embedding_model or "")
    stmt = (
        select(Document, func.coalesce(counts.c.n, 0), func.coalesce(embedded.c.n, 0))
        .outerjoin(counts, counts.c.document_id == Document.id)
        .outerjoin(embedded, embedded.c.document_id == Document.id)
        .options(selectinload(Document.subjects))
    )
    if query and query.strip():
        pattern = _like_pattern(query.strip())
        stmt = stmt.where(
            Document.title.ilike(pattern, escape="\\")
            | Document.origin_uri.ilike(pattern, escape="\\")
        )
    if subject_id is not None:
        stmt = stmt.where(
            Document.id.in_(
                select(document_subjects.c.document_id).where(
                    document_subjects.c.subject_id == subject_id
                )
            )
        )
    if week is not None:
        stmt = stmt.where(Document.week == week)
    if status is not None:
        stmt = stmt.where(Document.status == status)
    if kind is not None:
        stmt = stmt.where(Document.kind == kind)

    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = session.execute(
        stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return [(doc, n, e) for doc, n, e in rows], total


def get_document(session: Session, document_id: uuid.UUID) -> Document:
    document = session.get(Document, document_id, options=[selectinload(Document.subjects)])
    if document is None:
        raise DocumentNotFound(str(document_id))
    return document


def chunk_count(session: Session, document_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count(Chunk.id)).where(
            Chunk.document_id == document_id,
            Chunk.superseded_at.is_(None),
            Chunk.parent_id.is_not(None),
        )
    ).scalar_one()


def list_chunks(
    session: Session,
    document_id: uuid.UUID,
    *,
    level: str = "all",
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[Chunk], int]:
    stmt = select(Chunk).where(Chunk.document_id == document_id, Chunk.superseded_at.is_(None))
    if level == "sections":
        stmt = stmt.where(Chunk.parent_id.is_(None))
    elif level == "retrieval":
        stmt = stmt.where(Chunk.parent_id.is_not(None))
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    chunks = list(
        session.execute(stmt.order_by(Chunk.ordinal).limit(limit).offset(offset)).scalars()
    )
    return chunks, total


def document_jobs(session: Session, document_id: uuid.UUID, limit: int = 10) -> list[Job]:
    stmt = (
        select(Job)
        .where(Job.payload["document_id"].astext == str(document_id))
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def delete_document(session: Session, settings: Settings, document_id: uuid.UUID) -> None:
    document = get_document(session, document_id)
    if document.storage_path:
        storage.delete_original(settings, document.storage_path)
    # Pending work for a document that no longer exists is pointless; finished
    # jobs stay as history.
    session.execute(
        update(Job)
        .where(Job.payload["document_id"].astext == str(document_id), Job.status == "queued")
        .values(status="cancelled", finished_at=utcnow())
    )
    session.delete(document)  # chunks and subject links cascade in the database
    session.flush()


def reprocess_document(session: Session, document_id: uuid.UUID) -> Job:
    """Re-run parsing and chunking from the managed copy / origin URL."""
    document = get_document(session, document_id)
    return _enqueue_parse(session, document)


def get_subject(session: Session, subject_id: uuid.UUID) -> Subject | None:
    return session.get(Subject, subject_id)


def utcnow() -> datetime:
    return datetime.now(UTC)
