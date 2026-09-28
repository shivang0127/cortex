"""Knowledge search (semantic / keyword / hybrid) against the real database with the
deterministic provider: word overlap stands in for meaning, which is enough to prove
the vector query, the full-text query, the fusion and the result metadata."""

import pytest

from secondbrain.db.engine import get_session_factory
from secondbrain.providers.embedding.fake import FakeEmbeddingProvider
from secondbrain.services import search as svc
from secondbrain.services.search import SearchFilters

pytestmark = pytest.mark.integration

NETWORKS = (
    "# Neural Networks\n\n"
    "A neural network learns by adjusting its weights with gradient descent. "
    "Backpropagation computes the gradient of the loss for every weight. "
    * 3
    + "\n\n## Optimisation\n\n"
    "Momentum and Adam are optimisation methods that speed up training of deep networks. "
    * 3
    + "\n"
)
AUTOMATA = (
    "# Finite Automata\n\n"
    "A deterministic finite automaton accepts exactly the regular languages. "
    "The pumping lemma proves that some languages are not regular. " * 3 + "\n"
)
COOKING = (
    "# Roasting\n\n"
    "Preheat the oven and roast the vegetables with olive oil for forty minutes. " * 4 + "\n"
)


def _drain(worker) -> None:
    while worker.run_once():
        pass


@pytest.fixture
def corpus(library, worker):
    """Three documents, two subjects, fully embedded."""
    s_ml, s_tcs = library.subject("ML"), library.subject("TCS")
    ids = {
        "networks": library.upload("networks.md", NETWORKS.encode(), subjects=[s_ml], week=3),
        "automata": library.upload("automata.md", AUTOMATA.encode(), subjects=[s_tcs], week=7),
        "cooking": library.upload("cooking.md", COOKING.encode()),
    }
    ids = {k: r.json()["document"]["id"] for k, r in ids.items()}
    _drain(worker)
    subjects = {s["name"]: s["id"] for s in library.client.get("/v1/subjects").json()}
    return {"ids": ids, "subjects": {"ml": subjects[s_ml], "tcs": subjects[s_tcs]}}


def _search(client, **params):
    response = client.get("/v1/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _titles(body) -> list[str]:
    return [r["document_title"] for r in body["results"]]


def test_semantic_search_returns_nearest_chunks_with_metadata(library, corpus) -> None:
    body = _search(library.client, q="how does a neural network learn", mode="semantic", limit=3)
    assert body["mode"] == "semantic" and body["model"] == "fake-bow-v1"
    assert body["results"], "no results"
    top = body["results"][0]
    assert top["document_id"] == corpus["ids"]["networks"]
    assert top["document_title"] == "Neural Networks"
    assert top["heading_path"] == ["Neural Networks"]
    assert top["semantic_rank"] == 1 and top["keyword_rank"] is None
    assert 0 < top["similarity"] <= 1 and top["score"] == top["similarity"]
    assert top["subjects"] and top["week"] == 3
    for key in ("chunk_id", "char_start", "char_end", "ordinal", "text", "document_kind"):
        assert key in top
    # Chunks of the other documents rank below every networks chunk that matched.
    scores = [r["similarity"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_keyword_search_finds_exact_terms(library, corpus) -> None:
    body = _search(library.client, q="pumping lemma", mode="keyword")
    assert body["model"] is None and body["semantic_candidates"] == 0
    assert _titles(body) == ["Finite Automata"]
    top = body["results"][0]
    assert top["keyword_rank"] == 1 and top["semantic_rank"] is None
    assert top["keyword_score"] > 0 and top["similarity"] is None
    assert "pumping lemma" in top["text"].lower()


def test_hybrid_fuses_both_signals(library, corpus) -> None:
    body = _search(library.client, q="backpropagation gradient descent", mode="hybrid", limit=5)
    assert body["semantic_candidates"] > 0 and body["keyword_candidates"] > 0
    top = body["results"][0]
    assert top["document_title"] == "Neural Networks"
    assert top["semantic_rank"] == 1 and top["keyword_rank"] == 1, "both signals agree"
    assert top["score"] == pytest.approx(1 / 61 + 1 / 61)
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)
    # A hit found by only one signal carries only that signal's rank.
    one_sided = [
        r for r in body["results"] if (r["semantic_rank"] is None) != (r["keyword_rank"] is None)
    ]
    assert all((r["similarity"] is None) == (r["semantic_rank"] is None) for r in one_sided)


def test_keyword_only_term_still_surfaces_in_hybrid(library, corpus) -> None:
    body = _search(library.client, q="pumping", mode="hybrid", limit=3)
    assert "Finite Automata" in _titles(body)
    automata = next(r for r in body["results"] if r["document_title"] == "Finite Automata")
    assert automata["keyword_rank"] == 1


def test_filters_narrow_the_population(library, corpus) -> None:
    ids, subjects = corpus["ids"], corpus["subjects"]
    body = _search(library.client, q="network weights gradient", subject_id=subjects["tcs"])
    assert set(r["document_id"] for r in body["results"]) <= {ids["automata"]}
    body = _search(library.client, q="network weights gradient", week=3)
    assert {r["document_id"] for r in body["results"]} == {ids["networks"]}
    body = _search(library.client, q="oven", mode="keyword", document_id=ids["networks"])
    assert body["results"] == []


def test_empty_and_blank_queries_are_rejected(library) -> None:
    assert library.client.get("/v1/search", params={"q": ""}).status_code == 422
    assert library.client.get("/v1/search", params={"q": "   "}).status_code == 422
    assert library.client.get("/v1/search").status_code == 422


def test_no_results_is_an_empty_list(library, corpus) -> None:
    body = _search(library.client, q="zxqv nonexistentterm", mode="keyword")
    assert body["results"] == [] and body["keyword_candidates"] == 0


def test_disabled_embeddings_refuse_semantic_but_allow_keyword(
    library, corpus, settings_env
) -> None:
    from secondbrain.config import get_settings
    from secondbrain.providers.embedding import reset_embedding_provider_cache

    settings_env.setenv("EMBEDDING_PROVIDER", "none")
    get_settings.cache_clear()
    reset_embedding_provider_cache()
    assert (
        library.client.get("/v1/search", params={"q": "roast", "mode": "semantic"}).status_code
        == 409
    )
    assert _search(library.client, q="roast", mode="keyword")["results"]


def test_superseded_and_unembedded_chunks_are_excluded(library, worker, corpus) -> None:
    ids = corpus["ids"]
    with get_session_factory()() as session:
        before = svc.search(
            session, "roast vegetables", mode="semantic", provider=FakeEmbeddingProvider(), limit=5
        )
        assert str(before.hits[0].document.id) == ids["cooking"]
    library.client.post(f"/v1/documents/{ids['cooking']}/reprocess")
    assert worker.run_once()  # parse only: chunks not yet rebuilt/embedded…
    assert worker.run_once()  # chunk: old chunks superseded, new ones not yet embedded
    with get_session_factory()() as session:
        mid = svc.search(
            session, "roast vegetables", mode="semantic", provider=FakeEmbeddingProvider(), limit=5
        )
        assert not [h for h in mid.hits if str(h.document.id) == ids["cooking"]]
    _drain(worker)  # embed
    body = _search(library.client, q="roast vegetables", mode="semantic")
    assert body["results"][0]["document_id"] == ids["cooking"]


def test_search_service_rejects_blank_and_missing_provider() -> None:
    with get_session_factory()() as session:
        with pytest.raises(ValueError):
            svc.search(session, "   ", mode="keyword", provider=None, limit=5)
        with pytest.raises(ValueError):
            svc.search(session, "x", mode="semantic", provider=None, limit=5)
    assert SearchFilters() == SearchFilters(subject_id=None)
