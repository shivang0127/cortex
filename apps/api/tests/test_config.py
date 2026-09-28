from pathlib import Path

import pytest

from secondbrain.config import REPO_ROOT, Settings, get_settings


def test_repo_root_is_the_project_directory() -> None:
    assert (REPO_ROOT / "ARCHITECTURE.md").exists()
    assert (REPO_ROOT / "docker-compose.yml").exists()


def test_defaults_are_local_first(settings_env: pytest.MonkeyPatch) -> None:
    for var in ("API_HOST", "LLM_PROVIDER", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL"):
        settings_env.delenv(var, raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.api_host == "127.0.0.1"
    assert s.llm_provider == "none"
    # Local model, no API key, no service — still local-first.
    assert s.embedding_provider == "fastembed"
    assert s.embedding_model == "BAAI/bge-small-en-v1.5" and s.embedding_dimension == 384
    assert s.embedding_cache_dir.is_absolute()


def test_environment_overrides_defaults(settings_env: pytest.MonkeyPatch) -> None:
    settings_env.setenv("API_PORT", "9999")
    settings_env.setenv("CORS_ORIGINS", '["http://example.test"]')
    s = get_settings()
    assert s.api_port == 9999
    assert s.cors_origins == ["http://example.test"]


def test_relative_data_dir_resolves_against_repo_root() -> None:
    s = Settings(data_dir=Path("data"))
    assert s.data_dir == (REPO_ROOT / "data").resolve()
    assert s.data_dir.is_absolute()
