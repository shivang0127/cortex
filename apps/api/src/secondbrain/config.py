"""Application configuration.

The environment is the only source of configuration. Values are read from the
process environment first, then from `.env` at the repository root. Nothing in
the codebase reads `os.environ` directly — everything goes through `Settings`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/src/secondbrain/config.py → repository root is four levels up.
REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Second Brain"
    environment: Literal["development", "test", "production"] = "development"

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://secondbrain:secondbrain@127.0.0.1:5432/secondbrain"
    database_connect_timeout_seconds: int = 5

    # ── API ───────────────────────────────────────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # ── Storage ───────────────────────────────────────────────────────────
    data_dir: Path = Path("data")
    max_upload_mb: int = 200

    # ── Ingestion ─────────────────────────────────────────────────────────
    http_timeout_seconds: float = 30.0
    http_user_agent: str = "SecondBrain/0.1 (+local-first personal knowledge tool)"
    youtube_languages: list[str] = ["en", "en-US", "en-GB"]
    chunk_target_tokens: int = 300  # ARCHITECTURE.md §6: starting points, tune per corpus
    chunk_max_tokens: int = 450
    chunk_overlap_ratio: float = 0.12
    chunk_parent_max_tokens: int = 2000

    # ── Worker ────────────────────────────────────────────────────────────
    worker_poll_interval_seconds: float = 2.0
    worker_stale_job_seconds: int = 900

    # ── AI providers (protocols only in Phase 0; "none" disables) ─────────
    llm_provider: str = "none"
    embedding_provider: str = "none"

    @field_validator("data_dir")
    @classmethod
    def _absolute_data_dir(cls, value: Path) -> Path:
        # Relative paths are relative to the repository, not to the CWD, so the
        # API and the worker agree on where files live regardless of how they
        # were launched.
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, built once. Use `get_settings.cache_clear()` in tests."""
    return Settings()
