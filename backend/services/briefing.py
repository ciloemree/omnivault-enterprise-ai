"""Auto-generated executive briefings (3 bullets + tags + entities) after each successful index."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.core.ollama import OllamaClient
from backend.core.privacy import PrivacyShield

logger = logging.getLogger("omnivault.briefing")

_SCHEMA_KEYS = {
    "text",
    "entity",
    "type",
    "summary",
    "tags",
    "entities",
    "name",
    "description",
    "imagename",
    "descriptiontype",
    "department",
    "age",
    "salary",
    "person",
    "organization",
    "date",
}
_JSON_KEY = re.compile(
    r'"?(?:text|entity|type|summary|tags|entities|name|description)"?\s*:\s*',
    re.IGNORECASE,
)


def clean_phrase(value: str) -> str:
    """Turn model JSON fragments into a single readable phrase."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text[0] in "{[":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            parts = []
            for key in ("text", "summary", "description", "entity", "name"):
                item = parsed.get(key)
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, list):
                    parts.extend(str(piece) for piece in item)
            text = " ".join(parts)
        elif isinstance(parsed, list):
            text = " ".join(str(piece) for piece in parsed)
    quoted = re.findall(r'"([^"]*)"', text)
    if quoted and re.search(r'"\s*:', text):
        kept = [item.strip() for item in quoted if item.strip() and item.strip().lower() not in _SCHEMA_KEYS]
        text = " ".join(kept)
    text = _JSON_KEY.sub(" ", text)
    text = re.sub(r"\b(?:ImageName|DescriptionType)\b", " ", text, flags=re.IGNORECASE)
    text = text.replace("{", " ").replace("}", " ").replace("[", " ").replace("]", " ").replace('"', "")
    text = re.sub(r"\s+", " ", text).strip(" ,:;-")
    return text


def sanitize_briefing(briefing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = dict(briefing or {})

    def unique(items: Any, limit: int) -> List[str]:
        cleaned: List[str] = []
        seen = set()
        for item in items or []:
            phrase = clean_phrase(str(item))
            key = phrase.lower()
            if not phrase or key in seen or key in _SCHEMA_KEYS:
                continue
            seen.add(key)
            cleaned.append(phrase)
            if len(cleaned) >= limit:
                break
        return cleaned

    source["summary"] = unique(source.get("summary"), 3)
    source["tags"] = unique(source.get("tags"), 10)
    source["entities"] = unique(source.get("entities"), 12)
    source["risk_flags"] = unique(source.get("risk_flags"), 8)
    return source


def briefing_from_vision(*, description: str, ocr_text: str, filename: str) -> Dict[str, Any]:
    """Plain Turkish caption. No local chat model and no JSON summary."""
    caption = clean_phrase(description)
    ocr = clean_phrase(ocr_text)
    text = caption or ocr
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    summary = sentences[:2] or ([text[:400]] if text else [f"{filename} görseli kasaya alındı."])
    return sanitize_briefing(
        {
            "summary": summary,
            "tags": ["görsel"],
            "entities": [],
            "risk_flags": [],
            "source": "vision",
            "lang": "tr",
        }
    )

BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "tags", "entities"],
}


def _cap(word: str) -> str:
    word = (word or "").strip()
    if not word:
        return ""
    return word[0].upper() + word[1:]


def _money_tag(text: str) -> str:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(bin)?\s*(?:tl|₺)", text, re.IGNORECASE)
    if not match:
        return ""
    number = float(match.group(1).replace(",", "."))
    if match.group(2):
        number *= 1000
    formatted = f"{int(round(number)):,}".replace(",", ".")
    return f"{formatted} TL"


def _note_body(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") or stripped.lower().startswith("department:"):
            continue
        lines.append(line)
    body = re.sub(r"\s+", " ", "\n".join(lines)).strip()
    return body or re.sub(r"\s+", " ", text or "").strip()


def factual_briefing(text: str, filename: str, modality: str, lang: str = "tr") -> Dict[str, Any]:
    """Pull names, ages, roles, and amounts out of a short note. No chat model."""
    raw = _note_body(text)
    first = re.search(r"ad[ıi]m\s*[:\-]?\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)", raw, re.IGNORECASE)
    last = re.search(r"soyad[ıi]m\s*[:\-]?\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)", raw, re.IGNORECASE)
    name = " ".join(_cap(match.group(1)) for match in (first, last) if match)
    age = re.search(r"(\d{1,3})\s*yaş", raw, re.IGNORECASE)
    money = _money_tag(raw)
    role = "İnsan Kaynakları" if re.search(r"insan\s+kaynaklar", raw, re.IGNORECASE) else ""
    bullets: List[str] = []
    if name and age and role:
        bullets.append(f"{name}, {age.group(1)} yaşında, insan kaynakları personeli.")
    elif name:
        bullets.append(f"{name}.")
    if age and not any(age.group(1) in item for item in bullets):
        bullets.append(f"{age.group(1)} yaşında.")
    if role and not any("insan kaynakları" in item.lower() for item in bullets):
        bullets.append("İnsan kaynakları personeli.")
    if money:
        bullets.append(f"{money} maaş alıyor.")
    if len(bullets) < 3 and not (name and (age or role or money)):
        for part in re.split(r"(?<=[.!?])\s+|[,;]", raw):
            piece = part.strip(" .")
            if not piece or re.search(r"ad[ıi]m|soyad[ıi]m", piece, re.IGNORECASE):
                continue
            sentence = piece[0].upper() + piece[1:]
            if sentence[-1] not in ".!?":
                sentence += "."
            if sentence not in bullets:
                bullets.append(sentence)
            if len(bullets) >= 3:
                break
    if not bullets:
        bullets = [raw[:400] or filename]
    tags = [item for item in (name, f"{age.group(1)} Yaş" if age else "", role, money) if item]
    return sanitize_briefing(
        {
            "summary": bullets[:3],
            "tags": tags or [modality],
            "entities": [name] if name else [],
            "risk_flags": [],
            "source": "extract",
            "lang": "tr" if str(lang or "").lower().startswith("tr") else lang or "tr",
        }
    )


def _fallback_briefing(text: str, filename: str, modality: str) -> Dict[str, Any]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    summary = sentences[:3] or [f"Indexed {modality} asset: {filename}"]
    while len(summary) < 3:
        summary.append("Additional detail retained in the vector memory for retrieval.")
    words = re.findall(r"[A-Z][A-Za-z0-9_\-]{2,}", text)
    entities = []
    for word in words:
        if word.lower() in {"the", "this", "that", "and", "for"}:
            continue
        if word not in entities:
            entities.append(word)
        if len(entities) >= 8:
            break
    tags = [modality, "auto-brief"]
    for token in ("RTO", "RPO", "GDPR", "KVKK", "security", "payment", "architecture", "policy"):
        if re.search(rf"\b{token}\b", text, re.IGNORECASE) and token.lower() not in {t.lower() for t in tags}:
            tags.append(token)
    return {
        "summary": summary[:3],
        "tags": tags[:10],
        "entities": entities[:10],
        "risk_flags": [],
        "source": "heuristic",
    }


def _normalize_lang(lang: str) -> str:
    return "tr" if str(lang or "").lower().startswith("tr") else "en"


def generate_briefing(
    *,
    ollama: OllamaClient,
    chat_model: str,
    filename: str,
    modality: str,
    text: str,
    num_ctx: int = 4096,
    keep_alive: str = "5m",
    privacy_shield: bool = False,
    lang: str = "en",
) -> Dict[str, Any]:
    language = _normalize_lang(lang)
    excerpt = (text or "").strip()
    if len(excerpt) > 6000:
        excerpt = excerpt[:6000] + "\n…"
    if not excerpt:
        return {
            "summary": [f"Empty {modality} asset: {filename}", "No extractable text was found.", "File retained for manual review."],
            "tags": [modality],
            "entities": [],
            "risk_flags": ["empty-content"],
            "source": "empty",
            "lang": language,
        }

    shield = PrivacyShield(enabled=privacy_shield)
    safe_excerpt = shield.redact(excerpt)
    if len(_note_body(excerpt)) < 400:
        return factual_briefing(excerpt, filename, modality, language)
    if not chat_model:
        briefing = _fallback_briefing(excerpt, filename, modality)
        return briefing

    language_rule = (
        "Write summary bullets, tags, entities, and risk_flags entirely in Turkish."
        if language == "tr"
        else "Write summary bullets, tags, entities, and risk_flags entirely in English."
    )
    prompt = (
        f"You are OmniVault's executive briefing agent for a privacy-first enterprise knowledge base.\n"
        f"Asset: {filename} (modality={modality})\n"
        f"Write exactly 3 concise executive bullets, 3-8 topical tags, and key named entities.\n"
        f"Flag compliance risks (KVKK/GDPR, secrets, PII) in risk_flags when present.\n"
        f"Use only the source text. Reply with JSON matching the schema.\n"
        f"Each summary item, tag, and entity must be plain text. Never put JSON, keys, or quotes inside a value.\n"
        f"{language_rule}\n\n"
        f"SOURCE:\n{safe_excerpt}"
    )
    try:
        ollama.ensure_slot([chat_model], unload_others=True)
        response = ollama.chat(
            chat_model,
            [{"role": "user", "content": prompt}],
            keep_alive=keep_alive,
            temperature=0.1,
            num_ctx=min(num_ctx, 4096),
            num_predict=400,
            format=BRIEFING_SCHEMA,
        )
        content = (response.get("message") or {}).get("content") or "{}"
        content = shield.restore(content)
        data = json.loads(content)
        summary = [clean_phrase(str(item)) for item in (data.get("summary") or []) if clean_phrase(str(item))][:3]
        tags = [clean_phrase(str(item)) for item in (data.get("tags") or []) if clean_phrase(str(item))][:10]
        entities = [clean_phrase(str(item)) for item in (data.get("entities") or []) if clean_phrase(str(item))][:12]
        risk_flags = [clean_phrase(str(item)) for item in (data.get("risk_flags") or []) if clean_phrase(str(item))][:8]
        return sanitize_briefing(
            {
                "summary": summary,
                "tags": tags or [modality],
                "entities": entities,
                "risk_flags": risk_flags,
                "source": "llm",
                "model": chat_model,
                "lang": language,
            }
        )
    except Exception as exc:
        logger.warning("Briefing LLM failed (%s); using heuristic fallback", exc)
        fallback = _fallback_briefing(excerpt, filename, modality)
        fallback["lang"] = language
        return sanitize_briefing(fallback)
