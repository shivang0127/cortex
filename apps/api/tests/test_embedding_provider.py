"""Embedding provider behaviour: the deterministic fake always, the real model when cached."""

import math
from pathlib import Path

import pytest

from secondbrain.config import Settings, get_settings
from secondbrain.providers.embedding import (
    EmbeddingsDisabled,
    build_embedding_provider,
    get_embedding_provider,
    reset_embedding_provider_cache,
)
from secondbrain.providers.embedding.fake import FakeEmbeddingProvider
from secondbrain.services.search import reciprocal_rank_fusion


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


# ── fake provider ─────────────────────────────────────────────────────────


def test_fake_provider_is_deterministic_and_unit_length() -> None:
    p = FakeEmbeddingProvider(dimension=384)
    a = p.embed_documents(["Gradient descent updates weights."])[0]
    b = p.embed_documents(["Gradient descent updates weights."])[0]
    assert a == b
    assert len(a) == 384 == p.dimension
    assert abs(_norm(a) - 1.0) < 1e-9
    assert p.embed_query("Gradient descent updates weights.") == a  # symmetric by design


def test_fake_provider_similarity_tracks_word_overlap() -> None:
    p = FakeEmbeddingProvider()
    q = p.embed_query("neural network learning")
    close = p.embed_documents(["A neural network learns by adjusting weights."])[0]
    far = p.embed_documents(["Preheat the oven and roast the vegetables."])[0]
    assert _cos(q, close) > _cos(q, far)


def test_fake_provider_batches_preserve_order_and_handle_empty_text() -> None:
    p = FakeEmbeddingProvider(dimension=16)
    vectors = p.embed_documents(["alpha", "", "beta"])
    assert len(vectors) == 3 and all(len(v) == 16 for v in vectors)
    assert vectors[1][0] == 1.0, "empty text maps to a fixed unit vector"
    assert vectors[0] == p.embed_documents(["alpha"])[0]
    assert p.embed_documents([]) == []


# ── factory ───────────────────────────────────────────────────────────────


def test_factory_selects_provider_from_settings(tmp_path: Path) -> None:
    fake = build_embedding_provider(Settings(embedding_provider="fake", embedding_dimension=8))
    assert fake.provider_id == "fake" and fake.dimension == 8
    real = build_embedding_provider(
        Settings(embedding_provider="fastembed", embedding_cache_dir=tmp_path)
    )
    assert real.provider_id == "fastembed" and real.model_id == "BAAI/bge-small-en-v1.5"
    with pytest.raises(EmbeddingsDisabled):
        build_embedding_provider(Settings(embedding_provider="none"))


def test_cached_provider_follows_settings(settings_env: pytest.MonkeyPatch) -> None:
    settings_env.setenv("EMBEDDING_PROVIDER", "fake")
    get_settings.cache_clear()
    reset_embedding_provider_cache()
    assert get_embedding_provider() is get_embedding_provider()
    assert get_embedding_provider().provider_id == "fake"
    reset_embedding_provider_cache()


# ── real model (skipped unless already downloaded) ───────────────────────


def _cached_model_dir() -> Path | None:
    cache = Settings().embedding_cache_dir
    hits = list(cache.glob("models--*bge-small-en-v1.5*")) if cache.exists() else []
    return hits[0] if hits else None


@pytest.mark.skipif(_cached_model_dir() is None, reason="bge-small model not downloaded yet")
def test_real_model_dimension_normalisation_and_semantics() -> None:
    provider = build_embedding_provider(Settings(embedding_provider="fastembed"))
    assert provider.dimension == 384
    docs = provider.embed_documents(
        [
            "Backpropagation computes gradients so gradient descent can update the weights.",
            "Preheat the oven to 200 degrees and roast the vegetables for forty minutes.",
        ]
    )
    assert [len(v) for v in docs] == [384, 384]
    assert all(abs(_norm(v) - 1.0) < 1e-3 for v in docs)
    q = provider.embed_query("How does a neural network learn?")
    assert len(q) == 384
    assert _cos(q, docs[0]) > _cos(q, docs[1]) + 0.15, "query lands nearer the relevant passage"


# ── fusion ────────────────────────────────────────────────────────────────


def test_reciprocal_rank_fusion_rewards_agreement() -> None:
    import uuid

    a, b, c, d = (uuid.uuid4() for _ in range(4))
    scores = reciprocal_rank_fusion([[a, b, c], [b, d]], k=60)
    assert scores[b] == pytest.approx(1 / 62 + 1 / 61)  # 2nd in one list, 1st in the other
    assert scores[a] == pytest.approx(1 / 61)
    assert scores[b] > scores[a] > scores[c]
    assert set(scores) == {a, b, c, d}
    assert reciprocal_rank_fusion([]) == {}
