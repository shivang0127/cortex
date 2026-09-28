import uuid
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from secondbrain.config import Settings, get_settings
from secondbrain.db.engine import get_session
from secondbrain.db.models import Document, Job
from secondbrain.providers.embedding import active_model_id, embeddings_enabled
from secondbrain.schemas.documents import (
    ChunkListOut,
    ChunkOut,
    DocumentDetailOut,
    DocumentListOut,
    DocumentOut,
    ImportResponse,
    JobOut,
    SubjectOut,
)
from secondbrain.services import documents as svc
from secondbrain.services.documents import DocumentNotFound, ImportOptions, UnsupportedSource
from secondbrain.services.embeddings import embedded_chunk_count

router = APIRouter(prefix="/documents", tags=["documents"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_HIDDEN_META = {"structure"}  # large and internal; served via /chunks instead


def _embedding_model(settings: Settings) -> str | None:
    return active_model_id() if embeddings_enabled(settings) else None


def _embedded(session: Session, settings: Settings, document_id: uuid.UUID) -> int:
    model = _embedding_model(settings)
    return embedded_chunk_count(session, document_id, model) if model else 0


def to_document_out(document: Document, chunk_count: int, embedded: int = 0) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        kind=document.kind,
        title=document.title,
        origin_uri=document.origin_uri,
        storage_path=document.storage_path,
        content_hash=document.content_hash,
        status=document.status,
        error=document.error,
        week=document.week,
        classified_by=document.classified_by,
        subjects=[SubjectOut.model_validate(s) for s in document.subjects],
        chunk_count=chunk_count,
        embedded_chunk_count=embedded,
        meta={k: v for k, v in document.meta.items() if k not in _HIDDEN_META},
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _job_out(job: Job | None) -> JobOut | None:
    return JobOut.model_validate(job) if job is not None else None


def _split_subjects(values: list[str]) -> list[str]:
    """Accept repeated form fields and/or comma-separated values."""
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


@router.post(
    "",
    response_model=ImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Import a file or a URL",
    responses={
        200: {
            "model": ImportResponse,
            "description": "Already imported — the existing document is returned",
        }
    },
)
def import_document(
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
    file: Annotated[UploadFile | None, File(description="PDF, Markdown, text or DOCX")] = None,
    url: Annotated[str | None, Form(description="Web page or YouTube video URL")] = None,
    subjects: Annotated[
        list[str] | None, Form(description="Subject names; repeat or comma-separate")
    ] = None,
    week: Annotated[int | None, Form(ge=0, le=60)] = None,
    title: Annotated[str | None, Form(max_length=500)] = None,
) -> ImportResponse:
    if (file is None) == (url is None):
        raise HTTPException(422, "provide exactly one of `file` or `url`")
    options = ImportOptions(subjects=_split_subjects(subjects or []), week=week, title=title)
    try:
        if file is not None:
            data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
            result = svc.register_file(
                session, settings, filename=file.filename or "upload", data=data, options=options
            )
        else:
            result = svc.register_url(session, settings, url=url or "", options=options)
    except UnsupportedSource as exc:
        code = 413 if "upload limit" in str(exc) else 415
        raise HTTPException(code, str(exc)) from exc
    session.commit()

    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    document = svc.get_document(session, result.document.id)
    return ImportResponse(
        document=to_document_out(document, svc.chunk_count(session, document.id)),
        job=_job_out(result.job),
        duplicate=result.duplicate,
    )


@router.get("", response_model=DocumentListOut, summary="Library listing")
def list_documents(
    session: SessionDep,
    settings: SettingsDep,
    q: Annotated[
        str | None, Query(max_length=200, description="Title / filename lookup (case-insensitive)")
    ] = None,
    subject_id: uuid.UUID | None = None,
    week: Annotated[int | None, Query(ge=0)] = None,
    status_filter: Annotated[
        Literal["pending", "processing", "ready", "failed"] | None, Query(alias="status")
    ] = None,
    kind: Annotated[
        Literal["pdf", "markdown", "text", "docx", "web", "youtube"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListOut:
    rows, total = svc.list_documents(
        session,
        query=q,
        subject_id=subject_id,
        week=week,
        status=status_filter,
        kind=kind,
        embedding_model=_embedding_model(settings),
        limit=limit,
        offset=offset,
    )
    return DocumentListOut(items=[to_document_out(d, n, e) for d, n, e in rows], total=total)


@router.get("/{document_id}", response_model=DocumentDetailOut, summary="Document + its jobs")
def get_document(
    session: SessionDep, settings: SettingsDep, document_id: uuid.UUID
) -> DocumentDetailOut:
    try:
        document = svc.get_document(session, document_id)
    except DocumentNotFound as exc:
        raise HTTPException(404, "document not found") from exc
    base = to_document_out(
        document, svc.chunk_count(session, document.id), _embedded(session, settings, document.id)
    )
    jobs = [JobOut.model_validate(j) for j in svc.document_jobs(session, document.id)]
    return DocumentDetailOut(**base.model_dump(), jobs=jobs)


@router.get("/{document_id}/chunks", response_model=ChunkListOut, summary="Chunk inspector")
def list_chunks(
    session: SessionDep,
    document_id: uuid.UUID,
    level: Annotated[Literal["all", "sections", "retrieval"], Query()] = "all",
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChunkListOut:
    try:
        svc.get_document(session, document_id)
    except DocumentNotFound as exc:
        raise HTTPException(404, "document not found") from exc
    chunks, total = svc.list_chunks(session, document_id, level=level, limit=limit, offset=offset)
    return ChunkListOut(items=[ChunkOut.model_validate(c) for c in chunks], total=total)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete")
def delete_document(session: SessionDep, settings: SettingsDep, document_id: uuid.UUID) -> None:
    try:
        svc.delete_document(session, settings, document_id)
    except DocumentNotFound as exc:
        raise HTTPException(404, "document not found") from exc
    session.commit()


@router.post(
    "/{document_id}/reprocess",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-parse and re-chunk from the managed copy / origin URL",
)
def reprocess_document(session: SessionDep, document_id: uuid.UUID) -> JobOut:
    try:
        job = svc.reprocess_document(session, document_id)
    except DocumentNotFound as exc:
        raise HTTPException(404, "document not found") from exc
    session.commit()
    return JobOut.model_validate(job)
