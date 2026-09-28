from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from secondbrain.config import Settings, get_settings
from secondbrain.db.engine import get_session
from secondbrain.providers.embedding import active_model_id, embeddings_enabled
from secondbrain.schemas.search import EmbeddingStatusOut, IndexRequest, IndexResponse
from secondbrain.services import embeddings as svc

router = APIRouter(prefix="/embeddings", tags=["embeddings"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/status", response_model=EmbeddingStatusOut, summary="Index coverage")
def embedding_status(session: SessionDep, settings: SettingsDep) -> EmbeddingStatusOut:
    enabled = embeddings_enabled(settings)
    model = active_model_id()
    st = svc.status(session, model or "", settings.embedding_dimension)
    return EmbeddingStatusOut(
        enabled=enabled,
        provider=settings.embedding_provider if enabled else None,
        model=model,
        dimension=st.dimension,
        chunks_total=st.chunks_total,
        chunks_embedded=st.chunks_embedded if enabled else 0,
        documents_total=st.documents_total,
        documents_complete=st.documents_complete if enabled else 0,
        jobs_queued=st.jobs_queued,
        jobs_running=st.jobs_running,
        jobs_failed=st.jobs_failed,
    )


@router.post(
    "/index",
    response_model=IndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue embedding jobs for documents that still need them (resumable)",
)
def index_embeddings(
    session: SessionDep, settings: SettingsDep, body: IndexRequest | None = None
) -> IndexResponse:
    if not embeddings_enabled(settings):
        raise HTTPException(409, "embeddings are disabled (EMBEDDING_PROVIDER=none)")
    force = bool(body and body.force)
    model = active_model_id() or ""
    enqueued, skipped = svc.enqueue_index(session, model, force=force)
    session.commit()
    return IndexResponse(enqueued=enqueued, skipped=skipped, model=model)
