"""On-device OCR via Apple Vision (primary) with a Tesseract-free graceful fallback."""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger("omnivault.ocr")


@dataclass
class OcrLine:
    text: str
    confidence: float
    y: float
    x: float


@dataclass
class OcrResult:
    text: str
    lines: List[OcrLine]
    engine: str


def _apple_vision_available() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        import Vision  # noqa: F401
        from Foundation import NSURL  # noqa: F401

        return True
    except Exception:
        return False


def ocr_image(path: Union[str, Path]) -> OcrResult:
    path = Path(path)
    if not path.exists():
        return OcrResult(text="", lines=[], engine="none")
    if _apple_vision_available():
        try:
            return _ocr_apple_vision(path)
        except Exception as exc:
            logger.warning("Apple Vision OCR failed on %s: %s", path.name, exc)
    return _ocr_pillow_stub(path)


def _ocr_apple_vision(path: Path) -> OcrResult:
    import objc
    import Vision
    from Foundation import NSDictionary, NSURL

    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(str(path.resolve()))
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, NSDictionary.dictionary())
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        try:
            request.setRecognitionLanguages_(["en-US", "tr-TR"])
        except Exception:
            pass
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"VNRecognizeTextRequest failed: {error}")

        lines: List[OcrLine] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            text = str(candidate.string() or "").strip()
            if not text:
                continue
            box = observation.boundingBox()
            # Vision uses a bottom-left origin; convert to top-left for reading order.
            y = 1.0 - float(box.origin.y) - float(box.size.height)
            lines.append(
                OcrLine(
                    text=text,
                    confidence=float(candidate.confidence()),
                    y=y,
                    x=float(box.origin.x),
                )
            )

    lines.sort(key=lambda line: (round(line.y, 2), line.x))
    text = "\n".join(line.text for line in lines)
    return OcrResult(text=text, lines=lines, engine="apple-vision")


def _ocr_pillow_stub(path: Path) -> OcrResult:
    """Last-resort fallback: no OCR engine available — return empty text rather than crashing."""
    logger.info("No OCR engine available for %s; continuing with vision-model description only", path.name)
    return OcrResult(text="", lines=[], engine="none")


def ocr_pixmap_png(png_bytes: bytes, temp_dir: Optional[Path] = None) -> OcrResult:
    import tempfile

    directory = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "_omnivault_ocr_page.png"
    target.write_bytes(png_bytes)
    try:
        return ocr_image(target)
    finally:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
