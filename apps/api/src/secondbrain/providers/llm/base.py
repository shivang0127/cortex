"""The LLM provider seam (ARCHITECTURE.md §6 — "local, free models first").

Nothing above this protocol knows which model is answering. Implementations
(`ollama` for the local runtime, `fake` for tests, hosted vendors later) live
beside this file and are selected by `Settings.llm_provider`. Prompt logic does
NOT belong here — task classes own prompts and output schemas; a provider only
turns messages into text, a stream of text, or a validated object.

Three entry points, because the application needs three shapes:

* `stream`   — tokens as they are produced. What "Ask Second Brain" uses: a
               local 4B model takes seconds, and a spinner for that long is a
               bad answer to a good question.
* `complete` — the same thing collected into one string, for the non-streaming
               endpoint and for tests.
* `complete_structured` — messages in, a validated Pydantic model out. Unused
               in Phase 3; it is what Phase 4's extraction tasks will call.

Errors are split the way the rest of the system splits them: `LLMUnavailable`
means the runtime is not there (connection refused, model not pulled) and is a
503; `LLMTimeout` is a 504; `LLMError` is anything else the provider could not
do.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The provider could not produce a completion."""


class LLMUnavailable(LLMError):
    """The runtime is unreachable or the model is not installed. Retryable by the user."""


class LLMTimeout(LLMError):
    """Generation exceeded the configured deadline."""


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None  # None for local models — the cost is time, not money


@dataclass(frozen=True)
class Completion[T]:
    output: T
    usage: Usage
    model_id: str
    latency_ms: int
    truncated: bool = False  # generation stopped at max_output_tokens, not at an end token


@dataclass(frozen=True)
class ProviderHealth:
    reachable: bool
    model_available: bool
    provider_id: str
    model_id: str
    context_window: int
    detail: str | None = None


@dataclass(frozen=True)
class GenerationConfig:
    """Everything a caller may vary per request. Defaults come from Settings."""

    temperature: float = 0.1
    max_output_tokens: int = 512
    context_tokens: int = 4096
    stop: Sequence[str] = ()
    seed: int | None = None


class LLMProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def context_window(self) -> int: ...

    def complete(
        self, messages: Sequence[Message], config: GenerationConfig | None = None
    ) -> Completion[str]: ...

    def stream(
        self, messages: Sequence[Message], config: GenerationConfig | None = None
    ) -> Iterator[str]:
        """Yield text deltas in order. The concatenation equals `complete().output`."""
        ...

    def complete_structured(
        self,
        messages: Sequence[Message],
        schema: type[T],
        config: GenerationConfig | None = None,
    ) -> Completion[T]: ...

    def health(self) -> ProviderHealth: ...
