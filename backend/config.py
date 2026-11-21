"""Static configuration, loaded from environment variables (prefix ``OMNIVAULT_``) or ``.env``."""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent

# Privacy-first defaults for third-party libraries; must be set before they are imported.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("CT2_VERBOSE", "-1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _total_ram_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().total / (1024**3)
    except Exception:
        return 8.0


def _default_num_ctx() -> int:
    ram = _total_ram_gb()
    if ram <= 8.5:
        return 6144
    if ram <= 16.5:
        return 8192
    return 12288


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMNIVAULT_",
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = ROOT_DIR / "data"
    frontend_dist: Path = ROOT_DIR / "frontend" / "dist"
    log_level: str = "info"

    ollama_url: str = "http://127.0.0.1:11434"
    allow_remote_ollama: bool = False
    ollama_timeout: float = 900.0

    chat_model: str = ""
    vision_model: str = ""
    embed_model: str = ""
    admin_user: str = "admin"
    admin_pass: str = "admin123"
    gemini_api_key: str = ""
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"

    chat_keep_alive: str = "5m"
    embed_keep_alive: str = "10m"
    chat_num_ctx: int = _default_num_ctx()
    chat_temperature: float = 0.15
    chat_max_tokens: int = 900

    whisper_model: str = "base"
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"
    whisper_beam_size: int = 1
    mlx_whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    groq_whisper_model: str = "whisper-large-v3-turbo"

    chunk_size: int = 1100
    chunk_overlap: int = 160
    top_k: int = 6
    max_upload_mb: int = 512
    strict_guard: bool = True
    privacy_shield: bool = False
    vision_max_side: int = 768
    scanned_page_ocr_limit: int = 30

    @field_validator("ollama_url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "omnivault.db"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def thumbnails_dir(self) -> Path:
        return self.data_dir / "thumbnails"

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def analysis_dir(self) -> Path:
        return self.data_dir / "analysis"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def demo_dir(self) -> Path:
        return self.data_dir / "demo"

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.chroma_dir,
            self.uploads_dir,
            self.thumbnails_dir,
            self.transcripts_dir,
            self.analysis_dir,
            self.models_dir,
            self.demo_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def ollama_endpoint_scope(self) -> str:
        """Classify the Ollama endpoint as ``loopback``, ``private`` or ``public``."""
        host = urlparse(self.ollama_url).hostname or ""
        if host in ("localhost", ""):
            return "loopback"
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return "loopback" if host.endswith(".local") or host.endswith(".localhost") else "public"
        if address.is_loopback:
            return "loopback"
        if address.is_private or address.is_link_local:
            return "private"
        return "public"

    def validate_privacy(self) -> Optional[str]:
        """Return an error message when the configuration would send data off-premise."""
        if self.ollama_endpoint_scope() == "public" and not self.allow_remote_ollama:
            return (
                f"Refusing to use the public Ollama endpoint {self.ollama_url}: OmniVault keeps all inference "
                "on-premise. Point OMNIVAULT_OLLAMA_URL to a local/private host or set "
                "OMNIVAULT_ALLOW_REMOTE_OLLAMA=true to override."
            )
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
