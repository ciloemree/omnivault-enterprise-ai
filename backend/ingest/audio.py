"""Audio transcription: MLX on Apple Silicon, Groq only in hybrid mode, faster-whisper fallback."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.core.chunking import estimate_tokens, merge_segments, split_sentences
from backend.ingest.audio_io import decode_audio

logger = logging.getLogger("omnivault.ingest.audio")

ProgressFn = Optional[Callable[[str, float, str], None]]
_GROQ_MAX_BYTES = 24 * 1024 * 1024


def cloud_transcription_allowed(engine: Optional[str]) -> bool:
    """Groq audio runs only when the selected answer engine is not 100% offline."""
    return (engine or "local").strip().lower() not in {"", "local"}


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
    engine: str = ""


class WhisperEngine:
    def __init__(
        self,
        model_size: str,
        download_root: Path,
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 1,
        mlx_model: str = "mlx-community/whisper-large-v3-turbo",
        groq_model: str = "whisper-large-v3-turbo",
        settings: Any = None,
        store: Any = None,
    ) -> None:
        self.model_size = model_size
        self.download_root = download_root
        self.device = device
        self.compute_type = compute_type
        self.beam_size = max(1, int(beam_size or 1))
        self.mlx_model = mlx_model or "mlx-community/whisper-large-v3-turbo"
        self.groq_model = groq_model or "whisper-large-v3-turbo"
        self.settings = settings
        self.store = store
        self._model = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None
        self._mlx_checked = False
        self._mlx_ok = False

    def status(self) -> Dict:
        mlx = self._mlx_ready()
        return {
            "model": self.mlx_model if mlx else self.model_size,
            "device": "mlx" if mlx else self.device,
            "compute_type": self.compute_type,
            "loaded": self._model is not None or mlx,
            "error": self._load_error,
            "beam_size": self.beam_size,
            "mlx": mlx,
            "groq_model": self.groq_model,
        }

    def _mlx_ready(self) -> bool:
        if self._mlx_checked:
            return self._mlx_ok
        try:
            import mlx_whisper  # noqa: F401

            self._mlx_ok = True
        except Exception:
            self._mlx_ok = False
        self._mlx_checked = True
        return self._mlx_ok

    def _groq_key(self) -> str:
        if self.store is not None and self.settings is not None:
            from backend.services.llm_router import groq_api_key

            return groq_api_key(self.store, self.settings)
        if self.settings is not None and str(getattr(self.settings, "groq_api_key", "") or "").strip():
            return str(self.settings.groq_api_key).strip()
        return (os.environ.get("GROQ_API_KEY") or "").strip()

    def _load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel

            self.download_root.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Loading faster-whisper model=%s device=%s compute_type=%s beam_size=%s",
                self.model_size,
                self.device,
                self.compute_type,
                self.beam_size,
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
        allow_cloud: bool = False,
    ) -> AudioExtraction:
        spoken = language if language in {"tr", "en"} else None

        def emit(stage: str, value: float, message: str) -> None:
            if progress:
                progress(stage, value, message)

        if allow_cloud:
            key = self._groq_key()
            if key and path.stat().st_size <= _GROQ_MAX_BYTES:
                started = time.perf_counter()
                try:
                    emit("transcribe", 0.3, "Transcribing with Groq Whisper")
                    raw_segments, detected, duration = self._transcribe_groq(path, spoken, key)
                    elapsed = time.perf_counter() - started
                    logger.info("Transcription engine=%s file=%s elapsed=%.2fs", "groq-whisper", filename, elapsed)
                    return self._pack(raw_segments, detected or spoken or "", duration, doc_id, filename, transcript_dir, "groq-whisper")
                except Exception as exc:
                    logger.warning("groq-whisper failed for %s (%s); using a local engine", filename, exc)
            elif not key:
                logger.info("Hybrid audio for %s stayed local because GROQ_API_KEY is not set", filename)
            else:
                logger.info("Hybrid audio for %s stayed local because the file is over 24 MB", filename)
        elif self._groq_key():
            logger.info("Offline mode kept %s on localhost", filename)

        if self._mlx_ready():
            started = time.perf_counter()
            try:
                emit("transcribe", 0.35, f"Transcribing with MLX Whisper {self.mlx_model}")
                raw_segments, detected, duration = self._transcribe_mlx(path, spoken)
                if not raw_segments:
                    raise RuntimeError("mlx-whisper returned no speech")
                elapsed = time.perf_counter() - started
                logger.info("Transcription engine=%s file=%s elapsed=%.2fs", "mlx-whisper", filename, elapsed)
                return self._pack(raw_segments, detected or spoken or "", duration, doc_id, filename, transcript_dir, "mlx-whisper")
            except Exception as exc:
                logger.warning("mlx-whisper failed for %s (%s); falling back to faster-whisper", filename, exc)

        started = time.perf_counter()
        emit("transcribe", 0.2, "Decoding audio")
        samples, duration = decode_audio(path)
        emit("transcribe", 0.35, f"Transcribing {duration:.1f}s of audio with faster-whisper {self.model_size}")
        raw_segments, detected, heard = self._transcribe_faster(samples, spoken, duration, emit)
        elapsed = time.perf_counter() - started
        logger.info("Transcription engine=%s file=%s elapsed=%.2fs", "faster-whisper", filename, elapsed)
        return self._pack(raw_segments, detected, heard or duration, doc_id, filename, transcript_dir, "faster-whisper")

    def _transcribe_groq(self, path: Path, language: Optional[str], key: str) -> tuple[List[Dict], str, float]:
        import httpx

        base = "https://api.groq.com/openai/v1"
        if self.settings is not None and str(getattr(self.settings, "groq_base_url", "") or "").strip():
            base = str(self.settings.groq_base_url).rstrip("/")
        data = {
            "model": self.groq_model,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if language:
            data["language"] = language
        with path.open("rb") as handle:
            response = httpx.post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data=data,
                files={"file": (path.name, handle, "application/octet-stream")},
                timeout=httpx.Timeout(180.0, connect=10.0),
            )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}")
        payload = response.json()
        segments = []
        for item in payload.get("segments") or []:
            text = str(item.get("text") or "").strip()
            if text:
                segments.append(
                    {"text": text, "start": float(item.get("start") or 0.0), "end": float(item.get("end") or 0.0)}
                )
        if not segments and str(payload.get("text") or "").strip():
            duration = float(payload.get("duration") or 0.0)
            segments.append({"text": str(payload["text"]).strip(), "start": 0.0, "end": duration})
        if not segments:
            raise RuntimeError("Groq returned no speech")
        duration = float(payload.get("duration") or segments[-1]["end"] or 0.0)
        return segments, str(payload.get("language") or language or ""), duration

    def _transcribe_mlx(self, path: Path, language: Optional[str]) -> tuple[List[Dict], str, float]:
        import mlx_whisper

        options: Dict[str, Any] = {
            "path_or_hf_repo": self.mlx_model,
            "verbose": False,
            "word_timestamps": False,
            "condition_on_previous_text": False,
            "temperature": 0.0,
            "task": "transcribe",
        }
        if language:
            options["language"] = language
        result = mlx_whisper.transcribe(str(path), **options)
        segments = []
        for item in result.get("segments") or []:
            text = str(item.get("text") or "").strip()
            if text:
                segments.append(
                    {"text": text, "start": float(item.get("start") or 0.0), "end": float(item.get("end") or 0.0)}
                )
        duration = float(segments[-1]["end"]) if segments else 0.0
        return segments, str(result.get("language") or language or ""), duration

    def _transcribe_faster(self, samples, language: Optional[str], duration: float, emit) -> tuple[List[Dict], str, float]:
        model = self._load()
        segments_iter, info = model.transcribe(
            samples,
            beam_size=self.beam_size,
            vad_filter=True,
            word_timestamps=False,
            task="transcribe",
            condition_on_previous_text=False,
            language=language,
        )
        raw_segments = []
        for segment in segments_iter:
            text = (segment.text or "").strip()
            if not text:
                continue
            raw_segments.append({"text": text, "start": float(segment.start), "end": float(segment.end)})
            if duration > 0:
                emit(
                    "transcribe",
                    0.35 + 0.35 * min(1.0, float(segment.end) / duration),
                    f"Transcribed through {segment.end:.1f}s",
                )
        detected = str(getattr(info, "language", "") or language or "")
        heard = float(getattr(info, "duration", duration) or duration)
        return raw_segments, detected, heard

    def _pack(
        self,
        raw_segments: List[Dict],
        language: str,
        duration: float,
        doc_id: str,
        filename: str,
        transcript_dir: Path,
        engine: str,
    ) -> AudioExtraction:
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
            "engine": engine,
            "language": language,
            "duration": float(duration or 0.0),
            "segments": raw_segments,
            "chunks": [
                {
                    "text": chunk["text"],
                    "start_time": chunk["metadata"]["start_time"],
                    "end_time": chunk["metadata"]["end_time"],
                }
                for chunk in chunks
            ],
        }
        transcript_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return AudioExtraction(
            chunks=chunks,
            language=language,
            duration=payload["duration"],
            transcript_path=str(transcript_path),
            full_text=" ".join(chunk["text"] for chunk in chunks),
            engine=engine,
        )
