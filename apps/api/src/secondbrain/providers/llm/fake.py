"""A deterministic stand-in LLM: no model, no runtime, instant and repeatable.

Real model output is nondeterministic, so almost nothing about the RAG pipeline
could be asserted against it. This provider answers by *quoting back the source
markers it was given*, which makes every part around the model testable exactly:
context construction, citation parsing, citation validation, refusal handling,
streaming, timeouts and error paths.

`mode` selects the behaviour a test needs:

* `normal`    — cites every source it was shown, in order.
* `refusal`   — emits the configured refusal sentence and no citations.
* `invented`  — cites `[S99]`, which the validator must strip.
* `uncited`   — a fluent answer with no markers at all (`grounded` must be false).
* `timeout` / `error` / `unavailable` — raises the matching exception.

It knows nothing about meaning; `LLM_PROVIDER=fake` is for tests and for
working on the pipeline without a model loaded.
"""

import re
import time
from collections.abc import Iterator, Sequence
from typing import TypeVar

from pydantic import BaseModel

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

T = TypeVar("T", bound=BaseModel)

_MARKER = re.compile(r"\[S(\d+)\]")
# The refusal *contract* — what a real model is told to emit (see prompts/v1/answer.md).
# The service turns this into user-facing prose; the fake must speak the same protocol.
FAKE_REFUSAL = "INSUFFICIENT_EVIDENCE\nThe sources do not cover this."


class FakeLLMProvider:
    provider_id = "fake"

    def __init__(
        self,
        *,
        mode: str = "normal",
        model_id: str = "fake-echo-v1",
        context_window: int = 4096,
        delay_seconds: float = 0.0,
    ) -> None:
        self.mode = mode
        self._model_id = model_id
        self._context_window = context_window
        self._delay = delay_seconds
        self.calls: list[list[Message]] = []  # tests assert on what the pipeline sent

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def context_window(self) -> int:
        return self._context_window

    def _answer(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self.mode == "timeout":
            raise LLMTimeout("fake provider: simulated timeout")
        if self.mode == "error":
            raise LLMError("fake provider: simulated failure")
        if self.mode == "unavailable":
            raise LLMUnavailable("fake provider: simulated unreachable runtime")
        if self._delay:
            time.sleep(self._delay)
        if self.mode == "refusal":
            return FAKE_REFUSAL
        if self.mode == "invented":
            return "This claim comes from a source that was never supplied [S99]."
        if self.mode == "uncited":
            return "A confident answer that cites nothing at all."

        # `normal`: echo the markers the context actually offered, in order.
        prompt = "\n".join(m.content for m in messages)
        markers = sorted({int(n) for n in _MARKER.findall(prompt)})
        if not markers:
            return FAKE_REFUSAL
        cited = " ".join(f"Point {i} is supported [S{n}]." for i, n in enumerate(markers, start=1))
        return f"Based on your library: {cited}"

    def complete(
        self, messages: Sequence[Message], config: GenerationConfig | None = None
    ) -> Completion[str]:
        started = time.perf_counter()
        text = self._answer(messages)
        return Completion(
            output=text,
            usage=Usage(
                input_tokens=sum(len(m.content.split()) for m in messages),
                output_tokens=len(text.split()),
            ),
            model_id=self._model_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def stream(
        self, messages: Sequence[Message], config: GenerationConfig | None = None
    ) -> Iterator[str]:
        # Word by word, so a streaming consumer sees several deltas — and so a
        # marker split across deltas is exercised the way a real model does it.
        for index, word in enumerate(self._answer(messages).split(" ")):
            yield word if index == 0 else f" {word}"

    def complete_structured(
        self,
        messages: Sequence[Message],
        schema: type[T],
        config: GenerationConfig | None = None,
    ) -> Completion[T]:
        self.calls.append(list(messages))
        raise LLMError("the fake provider does not implement structured output yet")

    def health(self) -> ProviderHealth:
        ok = self.mode not in ("unavailable",)
        return ProviderHealth(
            reachable=ok,
            model_available=ok,
            provider_id=self.provider_id,
            model_id=self._model_id,
            context_window=self._context_window,
            detail=None if ok else "fake provider: simulated unreachable runtime",
        )
