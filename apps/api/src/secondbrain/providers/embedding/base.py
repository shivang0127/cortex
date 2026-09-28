"""The embedding provider seam (ARCHITECTURE.md §7).

Everything above this protocol — the embedding job, the search service — only
knows that *some* model turns text into unit vectors of `dimension` floats.
Implementations live beside this file and are selected by configuration
(`EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`), so swapping models is an environment
change plus, if the dimension differs, one migration.

`model_id` is stored on every `chunk_embeddings` row: vectors from different
models are never compared, and re-indexing after a model change is a query,
not a guess. Queries and passages are embedded through separate methods
because retrieval models are asymmetric (bge prefixes queries with an
instruction); a provider that makes no distinction simply routes both to one
path.
"""

from collections.abc import Sequence
from typing import Protocol


class EmbeddingError(Exception):
    """The model could not be loaded or run. Retryable: usually a download or resource issue."""


class EmbeddingProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for indexing. One unit vector per input, in order."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query so it can be compared against document vectors."""
        ...
