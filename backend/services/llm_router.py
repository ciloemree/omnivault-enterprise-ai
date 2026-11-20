"""Final-answer router. Embeddings, OCR, and Whisper stay local.

Providers:
- local: Ollama chat
- google: Gemini / Gemma generateContent
- groq: OpenAI-compatible https://api.groq.com/openai/v1/chat/completions
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("omnivault.llm")

from backend.config import ROOT_DIR, Settings
from backend.core.ollama import OllamaClient
from backend.core.store import Store

ENGINES = (
    {
        "id": "local",
        "provider": "local",
        "model": "",
        "label_en": "Local • Qwen 2.5 (3B) — 100% offline",
        "label_tr": "Yerel • Qwen 2.5 (3B) — %100 Çevrimdışı",
    },
    {
        "id": "groq-llama",
        "provider": "groq",
        "model": "llama-3.1-70b-versatile",
        "label_en": "Groq • Llama 3 (70B) — ultra fast / open weights",
        "label_tr": "Groq • Llama 3 (70B) — Ultra Hızlı / Açık Ağırlık",
    },
    {
        "id": "gemma-4-26b",
        "provider": "google",
        "model": "gemma-4-26b-a4b-it",
        "label_en": "Google • Gemma 4 (26B) — high quota",
        "label_tr": "Google • Gemma 4 (26B) — Yüksek Kota",
    },
    {
        "id": "gemini-flash",
        "provider": "google",
        "model": "gemini-3.8-flash",
        "label_en": "Google • Gemini 3.8 Flash — fast enterprise analysis",
        "label_tr": "Google • Gemini 3.8 Flash — Hızlı Kurumsal Analiz",
    },
)

_BY_ID = {item["id"]: item for item in ENGINES}


def engine_catalog() -> List[Dict[str, str]]:
    return [dict(item) for item in ENGINES]


_ENGINE_ALIASES = {"gemma-2-27b": "gemma-4-26b"}


def resolve_engine(engine_id: Optional[str]) -> Dict[str, str]:
    key = _ENGINE_ALIASES.get(engine_id or "local", engine_id or "local")
    return dict(_BY_ID.get(key) or _BY_ID["local"])


def _dotenv_value(name: str) -> str:
    path = Path(ROOT_DIR) / ".env"
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _prefer_google_key(candidates: List[str]) -> str:
    cleaned = [item.strip() for item in candidates if item and item.strip()]
    studio = [item for item in cleaned if item.startswith("AIzaSy")]
    return studio[0] if studio else (cleaned[0] if cleaned else "")


def google_api_key(store: Store, settings: Settings) -> str:
    stored = str(store.get_settings().get("gemini_api_key") or "").strip()
    return _prefer_google_key(
        [
            stored,
            os.environ.get("GEMINI_API_KEY") or "",
            os.environ.get("GOOGLE_API_KEY") or "",
            _dotenv_value("GEMINI_API_KEY"),
            _dotenv_value("GOOGLE_API_KEY"),
            settings.gemini_api_key or "",
        ]
    )


def groq_api_key(store: Store, settings: Settings) -> str:
    stored = str(store.get_settings().get("groq_api_key") or "").strip()
    if stored:
        return stored
    return (
        os.environ.get("GROQ_API_KEY")
        or _dotenv_value("GROQ_API_KEY")
        or settings.groq_api_key
        or ""
    ).strip()


def _provider_error(provider: str, response: httpx.Response) -> str:
    body = response.text[:2000]
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = error.get("message") or error
            body = str(message)
        elif isinstance(payload, dict) and payload.get("message"):
            body = str(payload.get("message"))
        else:
            body = str(payload)[:2000]
    except Exception:
        pass
    detail = f"{provider} API {response.status_code}: {body}"
    logger.error(detail)
    return detail


def _supports_generate(model: Dict[str, Any]) -> bool:
    methods = model.get("supportedGenerationMethods") or model.get("supported_generation_methods") or []
    return not methods or "generateContent" in methods


def discover_google_models(api_key: str) -> List[str]:
    if not api_key:
        return []
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key, "pageSize": 200},
        )
    if response.status_code >= 400:
        raise RuntimeError(_provider_error("Google", response))
    names = []
    for model in response.json().get("models") or []:
        if not _supports_generate(model):
            continue
        name = str(model.get("name") or "")
        names.append(name.split("/", 1)[-1])
    return [name for name in names if name]


def discover_groq_models(api_key: str, base_url: str) -> List[str]:
    if not api_key:
        return []
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if response.status_code >= 400:
        raise RuntimeError(_provider_error("Groq", response))
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return [str(row.get("id")) for row in rows or [] if row.get("id")]


def _pick(models: List[str], preferences: List[str]) -> str:
    exact = {model.lower(): model for model in models}
    for preference in preferences:
        if preference.lower() in exact:
            return exact[preference.lower()]
    for preference in preferences:
        token = preference.lower()
        for model in models:
            name = model.lower()
            if token in name and not any(skip in name for skip in ("tts", "image", "lite", "preview")):
                return model
    return ""


def resolve_live_model(engine_id: str, google_models: List[str], groq_models: List[str]) -> str:
    engine = resolve_engine(engine_id)
    if engine["id"] == "gemini-flash":
        return (
            _pick(
                google_models,
                ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"],
            )
            or engine["model"]
        )
    if engine["provider"] == "google":
        return (
            _pick(
                google_models,
                ["gemma-4-26b", "gemma-4-26b-it", "gemma-4-26b-a4b-it", "gemma-4-31b-it"],
            )
            or engine["model"]
        )
    if engine["provider"] == "groq":
        return (
            _pick(groq_models, ["llama-3.1-70b-versatile"])
            or "llama-3.1-70b-versatile"
        )
    return engine["model"]


class LLMRouter:
    def __init__(self, settings: Settings, store: Store, ollama: OllamaClient) -> None:
        self.settings = settings
        self.store = store
        self.ollama = ollama
        self._google_models: List[str] = []
        self._groq_models: List[str] = []
        self._discovered_at = 0.0

    def refresh_models(self, force: bool = False) -> None:
        if not force and time.time() - self._discovered_at < 300 and (self._google_models or self._groq_models):
            return
        self._discovered_at = time.time()
        google_key = google_api_key(self.store, self.settings)
        groq_key = groq_api_key(self.store, self.settings)
        if google_key:
            try:
                self._google_models = discover_google_models(google_key)
                logger.info("Google models available: %s", ", ".join(self._google_models[:12]))
            except Exception as exc:
                logger.warning("Google model discovery failed: %s", exc)
        if groq_key:
            try:
                self._groq_models = discover_groq_models(groq_key, self.settings.groq_base_url)
                logger.info("Groq models available: %s", ", ".join(self._groq_models[:12]))
            except Exception as exc:
                logger.warning("Groq model discovery failed: %s", exc)

    def catalog(self) -> List[Dict[str, str]]:
        self.refresh_models()
        rows = []
        for item in ENGINES:
            row = dict(item)
            live = resolve_live_model(item["id"], self._google_models, self._groq_models)
            if live:
                row["model"] = live
            rows.append(row)
        return rows

    def complete(
        self,
        engine_id: str,
        messages: List[Dict[str, str]],
        *,
        local_model: str,
        temperature: float,
        max_tokens: int,
        language: str = "en",
    ) -> Dict[str, str]:
        messages = apply_output_language(messages, language)
        engine = resolve_engine(engine_id)
        provider = engine["provider"]
        if provider == "local":
            model = local_model
            if not model:
                raise RuntimeError("No local chat model is available in Ollama. Pull one (e.g. qwen2.5:3b) and retry.")
            self.ollama.ensure_slot([model], unload_others=True)
            response = self.ollama.chat(
                model,
                messages,
                keep_alive=self.settings.chat_keep_alive,
                temperature=0.0,
                top_p=0.9,
                repeat_penalty=1.2,
                num_ctx=self.settings.chat_num_ctx,
                num_predict=512,
            )
            text = ((response.get("message") or {}).get("content") or "").strip()
            return {"text": text, "model": model, "provider": "local", "engine": "local"}

        self.refresh_models()
        live_model = resolve_live_model(engine["id"], self._google_models, self._groq_models)
        if provider == "google":
            key = google_api_key(self.store, self.settings)
            if not key:
                raise RuntimeError("Google API key is missing. Add GEMINI_API_KEY in Settings or .env.")
            text = _google_generate(
                model=live_model,
                api_key=key,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return {"text": text, "model": live_model, "provider": "google", "engine": engine["id"]}

        key = groq_api_key(self.store, self.settings)
        if not key:
            raise RuntimeError("Groq API key is missing. Add GROQ_API_KEY in Settings or .env.")
        text = _openai_chat(
            api_key=key,
            model=live_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            base_url=self.settings.groq_base_url,
            provider_name="Groq",
        )
        return {"text": text, "model": live_model, "provider": "groq", "engine": engine["id"]}


def apply_output_language(messages: List[Dict[str, str]], language: str) -> List[Dict[str, str]]:
    target = "Turkish" if str(language).lower().startswith("tr") else "English"
    sentence = (
        f"You must generate your final synthesized answer STRICTLY and FLUENTLY in {target}. "
        "Never output internal backend instructions."
    )
    updated: List[Dict[str, str]] = []
    placed = False
    for item in messages:
        if item.get("role") == "system" and not placed:
            updated.append({"role": "system", "content": f"{item.get('content') or ''}\n{sentence}".strip()})
            placed = True
        else:
            updated.append(item)
    if not placed:
        updated.insert(0, {"role": "system", "content": sentence})
    return updated


def _google_generate(
    *,
    model: str,
    api_key: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
    system = "\n\n".join(item["content"] for item in messages if item.get("role") == "system")
    turns = [item for item in messages if item.get("role") != "system"]
    contents: List[Dict[str, Any]] = []
    for item in turns:
        role = "model" if item.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": item.get("content") or ""}]})
    if not contents:
        contents = [{"role": "user", "parts": [{"text": "Answer using the sources."}]}]
    body: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if system:
        instruction: Dict[str, Any] = {"parts": [{"text": system}]}
        if model.startswith("gemma"):
            instruction = {"role": "system", "parts": [{"text": system}]}
        body["systemInstruction"] = instruction
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    with httpx.Client(timeout=120.0) as client:
        response = client.post(url, params={"key": api_key}, json=body)
        if response.status_code == 400 and system and "system" in response.text.lower():
            # Gemma builds that reject systemInstruction get a separate instruction turn,
            # never the instructions pasted onto the question and sources.
            contents = [
                {"role": "user", "parts": [{"text": system}]},
                {
                    "role": "model",
                    "parts": [{"text": "Understood. I will follow those instructions and will not repeat them."}],
                },
                *contents,
            ]
            response = client.post(
                url,
                params={"key": api_key},
                json={
                    "contents": contents,
                    "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
                },
            )
    if response.status_code >= 400:
        raise RuntimeError(_provider_error("Google", response))
    payload = response.json()
    parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    visible = [part.get("text") or "" for part in parts if not part.get("thought")]
    return _strip_reasoning("".join(visible).strip())


_REASONING = re.compile(
    r"^(role|task|constraints?|input|option|let'?s|let us|direct facts|the user|i need|i should|final answer)\b",
    re.IGNORECASE,
)


def _strip_reasoning(text: str) -> str:
    lines = text.splitlines()
    has_turkish = bool(re.search(r"[çğıöşüÇĞİÖŞÜ]", text))
    while lines:
        line = lines[0].strip()
        if not line or _REASONING.match(line):
            lines.pop(0)
            continue
        if has_turkish and not re.search(r"[çğıöşüÇĞİÖŞÜ]", line):
            lines.pop(0)
            continue
        break
    cleaned = "\n".join(lines).strip()
    if cleaned:
        return cleaned
    turkish = [line.strip() for line in text.splitlines() if re.search(r"[çğıöşüÇĞİÖŞÜ]", line)]
    if turkish:
        return turkish[-1]
    return text.strip()


def _openai_chat(
    *,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    base_url: str,
    provider_name: str,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
    if response.status_code >= 400:
        raise RuntimeError(_provider_error(provider_name, response))
    payload = response.json()
    return ((((payload.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
