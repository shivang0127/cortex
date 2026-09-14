"""The embedding provider seam (ARCHITECTURE.md §7).

Implementations arrive in Phase 2 (a local model first) and are selected by
`Settings.embedding_provider`. `dimension` and `model_id` matter beyond
bookkeeping: the `embeddings` table is keyed by model and its `vector(N)` column
is sized to `dimension`, so switching models is a planned migration rather than
a surprise.
"""

from collections.abc import Sequence
from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch. Returns one vector per input, in order, each of length `dimension`."""
        ...
