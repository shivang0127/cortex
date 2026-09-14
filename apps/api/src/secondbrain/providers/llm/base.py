"""The LLM provider seam (ARCHITECTURE.md §6 — "local, free models first").

Nothing above this protocol knows which model is answering. Implementations
(Phase 3: a local runtime such as Ollama; later, hosted providers) live beside
this file and are selected by `Settings.llm_provider`. Prompt logic does NOT
belong here — task classes own prompts and output schemas; a provider only turns
messages into a validated object.
"""

from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None  # None for local models


@dataclass(frozen=True)
class Completion[T]:
    output: T
    usage: Usage
    model_id: str
    latency_ms: int


class LLMProvider(Protocol):
    """Structured completion: messages in, a validated Pydantic model out."""

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def complete_structured(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
    ) -> Completion[T]: ...
