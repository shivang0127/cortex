"""Local embeddings with `fastembed` (ONNX Runtime on CPU; no PyTorch, no service).

The first call downloads the model (tens of MB) from Hugging Face into
`Settings.embedding_cache_dir` and loads it; both happen once per process.
`fastembed` knows each model's query/passage conventions, so `query_embed`
applies bge's instruction prefix for us.
"""

import logging
import os
from collections.abc import Sequence
from pathlib import Path
from threading import Lock

from secondbrain.providers.embedding.base import EmbeddingError

log = logging.getLogger(__name__)

BATCH_SIZE = 32


class FastEmbedProvider:
    provider_id = "fastembed"

    def __init__(self, model_id: str, cache_dir: Path, *, threads: int | None = None) -> None:
        self._model_id = model_id
        self._cache_dir = cache_dir
        self._threads = threads
        self._model = None
        self._dimension: int | None = None
        self._lock = Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = self._lookup_dimension()
        return self._dimension

    def _lookup_dimension(self) -> int:
        from fastembed import TextEmbedding

        for spec in TextEmbedding.list_supported_models():
            if spec["model"] == self._model_id:
                return int(spec["dim"])
        raise EmbeddingError(
            f"{self._model_id!r} is not a model fastembed knows; "
            "see TextEmbedding.list_supported_models()"
        )

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                    self._cache_dir.mkdir(parents=True, exist_ok=True)
                    log.info(
                        "loading embedding model %s (cache %s)", self._model_id, self._cache_dir
                    )
                    try:
                        self._model = TextEmbedding(
                            self._model_id, cache_dir=str(self._cache_dir), threads=self._threads
                        )
                    except Exception as exc:  # download failure, corrupt cache, missing runtime
                        raise EmbeddingError(
                            f"could not load embedding model {self._model_id}: {exc}"
                        ) from exc
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        try:
            return [v.tolist() for v in model.passage_embed(list(texts), batch_size=BATCH_SIZE)]
        except Exception as exc:
            raise EmbeddingError(f"embedding failed: {exc}") from exc

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        try:
            return next(iter(model.query_embed([text]))).tolist()
        except Exception as exc:
            raise EmbeddingError(f"query embedding failed: {exc}") from exc
