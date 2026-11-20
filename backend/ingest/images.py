"""Image ingestion: Apple Vision OCR + Ollama vision description with keep_alive=0 unload."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PIL import Image, ImageOps

from backend.core.ollama import OllamaClient
from backend.ingest.ocr import ocr_image
from backend.services.briefing import clean_phrase

logger = logging.getLogger("omnivault.ingest.images")

ProgressFn = Optional[Callable[[str, float, str], None]]

GEMMA_VISION_MODEL = "gemma-4-26b-a4b-it"
CAPTION_PROMPT = (
    "Bu görseli kurumsal bilgi kasası için 1-2 düzgün Türkçe cümleyle, "
    "uydurma yapmadan nesnel olarak açıkla ve varsa üzerindeki metinleri aktar."
)


@dataclass
class ImageExtraction:
    chunks: List[Dict] = field(default_factory=list)
    ocr_text: str = ""
    description: str = ""
    width: int = 0
    height: int = 0
    thumbnail_path: str = ""
    analysis_path: str = ""
    vision_model: str = ""
    ocr_engine: str = ""


def _prepare_vision_jpeg(path: Path, max_side: int = 768) -> bytes:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        rgb = image.convert("RGB")
        rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=90, optimize=True)
        return buffer.getvalue()


def _save_thumbnail(path: Path, dest: Path, max_side: int = 480) -> tuple[int, int]:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        width, height = image.size
        rgb = image.convert("RGB")
        rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(dest, format="JPEG", quality=85, optimize=True)
        return width, height


def portrait_label(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    stem = " ".join(stem.split()) or "Görsel"
    return f"{stem} — Kurumsal Görsel Kaydı (Üzerinde metin bulunmayan fotoğraf/görsel)"


def _caption_usable(text: str) -> bool:
    cleaned = clean_phrase(text)
    lowered = cleaned.lower()
    if "ıslam olacak" in lowered or "islam olacak" in lowered:
        return False
    return len(cleaned) >= 20 and " " in cleaned


def _gemma_caption(jpeg: bytes, api_key: str) -> str:
    """Same generateContent endpoint the chat path uses for gemma-4-26b-a4b-it."""
    import base64
    import httpx

    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": CAPTION_PROMPT},
                    {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii")}},
                ],
            }
        ],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 256},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMMA_VISION_MODEL}:generateContent"
    try:
        response = httpx.post(url, params={"key": api_key}, json=body, timeout=45.0)
        if response.status_code >= 400:
            logger.warning("Gemma vision returned %s", response.status_code)
            return ""
        parts = ((response.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = " ".join(part.get("text", "") for part in parts if not part.get("thought")).strip()
        text = clean_phrase(text)
        return text if _caption_usable(text) else ""
    except Exception as exc:
        logger.warning("Gemma vision failed: %s", exc)
        return ""


def extract_image(
    path: Path,
    *,
    doc_id: str,
    filename: str,
    ollama: OllamaClient,
    vision_model: str,
    gemini_key: str = "",
    thumbnails_dir: Path,
    analysis_dir: Path,
    max_side: int = 768,
    progress: ProgressFn = None,
) -> ImageExtraction:
    def emit(stage: str, value: float, message: str) -> None:
        if progress:
            progress(stage, value, message)

    emit("ocr", 0.2, "Running on-device OCR")
    ocr = ocr_image(path)
    emit("vision", 0.45, f"Describing image with {vision_model or 'vision model'}")

    thumbnail_path = thumbnails_dir / f"{doc_id}.jpg"
    width, height = _save_thumbnail(path, thumbnail_path)

    description = ""
    used_model = ""
    jpeg = _prepare_vision_jpeg(path, max_side=max_side)
    if gemini_key:
        description = _gemma_caption(jpeg, gemini_key)
        if description:
            used_model = GEMMA_VISION_MODEL
    # Moondream is never prompted. A textless photo without a cloud caption
    # gets a stable filename summary instead of a guessed description.
    if not description and not (ocr.text or "").strip():
        description = portrait_label(filename)
        used_model = used_model or "filename"
    description = clean_phrase(description)
    banned = ("akış şeması", "akis semasi", "proje süreci", "proje sureci", "flowchart")
    if description and any(phrase in description.lower() for phrase in banned):
        if not any(phrase in (ocr.text or "").lower() for phrase in banned):
            description = ""
    parts = []
    if ocr.text.strip():
        parts.append(f"OCR Text:\n{ocr.text.strip()}")
    if description.strip():
        parts.append(f"Visual Description:\n{description.strip()}")
    if not parts:
        parts.append(f"Image file: {filename} ({width}x{height})")
    combined = "\n\n".join(parts)

    analysis_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = analysis_dir / f"{doc_id}.md"
    analysis_path.write_text(combined, encoding="utf-8")

    chunk = {
        "text": combined,
        "metadata": {
            "doc_id": doc_id,
            "source": filename,
            "modality": "image",
            "image_path": str(path),
            "chunk_index": 0,
            "ocr_engine": ocr.engine,
            "vision_model": used_model,
        },
    }
    # If OCR is long, also index OCR-only and description-only slices for sharper retrieval.
    chunks = [chunk]
    if ocr.text.strip() and description.strip() and len(combined) > 900:
        chunks.append(
            {
                "text": f"OCR Text:\n{ocr.text.strip()}",
                "metadata": {
                    "doc_id": doc_id,
                    "source": filename,
                    "modality": "image",
                    "image_path": str(path),
                    "chunk_index": 1,
                    "slice": "ocr",
                },
            }
        )
        chunks.append(
            {
                "text": f"Visual Description:\n{description.strip()}",
                "metadata": {
                    "doc_id": doc_id,
                    "source": filename,
                    "modality": "image",
                    "image_path": str(path),
                    "chunk_index": 2,
                    "slice": "description",
                },
            }
        )

    emit("vision", 0.7, "Image analysis complete")
    return ImageExtraction(
        chunks=chunks,
        ocr_text=ocr.text,
        description=description,
        width=width,
        height=height,
        thumbnail_path=str(thumbnail_path),
        analysis_path=str(analysis_path),
        vision_model=used_model,
        ocr_engine=ocr.engine,
    )
