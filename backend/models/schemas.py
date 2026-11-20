"""Pydantic request/response models shared by the FastAPI surface."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Modality = Literal["pdf", "audio", "image", "text"]
DocStatus = Literal[
    "queued",
    "processing",
    "indexing",
    "briefing",
    "ready",
    "error",
    "duplicate",
]


class HealthResponse(BaseModel):
    status: str
    version: str
    ollama: Dict[str, Any]
    whisper: Dict[str, Any]
    vector_count: int
    document_count: int
    privacy_shield: bool
    ram_gb: float
    platform: str


class ModelSelection(BaseModel):
    chat_model: Optional[str] = None
    vision_model: Optional[str] = None
    embed_model: Optional[str] = None
    privacy_shield: Optional[bool] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    strict_guard: Optional[bool] = None
    gemini_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None


class RuntimeSettings(BaseModel):
    chat_model: str
    vision_model: str
    embed_model: str
    privacy_shield: bool
    top_k: int
    strict_guard: bool
    chat_keep_alive: str
    embed_keep_alive: str
    chat_num_ctx: int
    ollama_url: str
    available: Dict[str, List[Dict[str, Any]]]
    gemini_key_set: bool = False
    groq_key_set: bool = False
    engines: List[Dict[str, str]] = Field(default_factory=list)


class DocumentOut(BaseModel):
    id: str
    filename: str
    modality: str
    file_type: str
    mime: Optional[str] = None
    size_bytes: int = 0
    sha256: str
    origin: str = "upload"
    status: str
    stage: Optional[str] = None
    progress: float = 0
    message: Optional[str] = None
    error: Optional[str] = None
    chunk_count: int = 0
    embed_model: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    briefing: Optional[Dict[str, Any]] = None
    privacy: Optional[Dict[str, Any]] = None
    created_at: float
    updated_at: float
    indexed_at: Optional[float] = None


class Citation(BaseModel):
    id: str
    index: int
    modality: str
    doc_id: str
    filename: str
    snippet: str
    relevance: float
    distance: float
    page: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    image_path: Optional[str] = None
    thumbnail_url: Optional[str] = None
    media_url: Optional[str] = None
    preview_url: Optional[str] = None
    department: Optional[str] = None
    uploader: Optional[str] = None
    created_at: Optional[float] = None


class ChatTurn(BaseModel):
    question: str = ""
    answer: str = ""


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    modalities: Optional[List[Modality]] = None
    doc_ids: Optional[List[str]] = None
    chat_model: Optional[str] = None
    lang: Literal["en", "tr"] = "en"
    language: Optional[Literal["en", "tr"]] = None
    engine: Optional[str] = None
    conversation_id: Optional[str] = None
    history: Optional[List[ChatTurn]] = None


class PendingUpdate(BaseModel):
    doc_id: str
    filename: str
    department: str = ""
    old_snippet: str
    new_snippet: str
    new_text: str
    kind: str = ""
    subject: str = ""


class QueryResponse(BaseModel):
    answer: str
    citations: List[Citation]
    grounding_score: float
    grounding_label: str
    modalities_used: List[str]
    model: str
    retrieved: int
    privacy_redactions: Dict[str, int] = Field(default_factory=dict)
    latency_ms: float
    engine: str = "local"
    provider: str = "local"
    engine_label: str = ""
    conversation_id: str = ""
    pending_update: Optional[PendingUpdate] = None


class SeedResponse(BaseModel):
    created: List[DocumentOut]
    reused: List[DocumentOut]
    message: str


class AuditEntry(BaseModel):
    id: int
    ts: float
    action: str
    detail: Dict[str, Any] = Field(default_factory=dict)


class PdfPageResponse(BaseModel):
    doc_id: str
    page: int
    page_count: int
    width: int
    height: int
    image_url: str
    text: str
