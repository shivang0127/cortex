"""Shared fixtures.

Unit tests never need a database. Tests marked `integration` run against a
dedicated `secondbrain_test` database (created by scripts/init-db.sql) on the
local PostgreSQL server — never the real library, so they cannot clobber your
documents or race the running worker. They skip themselves when that database
is unreachable, so `pytest` is always green on a fresh checkout.

Override the location with TEST_DATABASE_URL; by default the database name in
DATABASE_URL is replaced with `secondbrain_test`.
"""

import io
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from secondbrain.config import Settings, get_settings
from secondbrain.db.engine import get_session_factory, reset_engine_cache
from secondbrain.providers.embedding import reset_embedding_provider_cache
from secondbrain.providers.llm import reset_llm_provider_cache

UNREACHABLE_DATABASE_URL = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere"


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    """Reset cached settings/engine around a test that changes the environment.

    DATA_DIR is pointed at a temp directory so tests never write into data/.
    """
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # Tests never load a real model; the deterministic providers stand in.
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_FAKE_MODE", "normal")
    monkeypatch.setenv("LLM_LOG_PAYLOADS", "false")
    get_settings.cache_clear()
    reset_engine_cache()
    reset_embedding_provider_cache()
    reset_llm_provider_cache()
    yield monkeypatch
    get_settings.cache_clear()
    reset_engine_cache()
    reset_embedding_provider_cache()
    reset_llm_provider_cache()


@pytest.fixture
def client_without_db(settings_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An app whose database is guaranteed unreachable."""
    settings_env.setenv("DATABASE_URL", UNREACHABLE_DATABASE_URL)
    from secondbrain.main import create_app

    with TestClient(create_app()) as client:
        yield client


_test_database_url: str | None | bool = False  # False = not probed yet


def _derive_test_database_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    base = make_url(os.environ.get("DATABASE_URL") or Settings().database_url)
    return base.set(database="secondbrain_test").render_as_string(hide_password=False)


def _prepare_test_database() -> str | None:
    """Probe the test database once per session and migrate it to head.

    Returns its URL, or None when it is unreachable (tests then skip).
    """
    global _test_database_url
    if _test_database_url is not False:
        return _test_database_url
    url = _derive_test_database_url()
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        engine.dispose()
    except SQLAlchemyError:
        _test_database_url = None
        return None
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # migrations/env.py reads Settings
    get_settings.cache_clear()
    try:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
        )
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
    _test_database_url = url
    return url


def _require_database(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _prepare_test_database()
    if url is None:
        pytest.skip(
            "secondbrain_test database not reachable — run scripts/init-db.sql on a "
            "running PostgreSQL"
        )
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    reset_engine_cache()


@pytest.fixture
def db_client(settings_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An app against the test database; skips when it is not running."""
    _require_database(settings_env)
    from secondbrain.main import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def db_session(settings_env: pytest.MonkeyPatch):
    """A real session with rollback-on-exit; skips when the database is absent."""
    _require_database(settings_env)
    with get_session_factory()() as session:
        yield session
        session.rollback()


# ── Integration helpers ──────────────────────────────────────────────────


TEST_SUBJECT_PREFIX = "pytest-"


@pytest.fixture
def library(db_client: TestClient):
    """Tracks documents created through the API and removes them (and test
    subjects) afterwards, so integration tests leave the database as they found it."""
    from secondbrain.db.models import Job, LlmCall, Subject

    created: list[str] = []
    yield _Library(db_client, created)
    for document_id in created:
        db_client.delete(f"/v1/documents/{document_id}")
    with get_session_factory().begin() as session:
        session.execute(delete(Subject).where(Subject.name.like(f"{TEST_SUBJECT_PREFIX}%")))
        session.execute(delete(LlmCall))  # not tied to a document; clear per test
        if created:
            session.execute(delete(Job).where(Job.payload["document_id"].astext.in_(created)))


class _Library:
    def __init__(self, client: TestClient, created: list[str]) -> None:
        self.client = client
        self.created = created

    def subject(self, name: str) -> str:
        return f"{TEST_SUBJECT_PREFIX}{name}-{uuid.uuid4().hex[:6]}"

    def upload(self, filename: str, data: bytes, **form):
        files = {"file": (filename, io.BytesIO(data))}
        response = self.client.post("/v1/documents", files=files, data=_form(form))
        self._track(response)
        return response

    def import_url(self, url: str, **form):
        response = self.client.post("/v1/documents", data={"url": url, **_form(form)})
        self._track(response)
        return response

    def _track(self, response) -> None:
        if response.status_code in (200, 202):
            document_id = response.json()["document"]["id"]
            if document_id not in self.created:
                self.created.append(document_id)


def _form(form: dict) -> dict:
    out = {}
    for key, value in form.items():
        if value is None:
            continue
        out[key] = [str(v) for v in value] if isinstance(value, list) else str(value)
    return out


@pytest.fixture
def worker(settings_env: pytest.MonkeyPatch):
    """A worker bound to the test settings; call `run_once()` per stage."""
    _require_database(settings_env)
    from secondbrain.queue import registry
    from secondbrain.worker import Worker

    registry.load_handlers()
    # A crashed earlier run may have left orphaned jobs (document deleted) behind;
    # they must not be claimed in place of this test's own jobs. Jobs for documents
    # that still exist are left alone — this is a shared development database.
    from sqlalchemy import exists, select, update

    from secondbrain.db.models import Document, Job

    with get_session_factory().begin() as session:
        orphan = ~exists(
            select(Document.id).where(Document.id == Job.payload["document_id"].astext.cast(UUID))
        )
        session.execute(
            update(Job).where(Job.status == "queued", orphan).values(status="cancelled")
        )
    return Worker(get_settings(), worker_id="pytest")


# ── Sample documents ─────────────────────────────────────────────────────


def make_pdf(pages: list[list[tuple[str, float, bool]]]) -> bytes:
    """Build a PDF: pages → list of (text, font_size, bold) blocks."""
    import pymupdf

    doc = pymupdf.open()
    for blocks in pages:
        page = doc.new_page()
        y = 72.0
        for content, size, bold in blocks:
            fontname = "helv" if not bold else "hebo"
            for line in _wrap(content, 80 if size <= 12 else 40):
                page.insert_text((72, y), line, fontsize=size, fontname=fontname)
                y += size * 1.4
            y += size
    return doc.tobytes()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def make_docx(items: list[tuple[str, str]]) -> bytes:
    """Build a DOCX: list of (style, text) where style is 'Title', 'Heading 1', 'Normal', …"""
    import docx

    document = docx.Document()
    for style, content in items:
        document.add_paragraph(content, style=style)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


SAMPLE_MARKDOWN = (
    "# Automata\n\n"
    "Finite automata recognise regular languages. " + "More words about automata. " * 30 + "\n\n"
    "## Deterministic\n\n"
    "A DFA has exactly one transition per symbol. " + "Padding sentence here. " * 60 + "\n\n"
    "# Grammars\n\n"
    "Context-free grammars generate context-free languages.\n"
)
