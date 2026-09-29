"""Ask Second Brain end to end against the real database, with the deterministic
LLM: retrieval → context → generation → citations → HTTP, plus every failure path."""

import json

import pytest
from sqlalchemy import select

from secondbrain.config import get_settings
from secondbrain.db.engine import get_session_factory
from secondbrain.db.models import LlmCall
from secondbrain.providers.llm import reset_llm_provider_cache
from secondbrain.providers.llm.fake import FAKE_REFUSAL
from secondbrain.services.rag import REFUSAL_TEXT

pytestmark = pytest.mark.integration

NETWORKS = (
    "# Neural Networks\n\n"
    "A neural network learns by adjusting its weights with gradient descent. "
    "Backpropagation computes the gradient of the loss for every weight. "
    * 3
    + "\n\n## Optimisation\n\n"
    "Momentum and Adam are optimisation methods that speed up training. " * 3 + "\n"
)
AUTOMATA = (
    "# Finite Automata\n\n"
    "A deterministic finite automaton accepts exactly the regular languages. "
    "The pumping lemma proves that some languages are not regular. " * 3 + "\n"
)


def _drain(worker) -> None:
    while worker.run_once():
        pass


@pytest.fixture
def corpus(library, worker):
    subject = library.subject("ML")
    ids = {
        "networks": library.upload("networks.md", NETWORKS.encode(), subjects=[subject], week=3),
        "automata": library.upload("automata.md", AUTOMATA.encode(), week=7),
    }
    ids = {k: r.json()["document"]["id"] for k, r in ids.items()}
    _drain(worker)  # parse → chunk → embed
    subjects = {s["name"]: s["id"] for s in library.client.get("/v1/subjects").json()}
    return {"ids": ids, "subject_id": subjects[subject]}


def _ask(client, question: str, **body) -> dict:
    response = client.post("/v1/ask", json={"question": question, **body})
    assert response.status_code == 200, response.text
    return response.json()


def _set_mode(settings_env, mode: str) -> None:
    """Switch the fake provider's behaviour for one test."""
    settings_env.setenv("LLM_FAKE_MODE", mode)
    get_settings.cache_clear()
    reset_llm_provider_cache()


def _llm_calls() -> list[LlmCall]:
    with get_session_factory()() as session:
        return list(session.execute(select(LlmCall).order_by(LlmCall.created_at)).scalars())


# ── the happy path ────────────────────────────────────────────────────────


def test_ask_returns_a_grounded_answer_with_resolvable_citations(library, corpus) -> None:
    body = _ask(library.client, "how does a neural network learn?")

    assert body["refused"] is False and body["grounded"] is True
    assert body["answer"] and "[S1]" in body["answer"]
    assert body["citations"], "a grounded answer carries citations"

    markers = {s["marker"] for s in body["sources"]}
    assert markers == set(range(1, len(body["sources"]) + 1)), "numbered from one"
    for citation in body["citations"]:
        assert citation["marker"] in markers, "every citation was a supplied source"
        chunk = library.client.get(
            f"/v1/documents/{citation['document_id']}/chunks?limit=1000"
        ).json()["items"]
        assert citation["chunk_id"] in {c["id"] for c in chunk}, "resolves to a real chunk row"
        assert citation["document_title"] and "char_start" in citation

    retrieval, generation = body["retrieval"], body["generation"]
    assert retrieval["hits"] > 0 and retrieval["above_floor"] > 0
    assert retrieval["mode"] == "hybrid" and retrieval["context_tokens"] > 0
    assert generation["provider"] == "fake" and generation["model"] == "fake-echo-v1"
    assert generation["prompt_version"] == "v1" and generation["latency_ms"] is not None


def test_sources_carry_full_provenance(library, corpus) -> None:
    source = _ask(library.client, "gradient descent weights")["sources"][0]
    for key in (
        "chunk_id",
        "document_id",
        "document_title",
        "document_kind",
        "heading_path",
        "char_start",
        "char_end",
        "ordinal",
        "text",
        "score",
    ):
        assert key in source, key
    assert source["document_title"] in ("Neural Networks", "Finite Automata")
    assert isinstance(source["heading_path"], list)


def test_context_is_what_the_model_was_actually_shown(library, corpus) -> None:
    """The source text in the response is the text that went into the prompt."""
    from secondbrain.providers.llm import get_llm_provider

    body = _ask(library.client, "backpropagation gradient")
    prompt = "\n".join(m.content for m in get_llm_provider().calls[-1])
    for source in body["sources"]:
        assert source["text"] in prompt
        assert f"[S{source['marker']}]" in prompt


# ── refusal: the deterministic safeguard ─────────────────────────────────


def test_question_with_no_evidence_refuses_without_calling_the_model(library, corpus) -> None:
    from secondbrain.providers.llm import get_llm_provider

    provider = get_llm_provider()
    before = len(provider.calls)
    body = _ask(library.client, "zzqx nonexistent topic about deep sea tubeworms")

    assert body["refused"] is True and body["grounded"] is False
    assert body["answer"] == REFUSAL_TEXT
    assert body["citations"] == [] and body["sources"] == []
    assert len(provider.calls) == before, "no model call was made"
    assert body["retrieval"]["above_floor"] == 0


def test_model_refusal_is_surfaced_as_a_refusal(library, corpus, settings_env) -> None:
    _set_mode(settings_env, "refusal")
    body = _ask(library.client, "how does a neural network learn?")
    assert body["refused"] is True and body["grounded"] is False
    assert body["answer"].startswith(REFUSAL_TEXT)
    assert FAKE_REFUSAL not in body["answer"], "the raw model text is normalised"


def test_empty_library_refuses(db_client) -> None:
    body = db_client.post("/v1/ask", json={"question": "anything at all"}).json()
    assert body["refused"] is True and body["sources"] == []


# ── citation validation ──────────────────────────────────────────────────


def test_invented_citations_are_stripped_and_answer_marked_ungrounded(
    library, corpus, settings_env
) -> None:
    _set_mode(settings_env, "invented")
    body = _ask(library.client, "how does a neural network learn?")
    assert "[S99]" not in body["answer"], "the model cannot invent an identifier"
    assert body["citations"] == []
    assert body["grounded"] is False and body["refused"] is False
    assert body["sources"], "the retrieved sources are still reported"


def test_uncited_answer_is_not_grounded(library, corpus, settings_env) -> None:
    _set_mode(settings_env, "uncited")
    body = _ask(library.client, "how does a neural network learn?")
    assert body["grounded"] is False and body["citations"] == []
    assert body["answer"]


# ── filters, validation, options ─────────────────────────────────────────


def test_filters_narrow_the_evidence(library, corpus) -> None:
    ids = corpus["ids"]
    body = _ask(library.client, "regular languages automaton", week=7)
    assert {s["document_id"] for s in body["sources"]} <= {ids["automata"]}

    body = _ask(library.client, "gradient descent", subject_id=corpus["subject_id"])
    assert {s["document_id"] for s in body["sources"]} <= {ids["networks"]}

    body = _ask(library.client, "gradient descent", document_id=ids["automata"])
    assert all(s["document_id"] == ids["automata"] for s in body["sources"])


def test_top_k_and_mode_are_per_request(library, corpus) -> None:
    body = _ask(library.client, "neural network gradient", top_k=2)
    assert len(body["sources"]) <= 2 and body["retrieval"]["top_k"] == 2

    body = _ask(library.client, "pumping lemma", mode="keyword")
    assert body["retrieval"]["mode"] == "keyword"
    assert body["retrieval"]["embedding_model"] is None


@pytest.mark.parametrize("question", ["", "   ", None])
def test_blank_questions_are_rejected(library, question) -> None:
    payload = {"question": question} if question is not None else {}
    assert library.client.post("/v1/ask", json=payload).status_code == 422


def test_out_of_range_options_are_rejected(library) -> None:
    for body in ({"top_k": 0}, {"top_k": 99}, {"temperature": 5}, {"max_tokens": 1}):
        response = library.client.post("/v1/ask", json={"question": "x", **body})
        assert response.status_code == 422, body


# ── provider failures ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "status"), [("unavailable", 503), ("timeout", 504), ("error", 502)]
)
def test_provider_failures_map_to_http_status(library, corpus, settings_env, mode, status) -> None:
    _set_mode(settings_env, mode)
    response = library.client.post("/v1/ask", json={"question": "how does a network learn?"})
    assert response.status_code == status, response.text
    assert response.json()["detail"]


def test_disabled_llm_returns_409(library, settings_env) -> None:
    settings_env.setenv("LLM_PROVIDER", "none")
    get_settings.cache_clear()
    reset_llm_provider_cache()
    response = library.client.post("/v1/ask", json={"question": "anything"})
    assert response.status_code == 409 and "disabled" in response.json()["detail"]


def test_disabled_embeddings_blocks_semantic_but_not_keyword(library, corpus, settings_env) -> None:
    from secondbrain.providers.embedding import reset_embedding_provider_cache

    settings_env.setenv("EMBEDDING_PROVIDER", "none")
    get_settings.cache_clear()
    reset_embedding_provider_cache()
    assert library.client.post("/v1/ask", json={"question": "x"}).status_code == 409
    response = library.client.post("/v1/ask", json={"question": "pumping lemma", "mode": "keyword"})
    assert response.status_code == 200


# ── observability ────────────────────────────────────────────────────────


def test_every_answer_writes_an_llm_call_row(library, corpus) -> None:
    before = len(_llm_calls())
    _ask(library.client, "how does a neural network learn?")
    calls = _llm_calls()
    assert len(calls) == before + 1
    call = calls[-1]
    assert call.task == "rag.answer" and call.status == "succeeded"
    assert call.provider == "fake" and call.model == "fake-echo-v1"
    assert call.prompt_version == "v1" and len(call.prompt_hash) == 64
    assert call.latency_ms is not None and call.cost_usd is None
    assert call.meta["grounded"] is True and call.meta["citations"] >= 1
    assert call.request is None and call.response is None, "payloads off by default"

    # …and stored as SQL NULL, not the JSONB literal `null`, so `is null` works.
    with get_session_factory()() as session:
        from sqlalchemy import func

        unset = session.execute(
            select(func.count(LlmCall.id)).where(LlmCall.request.is_(None))
        ).scalar_one()
    assert unset >= 1


def test_payload_logging_is_opt_in(library, corpus, settings_env) -> None:
    settings_env.setenv("LLM_LOG_PAYLOADS", "true")
    get_settings.cache_clear()
    _ask(library.client, "how does a neural network learn?")
    call = _llm_calls()[-1]
    assert call.request and call.response
    assert call.request["messages"][0]["role"] == "system"


def test_failures_are_logged_too(library, corpus, settings_env) -> None:
    _set_mode(settings_env, "error")
    library.client.post("/v1/ask", json={"question": "how does a network learn?"})
    call = _llm_calls()[-1]
    assert call.status == "failed" and call.error


# ── streaming ────────────────────────────────────────────────────────────


def _events(response) -> list[tuple[str, dict]]:
    out, event = [], None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: ") and event:
            out.append((event, json.loads(line.removeprefix("data: "))))
    return out


def test_stream_emits_sources_then_deltas_then_result(library, corpus) -> None:
    response = library.client.post(
        "/v1/ask/stream", json={"question": "how does a neural network learn?"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _events(response)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "sources" and kinds[-1] == "result"
    assert kinds.count("delta") > 1, "streamed in pieces"

    sources = events[0][1]
    assert sources and sources[0]["marker"] == 1

    streamed = "".join(payload["text"] for kind, payload in events if kind == "delta")
    result = events[-1][1]
    assert "[S1]" in streamed
    assert result["grounded"] is True and result["citations"]
    assert result["answer"].replace(" ", "") == streamed.replace(" ", "")


def test_stream_refusal_has_no_sources(library, corpus) -> None:
    response = library.client.post(
        "/v1/ask/stream", json={"question": "zzqx nonexistent topic about tubeworms"}
    )
    events = _events(response)
    assert events[0] == ("sources", [])
    assert events[-1][1]["refused"] is True


def test_stream_reports_provider_failure_as_an_error_event(library, corpus, settings_env) -> None:
    _set_mode(settings_env, "unavailable")
    response = library.client.post("/v1/ask/stream", json={"question": "how does it learn?"})
    assert response.status_code == 200, "the stream opened before the failure"
    kind, payload = _events(response)[-1]
    assert kind == "error" and payload["status"] == 503 and payload["detail"]


# ── status endpoint ──────────────────────────────────────────────────────


def test_llm_status(library) -> None:
    body = library.client.get("/v1/llm/status").json()
    assert body["enabled"] and body["provider"] == "fake"
    assert body["reachable"] and body["model_available"]
    assert body["context_window"] > 0 and body["max_output_tokens"] > 0


def test_llm_status_when_disabled(library, settings_env) -> None:
    settings_env.setenv("LLM_PROVIDER", "none")
    get_settings.cache_clear()
    reset_llm_provider_cache()
    body = library.client.get("/v1/llm/status").json()
    assert body["enabled"] is False and body["model"] is None


# ── Phase 1/2 must be untouched ──────────────────────────────────────────


def test_search_and_library_still_work(library, corpus) -> None:
    search = library.client.get("/v1/search", params={"q": "pumping lemma", "mode": "keyword"})
    assert search.status_code == 200 and search.json()["results"]
    documents = library.client.get("/v1/documents", params={"q": "automata"}).json()
    assert documents["items"], "title lookup unaffected"
    assert library.client.get("/v1/embeddings/status").json()["enabled"]
