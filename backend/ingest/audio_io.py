"""Audio decoding helpers that avoid the PyAV 19 / faster-whisper metadata_errors clash."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple, Union

import numpy as np


def decode_audio(path: Union[str, Path], sampling_rate: int = 16000) -> Tuple[np.ndarray, float]:
    """Decode any ffmpeg-supported audio/video container to mono float32 PCM at ``sampling_rate``.

    Returns ``(samples, duration_seconds)``.
    """
    import av

    path = Path(path)
    frames = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise RuntimeError(f"No audio stream found in {path.name}")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                frames.append(resampled.to_ndarray())
        for resampled in resampler.resample(None):
            frames.append(resampled.to_ndarray())

    if not frames:
        raise RuntimeError(f"Unable to decode audio from {path.name}")

    pcm = np.concatenate([frame.reshape(-1) for frame in frames]).astype(np.float32) / 32768.0
    duration = float(len(pcm) / sampling_rate)
    return pcm, duration


def write_wav_mono16(path: Union[str, Path], samples: np.ndarray, sample_rate: int = 16000) -> None:
    """Write a mono 16-bit PCM WAV without depending on ffmpeg being on PATH."""
    import wave

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
