"""LLM provider behaviour: the deterministic fake always, Ollama when it is running."""

import json

import httpx
import pytest

from secondbrain.config import Settings, get_settings
from secondbrain.providers.llm import (
    LLMDisabled,
    build_llm_provider,
    get_llm_provider,
    llm_enabled,
    reset_llm_provider_cache,
)
from secondbrain.providers.llm.base import (
    GenerationConfig,
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    Message,
)
from secondbrain.providers.llm.fake import FAKE_REFUSAL, FakeLLMProvider
from secondbrain.providers.llm.ollama import OllamaProvider

SOURCES = [
    Message(role="system", content="Sources:\n\n[S1] doc one\ntext\n\n[S2] doc two\ntext"),
    Message(role="user", content="what do they say?"),
]


# ── fake provider ─────────────────────────────────────────────────────────


def test_fake_provider_echoes_the_markers_it_was_shown() -> None:
    provider = FakeLLMProvider()
    completion = provider.complete(SOURCES)
    assert "[S1]" in completion.output and "[S2]" in completion.output
    assert completion.model_id == "fake-echo-v1"
    assert completion.usage.output_tokens
    assert provider.calls and provider.calls[0][1].content == "what do they say?"


def test_fake_provider_is_deterministic() -> None:
    a, b = FakeLLMProvider().complete(SOURCES), FakeLLMProvider().complete(SOURCES)
    assert a.output == b.output


def test_fake_provider_stream_concatenates_to_complete() -> None:
    provider = FakeLLMProvider()
    deltas = list(provider.stream(SOURCES))
    assert len(deltas) > 1, "streams in several pieces"
    assert "".join(deltas) == provider.complete(SOURCES).output


@pytest.mark.parametrize(
    ("mode", "check"),
    [
        ("refusal", lambda text: text == FAKE_REFUSAL),
        ("invented", lambda text: "[S99]" in text),
        ("uncited", lambda text: "[S" not in text),
    ],
)
def test_fake_provider_modes(mode: str, check) -> None:
    assert check(FakeLLMProvider(mode=mode).complete(SOURCES).output)


@pytest.mark.parametrize(
    ("mode", "exception"),
    [("timeout", LLMTimeout), ("error", LLMError), ("unavailable", LLMUnavailable)],
)
def test_fake_provider_failure_modes(mode: str, exception: type[Exception]) -> None:
    with pytest.raises(exception):
        FakeLLMProvider(mode=mode).complete(SOURCES)


def test_fake_provider_without_sources_refuses() -> None:
    plain = [Message(role="user", content="hello")]
    assert FakeLLMProvider().complete(plain).output == FAKE_REFUSAL


def test_fake_provider_health() -> None:
    assert FakeLLMProvider().health().reachable
    unhealthy = FakeLLMProvider(mode="unavailable").health()
    assert not unhealthy.reachable and unhealthy.detail


# ── factory ───────────────────────────────────────────────────────────────


def test_factory_selects_provider_from_settings() -> None:
    fake = build_llm_provider(Settings(llm_provider="fake", llm_context_tokens=2048))
    assert fake.provider_id == "fake" and fake.context_window == 2048
    real = build_llm_provider(Settings(llm_provider="ollama", llm_model="qwen3:4b"))
    assert real.provider_id == "ollama" and real.model_id == "qwen3:4b"
    with pytest.raises(LLMDisabled):
        build_llm_provider(Settings(llm_provider="none"))


def test_cached_provider_follows_settings(settings_env: pytest.MonkeyPatch) -> None:
    settings_env.setenv("LLM_PROVIDER", "fake")
    get_settings.cache_clear()
    reset_llm_provider_cache()
    assert get_llm_provider() is get_llm_provider()
    assert llm_enabled() and get_llm_provider().provider_id == "fake"
    reset_llm_provider_cache()


def test_disabled_provider(settings_env: pytest.MonkeyPatch) -> None:
    settings_env.setenv("LLM_PROVIDER", "none")
    get_settings.cache_clear()
    reset_llm_provider_cache()
    assert not llm_enabled()
    with pytest.raises(LLMDisabled):
        get_llm_provider()
    reset_llm_provider_cache()


# ── Ollama provider, against a stub transport (no runtime needed) ─────────


_REAL_CLIENT = httpx.Client


def stub_ollama(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route every httpx.Client the provider builds through a MockTransport."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: _REAL_CLIENT(**{**kw, "transport": transport})
    )


def make_provider(model: str = "qwen3:4b") -> OllamaProvider:
    return OllamaProvider(
        model,
        "http://127.0.0.1:11434",
        context_window=4096,
        timeout_seconds=5.0,
        keep_alive="30m",
    )


def test_ollama_builds_the_expected_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "answer [S1]"},
                "prompt_eval_count": 120,
                "eval_count": 8,
                "done_reason": "stop",
            },
        )

    stub_ollama(monkeypatch, handler)
    provider = make_provider("qwen3:4b")
    completion = provider.complete(SOURCES, GenerationConfig(temperature=0.2, max_output_tokens=64))

    assert captured["path"] == "/api/chat"
    assert captured["model"] == "qwen3:4b" and captured["stream"] is False
    assert captured["keep_alive"] == "30m"
    assert captured["options"] == {"temperature": 0.2, "num_predict": 64, "num_ctx": 4096}
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]
    assert completion.output == "answer [S1]"
    assert completion.usage.input_tokens == 120 and completion.usage.output_tokens == 8
    assert completion.usage.cost_usd is None and not completion.truncated


def test_ollama_reports_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"content": "cut off"}, "done_reason": "length"}
        )

    stub_ollama(monkeypatch, handler)
    provider = make_provider("qwen3:4b")
    assert provider.complete(SOURCES).truncated


def test_ollama_streams_ndjson(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        json.dumps({"message": {"content": "Hello"}, "done": False}),
        json.dumps({"message": {"content": " world"}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="\n".join(lines).encode())

    stub_ollama(monkeypatch, handler)
    provider = make_provider("qwen3:4b")
    assert list(provider.stream(SOURCES)) == ["Hello", " world"]


def test_ollama_maps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    stub_ollama(monkeypatch, refuse)
    provider = make_provider("qwen3:4b")
    with pytest.raises(LLMUnavailable, match="cannot reach Ollama"):
        provider.complete(SOURCES)
    assert provider.health().reachable is False

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    stub_ollama(monkeypatch, missing)
    with pytest.raises(LLMUnavailable, match="ollama pull"):
        provider.complete(SOURCES)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    stub_ollama(monkeypatch, timeout)
    with pytest.raises(LLMTimeout):
        provider.complete(SOURCES)


def test_ollama_health_checks_the_model_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}, {"name": "other:8b"}]})

    stub_ollama(monkeypatch, handler)
    present = make_provider("qwen3:4b").health()
    assert present.reachable and present.model_available and present.context_window == 4096

    absent = make_provider("qwen3:1.7b").health()
    assert absent.reachable and not absent.model_available and "ollama pull" in absent.detail


def test_ollama_disables_thinking_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Qwen3 reasons before answering unless told not to — 84s and an empty answer,
    measured, versus 7.6s with it off. The flag must be on every request."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    stub_ollama(monkeypatch, handler)
    make_provider().complete(SOURCES)
    assert captured["think"] is False

    thinker = OllamaProvider(
        "qwen3:4b",
        "http://127.0.0.1:11434",
        context_window=4096,
        timeout_seconds=5.0,
        keep_alive="30m",
        thinking=True,
    )
    thinker.complete(SOURCES)
    assert captured["think"] is True, "still configurable for a model that needs it"
