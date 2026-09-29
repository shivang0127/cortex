"""Local generation through Ollama's native HTTP API.

Ollama runs as a background service on `127.0.0.1:11434`, holds one copy of the
model, and splits layers between the RTX 3050's 4 GB of VRAM and the CPU on its
own. That process boundary is the point: a 2.5 GB model loaded *in* the API and
again *in* the worker would cost 5 GB of RAM and have the two fight over the
GPU. (Contrast the 67 MB embedding model, which is in-process for good reason.)

`/api/chat` is used rather than the OpenAI-compatible `/v1/chat/completions`
because `format` (JSON schema), `num_ctx` and `keep_alive` are first-class
there. The OpenAI shape is what a hosted adapter will look like, and it is one
more sibling of this file when that day comes — not something the RAG pipeline
above ever sees.
"""

import json
import logging
import time
from collections.abc import Iterator, Sequence
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from secondbrain.providers.llm.base import (
    Completion,
    GenerationConfig,
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    Message,
    ProviderHealth,
    Usage,
)

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OllamaProvider:
    provider_id = "ollama"

    def __init__(
        self,
        model_id: str,
        base_url: str,
        *,
        context_window: int,
        timeout_seconds: float,
        keep_alive: str,
        thinking: bool = False,
    ) -> None:
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._context_window = context_window
        self._timeout = timeout_seconds
        self._keep_alive = keep_alive
        self._thinking = thinking

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def context_window(self) -> int:
        return self._context_window

    # ── request building ──────────────────────────────────────────────────

    def _options(self, config: GenerationConfig) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": config.temperature,
            "num_predict": config.max_output_tokens,
            "num_ctx": config.context_tokens or self._context_window,
        }
        if config.stop:
            options["stop"] = list(config.stop)
        if config.seed is not None:
            options["seed"] = config.seed
        return options

    def _payload(
        self, messages: Sequence[Message], config: GenerationConfig, *, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": self._model_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            # Thinking-capable models (every Qwen3 tag) reason before answering unless
            # told not to. For RAG that is pure latency: measured 84s with an empty
            # answer versus 7.6s without.
            "think": self._thinking,
            "keep_alive": self._keep_alive,
            "options": self._options(config),
        }

    def _post(self, path: str, payload: dict[str, Any], *, stream: bool = False):
        client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
        try:
            if stream:
                return client, client.stream("POST", path, json=payload)
            response = client.post(path, json=payload)
            response.raise_for_status()
            return client, response
        except httpx.ConnectError as exc:
            client.close()
            raise LLMUnavailable(
                f"cannot reach Ollama at {self._base_url} — is the service running?"
            ) from exc
        except httpx.TimeoutException as exc:
            client.close()
            raise LLMTimeout(f"Ollama did not respond within {self._timeout:.0f}s") from exc
        except httpx.HTTPStatusError as exc:
            client.close()
            raise self._status_error(exc) from exc

    def _status_error(self, exc: httpx.HTTPStatusError) -> LLMError:
        detail = exc.response.text.strip()[:400]
        if exc.response.status_code == 404:
            return LLMUnavailable(
                f"model {self._model_id!r} is not installed — run `ollama pull {self._model_id}`"
            )
        return LLMError(f"Ollama returned HTTP {exc.response.status_code}: {detail}")

    # ── generation ────────────────────────────────────────────────────────

    def complete(
        self, messages: Sequence[Message], config: GenerationConfig | None = None
    ) -> Completion[str]:
        config = config or GenerationConfig()
        started = time.perf_counter()
        client, response = self._post("/api/chat", self._payload(messages, config, stream=False))
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError("Ollama returned a non-JSON response") from exc
        finally:
            client.close()
        text = (body.get("message") or {}).get("content", "")
        return Completion(
            output=text,
            usage=Usage(
                input_tokens=body.get("prompt_eval_count"),
                output_tokens=body.get("eval_count"),
            ),
            model_id=self._model_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
            truncated=body.get("done_reason") == "length",
        )

    def stream(
        self, messages: Sequence[Message], config: GenerationConfig | None = None
    ) -> Iterator[str]:
        config = config or GenerationConfig()
        client, ctx = self._post(
            "/api/chat", self._payload(messages, config, stream=True), stream=True
        )
        try:
            with ctx as response:
                if response.status_code >= 400:
                    response.read()
                    raise self._status_error(
                        httpx.HTTPStatusError("error", request=response.request, response=response)
                    )
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue  # Ollama emits one JSON object per line; skip anything else
                    if error := event.get("error"):
                        raise LLMError(f"Ollama error mid-stream: {error}")
                    delta = (event.get("message") or {}).get("content", "")
                    if delta:
                        yield delta
                    if event.get("done"):
                        break
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"Ollama stalled for more than {self._timeout:.0f}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama stream failed: {exc}") from exc
        finally:
            client.close()

    def complete_structured(
        self,
        messages: Sequence[Message],
        schema: type[T],
        config: GenerationConfig | None = None,
    ) -> Completion[T]:
        """Phase 4's entry point: constrain output to a JSON schema and validate it."""
        config = config or GenerationConfig()
        payload = self._payload(messages, config, stream=False)
        payload["format"] = schema.model_json_schema()
        started = time.perf_counter()
        client, response = self._post("/api/chat", payload)
        try:
            body = response.json()
        finally:
            client.close()
        raw = (body.get("message") or {}).get("content", "")
        try:
            output = schema.model_validate_json(raw)
        except ValidationError as exc:
            raise LLMError(f"model output did not match {schema.__name__}: {exc}") from exc
        return Completion(
            output=output,
            usage=Usage(
                input_tokens=body.get("prompt_eval_count"),
                output_tokens=body.get("eval_count"),
            ),
            model_id=self._model_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ── health ────────────────────────────────────────────────────────────

    def health(self) -> ProviderHealth:
        try:
            with httpx.Client(base_url=self._base_url, timeout=5.0) as client:
                response = client.get("/api/tags")
                response.raise_for_status()
                installed = {m["name"] for m in response.json().get("models", [])}
        except httpx.HTTPError as exc:
            return ProviderHealth(
                reachable=False,
                model_available=False,
                provider_id=self.provider_id,
                model_id=self._model_id,
                context_window=self._context_window,
                detail=f"cannot reach Ollama at {self._base_url}: {exc}",
            )
        # Ollama reports "qwen3:4b"; a bare "qwen3" means the :latest tag.
        wanted = self._model_id if ":" in self._model_id else f"{self._model_id}:latest"
        available = wanted in installed
        return ProviderHealth(
            reachable=True,
            model_available=available,
            provider_id=self.provider_id,
            model_id=self._model_id,
            context_window=self._context_window,
            detail=None if available else f"run `ollama pull {self._model_id}`",
        )
