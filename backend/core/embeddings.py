"""Batched Ollama embedding helper with nomic-embed-text query/document prefixes."""

from __future__ import annotations

import logging
import threading
from typing import List, Optional, Sequence

from backend.core.ollama import OllamaClient

logger = logging.getLogger("omnivault.embeddings")

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class EmbeddingService:
    def __init__(
        self,
        ollama: OllamaClient,
        model: str,
        keep_alive: str = "10m",
        batch_size: int = 32,
    ) -> None:
        self.ollama = ollama
        self.model = model
        self.keep_alive = keep_alive
        self.batch_size = batch_size
        self._lock = threading.Lock()
        self._dimension: Optional[int] = None

    def set_model(self, model: str) -> None:
        if model and model != self.model:
            self.model = model
            self._dimension = None

    @property
    def uses_nomic_prefix(self) -> bool:
        return "nomic-embed" in (self.model or "").lower()

    def _prepare(self, texts: Sequence[str], *, for_query: bool) -> List[str]:
        if not self.uses_nomic_prefix:
            return [text if text.strip() else " " for text in texts]
        prefix = QUERY_PREFIX if for_query else DOCUMENT_PREFIX
        prepared: List[str] = []
        for text in texts:
            body = text.strip() or " "
            if body.startswith(DOCUMENT_PREFIX) or body.startswith(QUERY_PREFIX):
                prepared.append(body)
            else:
                prepared.append(prefix + body)
        return prepared

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        return self._embed(texts, for_query=False)

    def embed_query(self, text: str) -> List[float]:
        vectors = self._embed([text], for_query=True)
        return vectors[0]

    def _embed(self, texts: Sequence[str], *, for_query: bool) -> List[List[float]]:
        if not texts:
            return []
        if not self.model:
            raise RuntimeError("No embedding model configured. Install nomic-embed-text in Ollama.")
        prepared = self._prepare(texts, for_query=for_query)
        vectors: List[List[float]] = []
        with self._lock:
            for start in range(0, len(prepared), self.batch_size):
                batch = prepared[start : start + self.batch_size]
                try:
                    batch_vectors = self.ollama.embed(self.model, batch, keep_alive=self.keep_alive)
                except Exception as exc:
                    logger.error("Embedding batch failed: %s", exc)
                    raise RuntimeError(f"Embedding failed via Ollama model '{self.model}': {exc}") from exc
                if len(batch_vectors) != len(batch):
                    raise RuntimeError(
                        f"Ollama returned {len(batch_vectors)} embeddings for {len(batch)} inputs"
                    )
                vectors.extend(batch_vectors)
        if vectors and self._dimension is None:
            self._dimension = len(vectors[0])
        return vectors

    @property
    def dimension(self) -> Optional[int]:
        return self._dimension
