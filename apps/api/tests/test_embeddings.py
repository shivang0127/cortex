"""Embedding pipeline against the real database (fake provider, real pgvector):

chunks → embed.chunks job → chunk_embeddings rows → coverage / index endpoints
"""

import pytest
from sqlalchemy import func, select
from tests.conftest import SAMPLE_MARKDOWN

from secondbrain.config import get_settings
from secondbrain.db.engine import get_session_factory
from secondbrain.db.models import Chunk, ChunkEmbedding
from secondbrain.pipeline import stages
from secondbrain.providers.embedding import reset_embedding_provider_cache
from secondbrain.providers.embedding.base import EmbeddingError
from secondbrain.providers.embedding.fake import FakeEmbeddingProvider
from secondbrain.services import embeddings as svc

pytestmark = pytest.mark.integration

MODEL = "fake-bow-v1"


def _drain(worker) -> int:
    ran = 0
    while worker.run_once():
        ran += 1
    return ran


def _rows(document_id: str, model: str = MODEL) -> list[ChunkEmbedding]:
    with get_session_factory()() as session:
        return list(
            session.execute(
                select(ChunkEmbedding)
                .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
                .where(Chunk.document_id == document_id, ChunkEmbedding.model == model)
            ).scalars()
        )


def _ingest(library, worker, name: str = "notes.md", data: bytes | None = None) -> str:
    document_id = library.upload(name, data or SAMPLE_MARKDOWN.encode()).json()["document"]["id"]
    _drain(worker)  # parse → chunk → embed
    return document_id


def test_ingestion_chains_into_embedding(library, worker) -> None:
    document_id = _ingest(library, worker)
    document = library.client.get(f"/v1/documents/{document_id}").json()
    assert [j["type"] for j in document["jobs"]] == ["embed.chunks", "ingest.chunk", "ingest.parse"]
    assert all(j["status"] == "succeeded" for j in document["jobs"])
    rows = _rows(document_id)
    assert len(rows) == document["chunk_count"] == document["embedded_chunk_count"] > 0
    assert all(r.model == MODEL and len(r.embedding) == 384 for r in rows)
    with get_session_factory()() as session:
        section_ids = set(
            session.execute(
                select(Chunk.id).where(Chunk.document_id == document_id, Chunk.parent_id.is_(None))
            ).scalars()
        )
    assert not {r.chunk_id for r in rows} & section_ids, "only retrieval chunks are embedded"


def test_embedding_is_idempotent_and_force_regenerates(library, worker) -> None:
    document_id = _ingest(library, worker)
    before = {r.chunk_id: r.id for r in _rows(document_id)}

    with get_session_factory().begin() as session:
        written = svc.embed_document(
            session, document_id, FakeEmbeddingProvider(), expected_dimension=384
        )
    assert written == 0, "nothing to do when every chunk already has a vector"
    assert {r.chunk_id: r.id for r in _rows(document_id)} == before

    with get_session_factory().begin() as session:
        written = svc.embed_document(
            session, document_id, FakeEmbeddingProvider(), expected_dimension=384, force=True
        )
    after = {r.chunk_id: r.id for r in _rows(document_id)}
    assert written == len(before)
    assert set(after) == set(before) and not set(after.values()) & set(before.values())


def test_models_coexist_and_dimension_is_guarded(library, worker) -> None:
    document_id = _ingest(library, worker)
    other = FakeEmbeddingProvider(model_id="fake-other-v9")
    with get_session_factory().begin() as session:
        svc.embed_document(session, document_id, other, expected_dimension=384)
    assert len(_rows(document_id, "fake-other-v9")) == len(_rows(document_id, MODEL)) > 0

    wrong = FakeEmbeddingProvider(dimension=768, model_id="fake-768")
    with (
        get_session_factory().begin() as session,
        pytest.raises(svc.DimensionMismatch, match="migration"),
    ):
        svc.embed_document(session, document_id, wrong, expected_dimension=384)


def test_transient_embedding_error_is_retried(library, worker, monkeypatch) -> None:
    class Flaky(FakeEmbeddingProvider):
        def embed_documents(self, texts):
            raise EmbeddingError("model download interrupted")

    monkeypatch.setattr(stages, "get_embedding_provider", lambda: Flaky())
    document_id = library.upload("flaky.md", SAMPLE_MARKDOWN.encode()).json()["document"]["id"]
    assert worker.run_once() and worker.run_once()  # parse, chunk
    assert worker.run_once()  # embed → fails, retry scheduled
    (embed_job,) = [
        j
        for j in library.client.get(f"/v1/documents/{document_id}").json()["jobs"]
        if j["type"] == "embed.chunks"
    ]
    assert embed_job["status"] == "queued" and embed_job["attempts"] == 1
    assert "download interrupted" in embed_job["last_error"]
    assert _rows(document_id) == []
    # The document itself stays ready: embedding is derived data, not ingestion.
    assert library.client.get(f"/v1/documents/{document_id}").json()["status"] == "ready"


def test_disabled_provider_fails_embed_jobs_without_retry(library, worker, settings_env) -> None:
    document_id = library.upload("off.md", SAMPLE_MARKDOWN.encode()).json()["document"]["id"]
    assert worker.run_once() and worker.run_once()
    settings_env.setenv("EMBEDDING_PROVIDER", "none")
    get_settings.cache_clear()
    reset_embedding_provider_cache()
    assert worker.run_once()
    (embed_job,) = [
        j
        for j in library.client.get(f"/v1/documents/{document_id}").json()["jobs"]
        if j["type"] == "embed.chunks"
    ]
    assert embed_job["status"] == "failed" and embed_job["attempts"] == 1


def test_reprocess_drops_vectors_of_superseded_chunks(library, worker) -> None:
    document_id = _ingest(library, worker)
    live_before = {r.chunk_id for r in _rows(document_id)}
    library.client.post(f"/v1/documents/{document_id}/reprocess")
    _drain(worker)
    rows = _rows(document_id)
    with get_session_factory()() as session:
        live_now = set(
            session.execute(
                select(Chunk.id).where(
                    Chunk.document_id == document_id,
                    Chunk.superseded_at.is_(None),
                    Chunk.parent_id.is_not(None),
                )
            ).scalars()
        )
        orphaned = session.execute(
            select(func.count(ChunkEmbedding.id))
            .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
            .where(Chunk.document_id == document_id, Chunk.superseded_at.is_not(None))
        ).scalar_one()
    assert {r.chunk_id for r in rows} == live_now and not live_now & live_before
    assert orphaned == 0, "superseded chunks keep no vectors"


def test_status_and_index_endpoints_are_resumable(library, worker) -> None:
    # Two documents chunked but with the embed jobs removed → coverage gaps.
    ids = []
    for name in ("a.md", "b.md"):
        text = SAMPLE_MARKDOWN.replace("Automata", name)
        ids.append(library.upload(name, text.encode()).json()["document"]["id"])
    for _ in range(4):
        assert worker.run_once()  # parse+chunk for both; embed jobs stay queued
    with get_session_factory().begin() as session:
        from sqlalchemy import delete

        from secondbrain.db.models import Job

        session.execute(delete(Job).where(Job.type == "embed.chunks", Job.status == "queued"))

    def status():
        return library.client.get("/v1/embeddings/status").json()

    st = status()
    assert st["enabled"] and st["model"] == MODEL and st["dimension"] == 384
    assert st["chunks_embedded"] == 0 and st["documents_complete"] == 0
    assert st["chunks_total"] > 0 and st["documents_total"] >= 2

    first = library.client.post("/v1/embeddings/index", json={"force": False})
    assert first.status_code == 202
    assert first.json()["enqueued"] >= 2 and first.json()["model"] == MODEL
    again = library.client.post("/v1/embeddings/index").json()
    assert again["enqueued"] == 0 and again["skipped"] >= 2, "jobs already waiting are not doubled"

    _drain(worker)
    st = status()
    assert st["chunks_embedded"] == st["chunks_total"]
    assert st["documents_complete"] == st["documents_total"]
    assert st["jobs_queued"] == 0 and st["jobs_failed"] == 0
    assert library.client.post("/v1/embeddings/index").json()["enqueued"] == 0, "nothing left"

    forced = library.client.post("/v1/embeddings/index", json={"force": True}).json()
    assert forced["enqueued"] >= 2
    _drain(worker)
    assert status()["chunks_embedded"] == st["chunks_total"]


def test_index_endpoint_refuses_when_disabled(library, settings_env) -> None:
    settings_env.setenv("EMBEDDING_PROVIDER", "none")
    get_settings.cache_clear()
    assert library.client.post("/v1/embeddings/index").status_code == 409
    st = library.client.get("/v1/embeddings/status").json()
    assert st["enabled"] is False and st["model"] is None
