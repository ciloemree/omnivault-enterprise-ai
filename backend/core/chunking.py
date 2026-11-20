"""Token-aware text chunking with overlap, used by every modality before embedding."""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence

_WHITESPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def normalize_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def estimate_tokens(text: str) -> int:
    # Rough but stable: ~4 characters per token for English / mixed technical prose.
    return max(1, (len(text) + 3) // 4) if text else 0


def split_sentences(text: str) -> List[str]:
    parts = [part.strip() for part in _SENTENCE.split(text) if part and part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def chunk_text(
    text: str,
    *,
    chunk_size: int = 1100,
    chunk_overlap: int = 160,
    min_chunk_chars: int = 40,
) -> List[str]:
    """Split ``text`` into overlapping windows measured in approximate tokens."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if estimate_tokens(cleaned) <= chunk_size:
        return [cleaned]

    sentences = split_sentences(cleaned)
    chunks: List[str] = []
    current: List[str] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        body = " ".join(current).strip()
        if body:
            chunks.append(body)
        current = []
        current_tokens = 0

    for sentence in sentences:
        tokens = estimate_tokens(sentence)
        if tokens > chunk_size:
            flush()
            # Hard-split oversized sentences by words.
            words = sentence.split()
            window: List[str] = []
            window_tokens = 0
            for word in words:
                word_tokens = estimate_tokens(word)
                if window and window_tokens + word_tokens > chunk_size:
                    chunks.append(" ".join(window))
                    # Overlap by trailing words.
                    overlap_words: List[str] = []
                    overlap_tokens = 0
                    for previous in reversed(window):
                        overlap_tokens += estimate_tokens(previous)
                        overlap_words.insert(0, previous)
                        if overlap_tokens >= chunk_overlap:
                            break
                    window = overlap_words
                    window_tokens = sum(estimate_tokens(w) for w in window)
                window.append(word)
                window_tokens += word_tokens
            if window:
                chunks.append(" ".join(window))
            continue

        if current and current_tokens + tokens > chunk_size:
            flush()
            if chunk_overlap > 0 and chunks:
                # Seed next window with the tail of the previous chunk.
                tail_words = chunks[-1].split()
                seed: List[str] = []
                seed_tokens = 0
                for word in reversed(tail_words):
                    seed_tokens += estimate_tokens(word)
                    seed.insert(0, word)
                    if seed_tokens >= chunk_overlap:
                        break
                current = seed
                current_tokens = seed_tokens

        current.append(sentence)
        current_tokens += tokens

    flush()
    # Deduplicate accidental identical windows from overlap.
    deduped: List[str] = []
    seen = set()
    for chunk in chunks:
        key = chunk[:160]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped or [cleaned]


def merge_segments(
    segments: Sequence[dict],
    *,
    text_key: str = "text",
    max_tokens: int = 420,
    max_gap: float = 1.5,
) -> List[dict]:
    """Merge short sequential segments (e.g. Whisper utterances) into larger chunks."""
    if not segments:
        return []
    merged: List[dict] = []
    bucket: List[dict] = []
    bucket_tokens = 0

    def emit() -> None:
        nonlocal bucket, bucket_tokens
        if not bucket:
            return
        text = " ".join(str(item.get(text_key) or "").strip() for item in bucket).strip()
        payload = dict(bucket[0])
        payload[text_key] = text
        if "start" in bucket[0]:
            payload["start"] = bucket[0]["start"]
            payload["end"] = bucket[-1].get("end", bucket[0].get("end"))
        merged.append(payload)
        bucket = []
        bucket_tokens = 0

    for segment in segments:
        text = str(segment.get(text_key) or "").strip()
        if not text:
            continue
        tokens = estimate_tokens(text)
        gap = 0.0
        if bucket and "start" in segment and "end" in bucket[-1]:
            gap = float(segment["start"]) - float(bucket[-1]["end"])
        if bucket and (bucket_tokens + tokens > max_tokens or gap > max_gap):
            emit()
        bucket.append(segment)
        bucket_tokens += tokens
    emit()
    return merged
