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

    # ── AI providers ("none" disables) ────────────────────────────────────
    # ollama = local runtime (default) | fake = deterministic, tests | none = disabled
    llm_provider: Literal["ollama", "fake", "none"] = "ollama"
    llm_model: str = "qwen3:4b-instruct"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_context_tokens: int = 4096  # num_ctx; bounded by 4 GB of VRAM, not by the model
    llm_max_output_tokens: int = 512
    llm_temperature: float = 0.1
    llm_timeout_seconds: float = 120.0
    llm_keep_alive: str = "30m"  # how long Ollama holds the model in VRAM between questions
    # Qwen3 tags ship with thinking ON by default. Measured on this machine: an answer
    # took 84s and came back EMPTY (the reasoning consumed the whole token budget);
    # with thinking off the same answer took 7.6s. Off unless a model needs it.
    llm_thinking: bool = False
    llm_fake_mode: str = "normal"  # fake provider only: normal|refusal|invented|uncited|…
    # Store full prompts and responses in llm_calls. Off by default: the prompt
    # contains your source text, and the rows would duplicate it.
    llm_log_payloads: bool = False
    # fastembed (local ONNX model, default) | fake (deterministic, tests) | none
    embedding_provider: Literal["fastembed", "fake", "none"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Must equal the chunk_embeddings.embedding column width (migration 0003).
    # Changing models to a different width is a migration, not a setting.
    embedding_dimension: int = 384
    embedding_cache_dir: Path = Path("data/models")
    embedding_batch_size: int = 64  # chunks per provider call inside the worker

    # ── Search ────────────────────────────────────────────────────────────
    search_candidates: int = 50  # per-source depth before fusion (ARCHITECTURE.md §7)
    search_rrf_k: int = 60  # reciprocal-rank-fusion constant

    # ── RAG / Ask (ARCHITECTURE.md §7) ────────────────────────────────────
    rag_mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    rag_top_k: int = 8  # retrieval chunks kept after fusion
    rag_context_tokens: int = 3000  # budget for the assembled sources block
    rag_max_source_tokens: int = 700  # a single source longer than this is truncated
    rag_expand_to_parents: bool = True  # "retrieve small, read big" (§6)
    # Below this cosine similarity a semantic hit is not evidence. A keyword hit
    # still counts — an exact term match is evidence whatever the vector says.
    # Calibrated against the real 87-chunk corpus with bge-small (ARCHITECTURE.md §7):
    # 7 answerable questions scored top-1 0.651–0.860; 6 questions on topics the
    # library genuinely lacks scored 0.440–0.527. 0.60 sits in that gap with margin
    # on both sides. Deliberately permissive rather than tight: a marginal source
    # still reaches the model, which is instructed to refuse — but a floor set too
    # high refuses a good question outright, with no second chance.
    rag_min_similarity: float = 0.60
    rag_min_sources: int = 1  # fewer sources above the floor than this ⇒ refuse

    @field_validator("data_dir", "embedding_cache_dir")
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
