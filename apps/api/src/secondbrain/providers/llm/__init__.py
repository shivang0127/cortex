"""LLM providers and the factory that picks one from configuration."""

from functools import lru_cache

from secondbrain.config import Settings, get_settings
from secondbrain.providers.llm.base import (
    Completion,
    GenerationConfig,
    LLMError,
    LLMProvider,
    LLMTimeout,
    LLMUnavailable,
    Message,
    ProviderHealth,
)

__all__ = [
    "Completion",
    "GenerationConfig",
    "LLMDisabled",
    "LLMError",
    "LLMProvider",
    "LLMTimeout",
    "LLMUnavailable",
    "Message",
    "ProviderHealth",
    "active_llm_model",
    "build_llm_provider",
    "get_llm_provider",
    "llm_enabled",
    "reset_llm_provider_cache",
]


class LLMDisabled(RuntimeError):
    """LLM_PROVIDER is "none": nothing can be generated."""


def build_llm_provider(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case "ollama":
            from secondbrain.providers.llm.ollama import OllamaProvider

            return OllamaProvider(
                settings.llm_model,
                settings.llm_base_url,
                context_window=settings.llm_context_tokens,
                timeout_seconds=settings.llm_timeout_seconds,
                keep_alive=settings.llm_keep_alive,
                thinking=settings.llm_thinking,
            )
        case "fake":
            from secondbrain.providers.llm.fake import FakeLLMProvider

            return FakeLLMProvider(
                mode=settings.llm_fake_mode, context_window=settings.llm_context_tokens
            )
        case "none":
            raise LLMDisabled("set LLM_PROVIDER to enable answering")
        case other:
            raise ValueError(f"unknown LLM_PROVIDER {other!r}")


@lru_cache
def get_llm_provider() -> LLMProvider:
    """Process-wide provider. Cheap to build — the model lives in Ollama, not here."""
    return build_llm_provider(get_settings())


def llm_enabled(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).llm_provider != "none"


def active_llm_model() -> str | None:
    """The model id answers are attributed to — always the provider's own."""
    if not llm_enabled():
        return None
    return get_llm_provider().model_id


def reset_llm_provider_cache() -> None:
    get_llm_provider.cache_clear()
