"""Ingestion end to end against the real database:

POST /v1/documents → jobs table → Worker.run_once() ×2 → chunks → GET endpoints
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from tests.conftest import SAMPLE_MARKDOWN, make_docx, make_pdf
from tests.test_parsers import ARTICLE_HTML

from secondbrain.config import get_settings
from secondbrain.db.engine import get_session_factory
from secondbrain.db.models import Chunk, Job
from secondbrain.pipeline.parse import web, youtube
from secondbrain.pipeline.parse.base import FetchError
from secondbrain.pipeline.parse.youtube import Snippet, VideoInfo

pytestmark = pytest.mark.integration


def _run_pipeline(worker, stages: int = 2) -> None:
    for _ in range(stages):
        assert worker.run_once(), "expected a job to be waiting"


def _drain(worker, at_least: int = 1) -> int:
    """Run every queued job (parse, chunk, embed, …) until the queue is idle."""
    ran = 0
    while worker.run_once():
        ran += 1
    assert ran >= at_least, f"expected at least {at_least} job(s), ran {ran}"
    return ran


def _document(client, document_id: str) -> dict:
    response = client.get(f"/v1/documents/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ── registration ─────────────────────────────────────────────────────────


def test_upload_registers_copies_and_enqueues(library, tmp_path: Path) -> None:
    subject = library.subject("TCS")
    response = library.upload("notes.md", SAMPLE_MARKDOWN.encode(), subjects=[subject], week=7)
    assert response.status_code == 202, response.text
    body = response.json()
    document, job = body["document"], body["job"]
    assert body["duplicate"] is False
    assert document["kind"] == "markdown"
    assert document["status"] == "pending"
    assert document["week"] == 7
    assert document["classified_by"] == "user"
    assert [s["name"] for s in document["subjects"]] == [subject]
    assert document["origin_uri"] == "notes.md"
    assert job["type"] == "ingest.parse" and job["status"] == "queued"

    # The original is in managed storage, content-addressed.
    settings = get_settings()
    stored = settings.data_dir / document["storage_path"]
    assert stored.read_bytes() == SAMPLE_MARKDOWN.encode()
    assert document["content_hash"] in stored.name
    assert str(settings.data_dir).startswith(str(tmp_path)), "tests must not write to data/"


def test_duplicate_upload_is_a_no_op_that_merges_subjects(library) -> None:
    a, b = library.subject("A"), library.subject("B")
    first = library.upload("one.md", SAMPLE_MARKDOWN.encode(), subjects=[a])
    second = library.upload("renamed.md", SAMPLE_MARKDOWN.encode(), subjects=[b], week=3)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["job"] is None
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    names = {s["name"] for s in second.json()["document"]["subjects"]}
    assert names == {a, b}
    assert second.json()["document"]["week"] == 3


def test_multiple_subjects_comma_and_repeated_and_case_insensitive(library) -> None:
    base = library.subject("Multi")
    response = library.upload(
        "multi.txt", b"some text", subjects=[f"{base}-x, {base}-y", f"{base}-X", f"{base}-z"]
    )
    assert response.status_code == 202
    names = sorted(s["name"] for s in response.json()["document"]["subjects"])
    assert names == sorted([f"{base}-x", f"{base}-y", f"{base}-z"])  # -X folded into -x

    subjects = {s["name"]: s for s in library.client.get("/v1/subjects").json()}
    assert subjects[f"{base}-x"]["document_count"] == 1


@pytest.mark.parametrize(
    ("filename", "data", "status"),
    [
        ("archive.zip", b"PK\x03\x04zip", 415),
        ("script.py", b"print(1)", 415),
        ("fake.pdf", b"definitely not a pdf", 415),
        ("empty.txt", b"", 415),
    ],
)
def test_unsupported_uploads_are_rejected(library, filename, data, status) -> None:
    response = library.upload(filename, data)
    assert response.status_code == status, response.text
    assert response.json()["detail"]


def test_upload_size_limit(library, settings_env) -> None:
    settings_env.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    response = library.upload("big.txt", b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413


def test_must_provide_file_or_url_not_both(library) -> None:
    assert library.client.post("/v1/documents", data={}).status_code == 422
    response = library.client.post(
        "/v1/documents", data={"url": "https://example.com"}, files={"file": ("a.txt", b"x")}
    )
    assert response.status_code == 422


def test_invalid_urls_are_rejected(library) -> None:
    assert library.import_url("ftp://example.com/file").status_code == 415
    assert library.import_url("not a url").status_code == 415


# ── worker processing ────────────────────────────────────────────────────


def test_markdown_pipeline_produces_chunks_with_exact_offsets(library, worker) -> None:
    response = library.upload("notes.md", SAMPLE_MARKDOWN.encode())
    document_id = response.json()["document"]["id"]
    _run_pipeline(worker)

    document = _document(library.client, document_id)
    assert document["status"] == "ready", document["error"]
    assert document["title"] == "Automata"
    assert document["chunk_count"] > 0
    assert document["meta"]["section_count"] == 3
    assert "structure" not in document["meta"], "internal structure is not exposed"
    assert [j["type"] for j in document["jobs"]] == ["embed.chunks", "ingest.chunk", "ingest.parse"]
    assert [j["status"] for j in document["jobs"]] == ["queued", "succeeded", "succeeded"]

    chunks = library.client.get(f"/v1/documents/{document_id}/chunks").json()
    assert chunks["total"] == len(chunks["items"])
    sections = [c for c in chunks["items"] if c["parent_id"] is None]
    assert [c["heading_path"] for c in sections][:2] == [
        ["Automata"],
        ["Automata", "Deterministic"],
    ]

    with get_session_factory()() as session:
        from secondbrain.db.models import Document

        raw = session.get(Document, document_id).raw_text
    for c in chunks["items"]:
        assert c["text"] == raw[c["char_start"] : c["char_end"]]

    retrieval = library.client.get(f"/v1/documents/{document_id}/chunks?level=retrieval").json()
    assert retrieval["total"] == document["chunk_count"]
    assert all(c["parent_id"] for c in retrieval["items"])


def test_pdf_pipeline_records_pages(library, worker) -> None:
    body = "Body text sentence that establishes the dominant font size of the document. " * 8
    pdf = make_pdf(
        [
            [("Introduction", 20, False), (body, 11, False)],
            [("Methods", 20, False), (body, 11, False)],
        ]
    )
    response = library.upload("paper.pdf", pdf, subjects=[library.subject("ML")])
    document_id = response.json()["document"]["id"]
    _run_pipeline(worker)

    document = _document(library.client, document_id)
    assert document["status"] == "ready", document["error"]
    assert document["kind"] == "pdf" and document["meta"]["pages"] == 2
    chunks = library.client.get(f"/v1/documents/{document_id}/chunks?level=sections").json()
    assert [c["heading_path"] for c in chunks["items"]] == [["Introduction"], ["Methods"]]
    assert chunks["items"][0]["page_start"] == 1
    assert chunks["items"][1]["page_start"] == 2


def test_docx_and_text_pipelines(library, worker) -> None:
    docx = make_docx([("Heading 1", "Graphs"), ("Normal", "A graph has vertices and edges. " * 10)])
    d1 = library.upload("notes.docx", docx).json()["document"]["id"]
    d2 = library.upload("plain.txt", b"Just some plain text.\n\nSecond paragraph.").json()[
        "document"
    ]["id"]
    _run_pipeline(worker, stages=4)
    assert _document(library.client, d1)["status"] == "ready"
    assert _document(library.client, d2)["status"] == "ready"
    assert _document(library.client, d2)["title"] == "plain"


def test_corrupt_pdf_fails_without_retry_and_marks_document(library, worker) -> None:
    response = library.upload("broken.pdf", b"%PDF-1.7 garbage that is not a pdf at all")
    document_id = response.json()["document"]["id"]
    assert worker.run_once()
    assert not worker.run_once(), "a non-retryable failure must not be re-queued"

    document = _document(library.client, document_id)
    assert document["status"] == "failed"
    assert "not a readable PDF" in document["error"]
    (job,) = document["jobs"]
    assert job["status"] == "failed" and job["attempts"] == 1
    assert document["chunk_count"] == 0


def test_transient_fetch_error_is_retried(library, worker, monkeypatch) -> None:
    def flaky(url: str) -> str:
        raise FetchError("connection reset")

    monkeypatch.setattr(web, "fetch_html", flaky)
    response = library.import_url("https://example.com/flaky")
    document_id = response.json()["document"]["id"]
    assert worker.run_once()

    document = _document(library.client, document_id)
    assert document["status"] == "pending", "not final: the retry is queued, not failed"
    assert "connection reset" in document["error"]
    (job,) = document["jobs"]
    assert job["status"] == "queued" and job["attempts"] == 1
    assert job["run_after"] > job["created_at"], "backoff applied"


def test_web_pipeline_with_mocked_fetch(library, worker, monkeypatch) -> None:
    monkeypatch.setattr(web, "fetch_html", lambda url: ARTICLE_HTML)
    response = library.import_url(
        "https://example.com/optimisers", subjects=[library.subject("DL")], week=2
    )
    assert response.status_code == 202
    document_id = response.json()["document"]["id"]
    assert response.json()["document"]["kind"] == "web"
    _run_pipeline(worker)

    document = _document(library.client, document_id)
    assert document["status"] == "ready", document["error"]
    assert document["title"] == "Optimisers Explained"
    assert document["storage_path"] is None
    assert document["origin_uri"] == "https://example.com/optimisers"
    assert document["meta"]["author"] == "Jane Doe"
    # Same URL again → duplicate, nothing re-fetched.
    again = library.import_url("https://example.com/optimisers")
    assert again.status_code == 200 and again.json()["duplicate"]


def test_youtube_pipeline_with_mocked_transcript(library, worker, monkeypatch) -> None:
    snippets = [Snippet(f"Snippet {i} of the lecture.", i * 10.0, 10.0) for i in range(30)]
    monkeypatch.setattr(youtube, "fetch_transcript", lambda vid: snippets)
    monkeypatch.setattr(youtube, "fetch_video_info", lambda vid: VideoInfo("Lecture 7", "Uni"))
    response = library.import_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ", week=7)
    assert response.status_code == 202
    document_id = response.json()["document"]["id"]
    assert response.json()["document"]["kind"] == "youtube"
    _run_pipeline(worker)

    document = _document(library.client, document_id)
    assert document["status"] == "ready", document["error"]
    assert document["title"] == "Lecture 7"
    assert document["meta"]["video_id"] == "dQw4w9WgXcQ"
    assert document["meta"]["duration"] == "5:00"
    assert document["chunk_count"] >= 1


# ── library queries, reprocess, delete ───────────────────────────────────


def test_list_filters_by_subject_week_and_status(library, worker) -> None:
    s1, s2 = library.subject("F1"), library.subject("F2")
    a = library.upload("a.txt", b"alpha text", subjects=[s1], week=1).json()["document"]["id"]
    b = library.upload("b.txt", b"beta text", subjects=[s1, s2], week=2).json()["document"]["id"]
    c = library.upload("c.txt", b"gamma text", subjects=[s2]).json()["document"]["id"]
    subjects = {s["name"]: s["id"] for s in library.client.get("/v1/subjects").json()}
    mine = {a, b, c}

    def ids(**params) -> set[str]:
        # The shared development database may hold other documents; look only at ours.
        data = library.client.get("/v1/documents", params={**params, "limit": 500}).json()
        return {d["id"] for d in data["items"]} & mine

    assert ids(subject_id=subjects[s1]) == {a, b}
    assert ids(subject_id=subjects[s2], week=2) == {b}
    assert ids(week=1) == {a}
    assert a in ids(status="pending")
    _run_pipeline(worker, stages=6)
    assert a not in ids(status="pending") and a in ids(status="ready")
    assert ids(kind="text") >= {a, b}


def test_reprocess_supersedes_old_chunks(library, worker) -> None:
    document_id = library.upload("r.md", SAMPLE_MARKDOWN.encode()).json()["document"]["id"]
    _run_pipeline(worker)
    before = library.client.get(f"/v1/documents/{document_id}/chunks").json()["total"]

    response = library.client.post(f"/v1/documents/{document_id}/reprocess")
    assert response.status_code == 202 and response.json()["type"] == "ingest.parse"
    _drain(worker, at_least=2)

    after = library.client.get(f"/v1/documents/{document_id}/chunks").json()["total"]
    assert after == before, "live chunks are replaced one-for-one"
    with get_session_factory()() as session:
        rows = session.execute(select(Chunk).where(Chunk.document_id == document_id)).scalars()
        superseded = [c for c in rows if c.superseded_at is not None]
    assert len(superseded) == before, "old chunks are kept, marked superseded"
    document = _document(library.client, document_id)
    assert [j["type"] for j in document["jobs"]].count("ingest.parse") == 2


def test_delete_removes_rows_and_managed_file(library, worker) -> None:
    response = library.upload("gone.md", SAMPLE_MARKDOWN.encode())
    document_id = response.json()["document"]["id"]
    stored = get_settings().data_dir / response.json()["document"]["storage_path"]
    _run_pipeline(worker)
    assert stored.exists()

    assert library.client.delete(f"/v1/documents/{document_id}").status_code == 204
    assert library.client.get(f"/v1/documents/{document_id}").status_code == 404
    assert library.client.delete(f"/v1/documents/{document_id}").status_code == 404
    assert not stored.exists()
    with get_session_factory()() as session:
        assert (
            session.execute(select(Chunk).where(Chunk.document_id == document_id)).first() is None
        )
        # Job history stays for auditing; it references the document only by payload.
        assert session.execute(
            select(Job).where(Job.payload["document_id"].astext == document_id)
        ).first()


def test_job_endpoint(library) -> None:
    job = library.upload("j.txt", b"job text").json()["job"]
    response = library.client.get(f"/v1/jobs/{job['id']}")
    assert response.status_code == 200 and response.json()["type"] == "ingest.parse"
    assert library.client.get("/v1/jobs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_reprocess_replaces_parser_metadata(library, worker, monkeypatch) -> None:
    monkeypatch.setattr(web, "fetch_html", lambda url: ARTICLE_HTML)
    document_id = library.import_url("https://example.com/meta").json()["document"]["id"]
    _run_pipeline(worker)
    assert _document(library.client, document_id)["meta"]["author"] == "Jane Doe"

    monkeypatch.setattr(
        web, "fetch_html", lambda url: ARTICLE_HTML.replace('content="Jane Doe"', 'content=""')
    )
    library.client.post(f"/v1/documents/{document_id}/reprocess")
    _drain(worker, at_least=2)
    meta = _document(library.client, document_id)["meta"]
    assert "author" not in meta, "stale parser metadata must not survive a reprocess"
    assert meta["chunk_count"] > 0


def test_document_lookup_by_title_is_case_insensitive_and_composes_with_filters(
    library, worker
) -> None:
    s1, s2 = library.subject("L1"), library.subject("L2")
    a = library.upload("automata-intro.txt", b"alpha", subjects=[s1], week=1).json()
    b = library.upload("grammars.txt", b"beta", subjects=[s1], week=2).json()
    c = library.upload("misc.txt", b"gamma", subjects=[s2], week=1, title="Automata 100% done")
    ids = {"a": a["document"]["id"], "b": b["document"]["id"], "c": c.json()["document"]["id"]}
    subjects = {s["name"]: s["id"] for s in library.client.get("/v1/subjects").json()}

    def found(**params) -> set[str]:
        data = library.client.get("/v1/documents", params={**params, "limit": 500}).json()
        return {d["id"] for d in data["items"]} & set(ids.values())

    assert found(q="AUTOMATA") == {ids["a"], ids["c"]}, "case-insensitive title match"
    assert found(q="automata", subject_id=subjects[s1]) == {ids["a"]}, "composes with subject"
    assert found(q="automata", week=1, subject_id=subjects[s2]) == {ids["c"]}
    assert found(q="100%") == {ids["c"]}, "a literal % is not a wildcard"
    assert found(q="a_b") == set(), "a literal _ is not a wildcard"
    assert found(q="grammars.txt") == {ids["b"]}, "the original filename matches too"
    assert found(q="   ") == set(ids.values()), "blank query means no filter"
    assert found(q="nothing-matches-this") == set()

    _run_pipeline(worker, stages=6)
    assert found(q="automata", status="ready") == {ids["a"], ids["c"]}, "composes with status"
