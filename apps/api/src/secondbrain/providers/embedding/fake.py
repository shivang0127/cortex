"""A deterministic stand-in provider: no model, no download, instant.

Each word hashes to a fixed pseudo-random direction and a text is the
normalised sum of its words' directions, so cosine similarity tracks word
overlap. That is enough to exercise every part of the pipeline and the search
plumbing — persistence, idempotency, ANN queries, fusion, ranking — without a
model, and it is what the integration tests run on. It knows nothing about
meaning; `EMBEDDING_PROVIDER=fake` is for tests and model-less development.
"""

import hashlib
import math
import re
from collections.abc import Sequence

_WORD = re.compile(r"[a-z0-9]+")


class FakeEmbeddingProvider:
    provider_id = "fake"

    def __init__(self, dimension: int = 384, model_id: str = "fake-bow-v1") -> None:
        self._dimension = dimension
        self._model_id = model_id
        self._cache: dict[str, list[float]] = {}

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    def _word_vector(self, word: str) -> list[float]:
        if word not in self._cache:
            vec: list[float] = []
            counter = 0
            while len(vec) < self._dimension:
                digest = hashlib.blake2b(f"{word}:{counter}".encode(), digest_size=32).digest()
                vec.extend((b - 127.5) / 127.5 for b in digest)
                counter += 1
            self._cache[word] = vec[: self._dimension]
        return self._cache[word]

    def _embed_one(self, text: str) -> list[float]:
        acc = [0.0] * self._dimension
        words = _WORD.findall(text.lower())
        for word in words:
            wv = self._word_vector(word)
            for i in range(self._dimension):
                acc[i] += wv[i]
        norm = math.sqrt(sum(x * x for x in acc))
        if norm == 0.0:  # empty text: a fixed unit vector on the first axis
            acc[0] = 1.0
            return acc
        return [x / norm for x in acc]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)
