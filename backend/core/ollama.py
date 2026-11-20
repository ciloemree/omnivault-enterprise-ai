"""Thin, privacy-aware client for a local Ollama instance with Apple Silicon RAM discipline."""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

logger = logging.getLogger("omnivault.ollama")

VISION_HINTS = (
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "qwen2.5vl",
    "qwen2-vl",
    "qwen3-vl",
    "gemma3",
    "llama3.2-vision",
    "llama4",
    "granite3.2-vision",
    "mistral-small3",
)
EMBED_HINTS = ("embed", "nomic-embed", "bge-", "mxbai-embed", "snowflake-arctic-embed", "embeddinggemma")
CHAT_DENY = ("embed", "coder:0.5", "coder:1b")


@dataclass
class ModelInfo:
    name: str
    size: int = 0
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    capabilities: List[str] = field(default_factory=list)
    context_length: Optional[int] = None
    embedding_length: Optional[int] = None

    @property
    def is_vision(self) -> bool:
        caps = {c.lower() for c in self.capabilities}
        if "vision" in caps:
            return True
        lower = self.name.lower()
        return any(token in lower for token in VISION_HINTS)

    @property
    def is_embedding(self) -> bool:
        caps = {c.lower() for c in self.capabilities}
        if "embedding" in caps:
            return True
        lower = self.name.lower()
        return any(token in lower for token in EMBED_HINTS)

    @property
    def is_chat(self) -> bool:
        if self.is_embedding:
            return False
        lower = self.name.lower()
        if any(token in lower for token in CHAT_DENY):
            return False
        caps = {c.lower() for c in self.capabilities}
        return "completion" in caps or "tools" in caps or not caps


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 900.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=5.0))
        self._lock = threading.RLock()
        self._models: Dict[str, ModelInfo] = {}
        self._last_refresh = 0.0
        self._online = False
        self._last_error: Optional[str] = None

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ discovery
    def ping(self) -> bool:
        try:
            response = self._client.get("/api/version")
            response.raise_for_status()
            self._online = True
            self._last_error = None
            return True
        except Exception as exc:
            self._online = False
            self._last_error = str(exc)
            return False

    def refresh_models(self, force: bool = False) -> List[ModelInfo]:
        if not force and self._models and (time.time() - self._last_refresh) < 15:
            return list(self._models.values())
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
            self._online = True
            self._last_error = None
        except Exception as exc:
            self._online = False
            self._last_error = str(exc)
            logger.warning("Ollama /api/tags failed: %s", exc)
            return list(self._models.values())

        discovered: Dict[str, ModelInfo] = {}
        for entry in payload.get("models", []):
            name = entry.get("name") or entry.get("model")
            if not name:
                continue
            details = entry.get("details") or {}
            info = ModelInfo(
                name=name,
                size=int(entry.get("size") or 0),
                family=str(details.get("family") or ""),
                parameter_size=str(details.get("parameter_size") or ""),
                quantization=str(details.get("quantization_level") or ""),
                capabilities=list(entry.get("capabilities") or []),
                context_length=entry.get("details", {}).get("context_length")
                if isinstance(entry.get("details"), dict)
                else entry.get("context_length"),
                embedding_length=entry.get("embedding_length")
                or (entry.get("details") or {}).get("embedding_length"),
            )
            # Prefer capabilities from the tags payload; enrich only when missing.
            if not info.capabilities:
                try:
                    show = self._client.post("/api/show", json={"model": name}, timeout=8.0)
                    if show.status_code == 200:
                        body = show.json()
                        info.capabilities = list(body.get("capabilities") or [])
                        model_info = body.get("model_info") or {}
                        for key, value in model_info.items():
                            if key.endswith(".context_length") and info.context_length is None:
                                info.context_length = int(value)
                            if key.endswith(".embedding_length") and info.embedding_length is None:
                                info.embedding_length = int(value)
                except Exception:
                    pass
            discovered[name] = info

        self._models = discovered
        self._last_refresh = time.time()
        return list(self._models.values())

    def list_models(self) -> List[ModelInfo]:
        return self.refresh_models()

    def get_model(self, name: str) -> Optional[ModelInfo]:
        if not name:
            return None
        self.refresh_models()
        if name in self._models:
            return self._models[name]
        bare = name.split(":")[0]
        for candidate in self._models.values():
            if candidate.name == name or candidate.name.startswith(f"{bare}:"):
                return candidate
        return None

    def select_chat_model(self, preferred: str = "") -> Optional[str]:
        models = self.refresh_models()
        if preferred:
            match = self.get_model(preferred)
            if match and match.is_chat:
                return match.name
        chat_models = [m for m in models if m.is_chat and not m.is_vision]
        if not chat_models:
            chat_models = [m for m in models if m.is_chat]
        if not chat_models:
            return None
        chat_models.sort(key=lambda m: (-m.size, m.name))
        return chat_models[0].name

    def select_vision_model(self, preferred: str = "") -> Optional[str]:
        models = self.refresh_models()
        if preferred:
            match = self.get_model(preferred)
            if match and match.is_vision:
                return match.name
        vision = [m for m in models if m.is_vision]
        if not vision:
            return None
        # Prefer lightweight vision models on Apple Silicon with limited unified memory.
        def score(model: ModelInfo) -> tuple:
            lower = model.name.lower()
            priority = 0
            if "moondream" in lower:
                priority = 3
            elif "llava-phi" in lower or "minicpm" in lower:
                priority = 2
            elif "llava" in lower:
                priority = 1
            return (-priority, model.size, model.name)

        vision.sort(key=score)
        return vision[0].name

    def select_embed_model(self, preferred: str = "") -> Optional[str]:
        models = self.refresh_models()
        if preferred:
            match = self.get_model(preferred)
            if match and match.is_embedding:
                return match.name
        preferred_order = ("nomic-embed-text", "embeddinggemma", "mxbai-embed", "bge-", "snowflake-arctic-embed")
        embeds = [m for m in models if m.is_embedding]
        if not embeds:
            return None
        for token in preferred_order:
            for model in embeds:
                if token in model.name.lower():
                    return model.name
        embeds.sort(key=lambda m: (m.size, m.name))
        return embeds[0].name

    # ------------------------------------------------------------------ RAM discipline
    def loaded_models(self) -> List[Dict[str, Any]]:
        try:
            response = self._client.get("/api/ps")
            response.raise_for_status()
            return list(response.json().get("models") or [])
        except Exception as exc:
            logger.debug("Ollama /api/ps failed: %s", exc)
            return []

    def unload(self, model: str) -> None:
        if not model:
            return
        with self._lock:
            try:
                response = self._client.post(
                    "/api/generate",
                    json={"model": model, "keep_alive": 0},
                    timeout=30.0,
                )
                if response.status_code >= 400:
                    # Embedding-only models often reject /api/generate; fall back to embed unload.
                    self._client.post(
                        "/api/embed",
                        json={"model": model, "input": [], "keep_alive": 0},
                        timeout=30.0,
                    )
                logger.info("Unloaded Ollama model %s (keep_alive=0)", model)
            except Exception as exc:
                logger.warning("Failed to unload %s: %s", model, exc)

    def ensure_slot(self, needed: Sequence[str], unload_others: bool = True) -> None:
        """Unload every resident model that is not in ``needed`` before loading a heavyweight one."""
        if not unload_others:
            return
        needed_set = {n.split(":")[0] for n in needed if n}
        for resident in self.loaded_models():
            name = resident.get("name") or resident.get("model") or ""
            bare = name.split(":")[0]
            if bare and bare not in needed_set and name not in needed:
                self.unload(name)

    # ------------------------------------------------------------------ inference
    def embed(self, model: str, texts: Sequence[str], keep_alive: str = "10m") -> List[List[float]]:
        if not texts:
            return []
        with self._lock:
            response = self._client.post(
                "/api/embed",
                json={"model": model, "input": list(texts), "keep_alive": keep_alive},
            )
            response.raise_for_status()
            payload = response.json()
            vectors = payload.get("embeddings")
            if not isinstance(vectors, list):
                raise RuntimeError(f"Unexpected embed response from Ollama: {payload}")
            return [list(map(float, vector)) for vector in vectors]

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        *,
        keep_alive: str = "5m",
        temperature: float = 0.15,
        num_ctx: int = 6144,
        num_predict: int = 900,
        top_p: float = 0.9,
        repeat_penalty: float = 1.25,
        format: Any = None,
        images: Optional[Sequence[bytes]] = None,
    ) -> Dict[str, Any]:
        payload_messages = [dict(message) for message in messages]
        if images:
            encoded = [base64.b64encode(blob).decode("ascii") for blob in images]
            if payload_messages and payload_messages[-1].get("role") == "user":
                payload_messages[-1] = dict(payload_messages[-1])
                payload_messages[-1]["images"] = encoded
            else:
                payload_messages.append({"role": "user", "content": "", "images": encoded})
        body: Dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "repeat_penalty": repeat_penalty,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }
        if format is not None:
            body["format"] = format
        with self._lock:
            response = self._client.post("/api/chat", json=body)
            response.raise_for_status()
            return response.json()

    def generate(
        self,
        model: str,
        prompt: str,
        *,
        keep_alive: str = "5m",
        temperature: float = 0.15,
        num_predict: int = 400,
        images: Optional[Sequence[bytes]] = None,
        format: Any = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        if images:
            body["images"] = [base64.b64encode(blob).decode("ascii") for blob in images]
        if format is not None:
            body["format"] = format
        with self._lock:
            response = self._client.post("/api/generate", json=body)
            response.raise_for_status()
            return response.json()

    def describe_image(
        self,
        model: str,
        image_bytes: bytes,
        prompt: str,
        *,
        keep_alive: Any = 0,
        num_predict: int = 350,
    ) -> str:
        """Run a vision model and, by default, unload it immediately after (``keep_alive=0``)."""
        with self._lock:
            response = self._client.post(
                "/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [base64.b64encode(image_bytes).decode("ascii")],
                        }
                    ],
                    "stream": False,
                    "keep_alive": keep_alive,
                    "options": {"temperature": 0.1, "num_predict": num_predict},
                },
            )
            response.raise_for_status()
            payload = response.json()
            content = (payload.get("message") or {}).get("content") or payload.get("response") or ""
            return str(content).strip()

    def status(self) -> Dict[str, Any]:
        models = self.refresh_models(force=True)
        return {
            "online": self._online,
            "url": self.base_url,
            "error": self._last_error,
            "models": [
                {
                    "name": m.name,
                    "size": m.size,
                    "family": m.family,
                    "parameter_size": m.parameter_size,
                    "quantization": m.quantization,
                    "capabilities": m.capabilities,
                    "is_chat": m.is_chat,
                    "is_vision": m.is_vision,
                    "is_embedding": m.is_embedding,
                    "context_length": m.context_length,
                    "embedding_length": m.embedding_length,
                }
                for m in models
            ],
            "loaded": self.loaded_models(),
            "selected": {
                "chat": self.select_chat_model(),
                "vision": self.select_vision_model(),
                "embed": self.select_embed_model(),
            },
        }
