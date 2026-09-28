"""Embedding providers and the factory that picks one from configuration."""

from functools import lru_cache

from secondbrain.config import Settings, get_settings
from secondbrain.providers.embedding.base import EmbeddingError, EmbeddingProvider

__all__ = [
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingsDisabled",
    "active_model_id",
    "embeddings_enabled",
    "get_embedding_provider",
]


class EmbeddingsDisabled(RuntimeError):
    """EMBEDDING_PROVIDER is "none": nothing can be embedded or searched semantically."""


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    match settings.embedding_provider:
        case "fastembed":
            from secondbrain.providers.embedding.fastembed_provider import FastEmbedProvider

            return FastEmbedProvider(settings.embedding_model, settings.embedding_cache_dir)
        case "fake":
            from secondbrain.providers.embedding.fake import FakeEmbeddingProvider

            return FakeEmbeddingProvider(dimension=settings.embedding_dimension)
        case "none":
            raise EmbeddingsDisabled("set EMBEDDING_PROVIDER to enable embeddings")
        case other:
            raise ValueError(f"unknown EMBEDDING_PROVIDER {other!r}")


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """Process-wide provider: the model is loaded once and reused."""
    return build_embedding_provider(get_settings())


def embeddings_enabled(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).embedding_provider != "none"


def active_model_id() -> str | None:
    """The model id rows are written with — always the provider's, never a setting
    read in isolation (the fake provider has its own id). None when disabled."""
    if not embeddings_enabled():
        return None
    return get_embedding_provider().model_id


def reset_embedding_provider_cache() -> None:
    get_embedding_provider.cache_clear()
