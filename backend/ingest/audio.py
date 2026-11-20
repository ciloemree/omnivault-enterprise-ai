"""Local audio transcription via faster-whisper with Apple Silicon–friendly defaults."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from backend.core.chunking import estimate_tokens, merge_segments, split_sentences
from backend.ingest.audio_io import decode_audio

logger = logging.getLogger("omnivault.ingest.audio")

ProgressFn = Optional[Callable[[str, float, str], None]]


def _split_oversized_windows(segments: List[Dict], max_tokens: int = 160) -> List[Dict]:
    """Split long merged utterances into timestamp-proportional windows for precise seeks."""
    output: List[Dict] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        if estimate_tokens(text) <= max_tokens or end <= start:
            output.append({"text": text, "start": start, "end": end})
            continue
        sentences = split_sentences(text) or [text]
        total_chars = max(sum(len(sentence) for sentence in sentences), 1)
        cursor = start
        bucket: List[str] = []
        bucket_chars = 0
        bucket_tokens = 0

        def flush(force_end: Optional[float] = None) -> None:
            nonlocal cursor, bucket, bucket_chars, bucket_tokens
            if not bucket:
                return
            span = (end - start) * (bucket_chars / total_chars)
            piece_end = force_end if force_end is not None else min(end, cursor + max(span, 0.4))
            output.append({"text": " ".join(bucket).strip(), "start": cursor, "end": piece_end})
            cursor = piece_end
            bucket, bucket_chars, bucket_tokens = [], 0, 0

        for sentence in sentences:
            tokens = estimate_tokens(sentence)
            if bucket and bucket_tokens + tokens > max_tokens:
                flush()
            bucket.append(sentence)
            bucket_tokens += tokens
            bucket_chars += len(sentence)
        flush(force_end=end)
    return output


@dataclass
class AudioExtraction:
    chunks: List[Dict] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    transcript_path: str = ""
    full_text: str = ""


class WhisperEngine:
    def __init__(
        self,
        model_size: str,
        download_root: Path,
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
    ) -> None:
        self.model_size = model_size
        self.download_root = download_root
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self._model = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    def status(self) -> Dict:
        return {
            "model": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
            "loaded": self._model is not None,
            "error": self._load_error,
        }

    def _load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel

            self.download_root.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Loading faster-whisper model=%s device=%s compute_type=%s",
                self.model_size,
                self.device,
                self.compute_type,
            )
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=min(8, os.cpu_count() or 4),
                    download_root=str(self.download_root),
                )
                self._load_error = None
            except Exception as exc:
                self._load_error = str(exc)
                if self.device != "cpu":
                    logger.warning("Whisper %s failed (%s); falling back to cpu/int8", self.device, exc)
                    self.device = "cpu"
                    self.compute_type = "int8"
                    self._model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=min(8, os.cpu_count() or 4),
                        download_root=str(self.download_root),
                    )
                    self._load_error = None
                else:
                    raise
            return self._model

    def unload(self) -> None:
        with self._lock:
            self._model = None

    def transcribe(
        self,
        path: Path,
        *,
        doc_id: str,
        filename: str,
        transcript_dir: Path,
        progress: ProgressFn = None,
        language: Optional[str] = None,
    ) -> AudioExtraction:
        def emit(stage: str, value: float, message: str) -> None:
            if progress:
                progress(stage, value, message)

        emit("transcribe", 0.2, "Decoding audio")
        samples, duration = decode_audio(path)
        emit("transcribe", 0.35, f"Transcribing {duration:.1f}s of audio with Whisper {self.model_size}")
        model = self._load()
        segments_iter, info = model.transcribe(
            samples,
            beam_size=5,
            vad_filter=True,
            word_timestamps=False,
            task="transcribe",
            condition_on_previous_text=False,
            language=language if language in {"tr", "en"} else None,
        )
        raw_segments = []
        for segment in segments_iter:
            text = (segment.text or "").strip()
            if not text:
                continue
            raw_segments.append(
                {
                    "text": text,
                    "start": float(segment.start),
                    "end": float(segment.end),
                }
            )
            if duration > 0:
                emit(
                    "transcribe",
                    0.35 + 0.35 * min(1.0, float(segment.end) / duration),
                    f"Transcribed through {segment.end:.1f}s",
                )

        # Keep windows short so audio citations seek to a precise moment in the player.
        merged = merge_segments(raw_segments, max_tokens=160, max_gap=1.25)
        windows = _split_oversized_windows(merged, max_tokens=160)
        chunks = []
        for index, item in enumerate(windows):
            chunks.append(
                {
                    "text": item["text"],
                    "metadata": {
                        "doc_id": doc_id,
                        "source": filename,
                        "modality": "audio",
                        "start_time": float(item["start"]),
                        "end_time": float(item["end"]),
                        "chunk_index": index,
                    },
                }
            )

        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"{doc_id}.json"
        payload = {
            "language": getattr(info, "language", ""),
            "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
            "duration": float(getattr(info, "duration", duration) or duration),
            "segments": raw_segments,
            "chunks": [
                {
                    "text": c["text"],
                    "start_time": c["metadata"]["start_time"],
                    "end_time": c["metadata"]["end_time"],
                }
                for c in chunks
            ],
        }
        transcript_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        full_text = " ".join(c["text"] for c in chunks)
        return AudioExtraction(
            chunks=chunks,
            language=payload["language"],
            duration=payload["duration"],
            transcript_path=str(transcript_path),
            full_text=full_text,
        )
