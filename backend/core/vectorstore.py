"""Persistent ChromaDB collection shared by every modality."""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence

import chromadb
from chromadb.config import Settings as ChromaSettings

from backend.config import Settings
from backend.core.embeddings import EmbeddingService

logger = logging.getLogger("omnivault.vectorstore")

COLLECTION_NAME = "omnivault_memory"


def _retarget_department_line(text: str, department: str) -> str:
    return re.sub(r"(?im)^(Department:\s*)\S+", rf"\1{department}", text or "", count=1)


def _as_rows(embeddings: Any) -> List[List[float]]:
    if embeddings is None:
        return []
    if hasattr(embeddings, "tolist"):
        embeddings = embeddings.tolist()
    rows = []
    for row in embeddings:
        if hasattr(row, "tolist"):
            row = row.tolist()
        rows.append([float(value) for value in row])
    return rows


def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


class VectorStore:
    def __init__(self, settings: Settings, embeddings: EmbeddingService) -> None:
        self.settings = settings
        self.embeddings = embeddings
        self._client = chromadb.PersistentClient(
            path=str(settings.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "platform": "omnivault"},
            embedding_function=None,
        )

    @property
    def collection(self):
        return self._collection

    def count(self) -> int:
        return int(self._collection.count())

    def retag_department(self, source: str, target: str) -> int:
        try:
            found = self._collection.get(where={"department": source}, include=["metadatas"])
        except Exception:
            return 0
        ids = found.get("ids") or []
        metas = found.get("metadatas") or []
        if not ids:
            return 0
        updated = []
        for meta in metas:
            row = dict(meta or {})
            row["department"] = target
            updated.append(row)
        self._collection.update(ids=ids, metadatas=updated)
        return len(ids)

    def count_department(self, department: Optional[str]) -> int:
        if not department or department in {"all", "tumu", "tümü", "*"}:
            return self.count()
        try:
            found = self._collection.get(where={"department": department}, include=[])
            return len(found.get("ids") or [])
        except Exception:
            return 0

    def upsert_chunks(self, chunks: Sequence[Dict[str, Any]]) -> int:
        if not chunks:
            return 0
        ids = [chunk["id"] for chunk in chunks]
        documents = [chunk["text"] for chunk in chunks]
        metadatas = [_sanitize_metadata(chunk.get("metadata") or {}) for chunk in chunks]
        vectors = self.embeddings.embed_documents(documents)
        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=vectors)
        return len(ids)

    def chunks_of(self, doc_id: str) -> Dict[str, Any]:
        try:
            return self._collection.get(
                where={"doc_id": doc_id},
                include=["documents", "metadatas", "embeddings"],
            )
        except Exception as exc:
            logger.warning("Unable to read vectors for %s: %s", doc_id, exc)
            return {"ids": [], "documents": [], "metadatas": [], "embeddings": []}

    def set_document_department(self, doc_id: str, department: str) -> int:
        """Point existing vectors at another department without embedding them again."""
        found = self.chunks_of(doc_id)
        ids = list(found.get("ids") or [])
        if not ids:
            return 0
        metadatas = []
        documents = []
        for index, meta in enumerate(found.get("metadatas") or []):
            row = dict(meta or {})
            row["department"] = department
            metadatas.append(_sanitize_metadata(row))
            text = (found.get("documents") or [""])[index] or ""
            documents.append(_retarget_department_line(text, department))
        embeddings = _as_rows(found.get("embeddings"))
        # Pass the stored vectors. Without them Chroma re-embeds with its 384-d
        # default model and rejects the write against the 768-d nomic collection.
        if len(embeddings) == len(ids):
            self._collection.update(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
        else:
            self._collection.update(ids=ids, metadatas=metadatas)
        return len(ids)

    def clone_document_vectors(self, source_id: str, target_id: str, department: str) -> int:
        """Duplicate stored embeddings under a new document id. No model call."""
        found = self.chunks_of(source_id)
        ids = list(found.get("ids") or [])
        if not ids:
            return 0
        embeddings = _as_rows(found.get("embeddings"))
        if len(embeddings) != len(ids):
            raise RuntimeError("Stored vectors cannot be copied because their embeddings are missing.")
        new_ids = []
        documents = []
        metadatas = []
        copied = []
        for index, meta in enumerate(found.get("metadatas") or []):
            row = dict(meta or {})
            chunk_index = int(row.get("chunk_index") or index)
            row["doc_id"] = target_id
            row["department"] = department
            row["chunk_index"] = chunk_index
            new_ids.append(f"{target_id}:{chunk_index}")
            text = (found.get("documents") or [""])[index] or ""
            documents.append(_retarget_department_line(text, department))
            metadatas.append(_sanitize_metadata(row))
            copied.append(embeddings[index])
        self._collection.upsert(ids=new_ids, documents=documents, metadatas=metadatas, embeddings=copied)
        return len(new_ids)

    def delete_document(self, doc_id: str) -> None:
        try:
            self._collection.delete(where={"doc_id": doc_id})
        except Exception as exc:
            logger.warning("Vector delete for %s failed: %s", doc_id, exc)

    def query(
        self,
        query_text: str,
        *,
        top_k: int = 6,
        modalities: Optional[Sequence[str]] = None,
        doc_ids: Optional[Sequence[str]] = None,
        departments: Optional[Sequence[str]] = None,
        pool: int = 12,
    ) -> List[Dict[str, Any]]:
        if self.count() == 0:
            return []
        vector = self.embeddings.embed_query(query_text)
        where = self._build_where(modalities=modalities, doc_ids=doc_ids, departments=departments)
        kwargs: Dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": min(max(pool, top_k), max(self.count(), 1)),
            "include": ["documents", "metadatas", "distances"],
        }
        if where is not None:
            kwargs["where"] = where
        try:
            result = self._collection.query(**kwargs)
        except Exception as exc:
            # Empty filter intersections raise in some Chroma versions; treat as no hits.
            logger.warning("Vector query failed: %s", exc)
            return []
        hits = self._unpack(result)
        return self._hybrid_rerank(query_text, hits)[:top_k]

    @staticmethod
    def _build_where(
        modalities: Optional[Sequence[str]] = None,
        doc_ids: Optional[Sequence[str]] = None,
        departments: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        clauses: List[Dict[str, Any]] = []
        if departments:
            values = list(departments)
            clauses.append({"department": {"$in": values}} if len(values) > 1 else {"department": values[0]})
        if modalities:
            values = list(modalities)
            clauses.append({"modality": {"$in": values}} if len(values) > 1 else {"modality": values[0]})
        if doc_ids:
            values = list(doc_ids)
            clauses.append({"doc_id": {"$in": values}} if len(values) > 1 else {"doc_id": values[0]})
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    @staticmethod
    def _tokens(text: str) -> set:
        return set(re.findall(r"[\wçğıöşü]{2,}", (text or "").lower(), flags=re.IGNORECASE))

    @classmethod
    def _hybrid_rerank(cls, query_text: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        query_tokens = cls._tokens(query_text)
        ranked = []
        for hit in hits:
            distance = float(hit.get("distance") or 1.0)
            # Cosine distance from nomic is often 0.25–0.55 even for a clear hit.
            # An exponential map puts a strong match in the 80–95% band.
            vector_score = 1.0 - math.exp(-4.6 * max(0.0, 1.0 - distance))
            doc_tokens = cls._tokens(hit.get("text") or "")
            lexical = (len(query_tokens & doc_tokens) / len(query_tokens)) if query_tokens else 0.0
            # Short questions such as "ben kimim" share almost no keywords with a CV.
            if len(query_tokens) < 4:
                combined = vector_score
            else:
                combined = 0.78 * vector_score + 0.22 * lexical
            combined = max(0.0, min(0.97, combined))
            hit["vector_score"] = vector_score
            hit["lexical"] = lexical
            hit["relevance"] = combined
            ranked.append(hit)
        ranked.sort(key=lambda item: item["relevance"], reverse=True)
        return ranked

    @staticmethod
    def _unpack(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: List[Dict[str, Any]] = []
        for index, chunk_id in enumerate(ids):
            distance = float(distances[index]) if index < len(distances) else 1.0
            # Cosine distance ∈ [0, 2]; map to a 0–100 relevance score.
            relevance = max(0.0, min(1.0, 1.0 - distance))
            hits.append(
                {
                    "id": chunk_id,
                    "text": documents[index] if index < len(documents) else "",
                    "metadata": metadatas[index] if index < len(metadatas) else {},
                    "distance": distance,
                    "relevance": relevance,
                }
            )
        return hits

    def document_text(self, doc_id: str, limit: int = 12) -> str:
        try:
            result = self._collection.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
        except Exception as exc:
            logger.warning("Unable to read chunks for %s: %s", doc_id, exc)
            return ""
        rows = []
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        for index, _chunk_id in enumerate(ids):
            meta = metadatas[index] if index < len(metadatas) else {}
            rows.append((int((meta or {}).get("chunk_index") or index), documents[index] or ""))
        rows.sort(key=lambda item: item[0])
        return "\n\n".join(text for _, text in rows[:limit] if text)

    def get_by_ids(self, chunk_ids: Sequence[str]) -> List[Dict[str, Any]]:
        if not chunk_ids:
            return []
        result = self._collection.get(ids=list(chunk_ids), include=["documents", "metadatas"])
        rows = []
        for index, chunk_id in enumerate(result.get("ids") or []):
            rows.append(
                {
                    "id": chunk_id,
                    "text": (result.get("documents") or [None])[index],
                    "metadata": (result.get("metadatas") or [None])[index] or {},
                }
            )
        return rows
