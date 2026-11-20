"""OmniVault FastAPI application — local multimodal RAG control plane."""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import re
import shutil
import threading
import time
import uuid
from contextvars import ContextVar
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import psutil
from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend.config import Settings, get_settings
from backend.core.embeddings import EmbeddingService
from backend.core.events import EventBus
from backend.core.ollama import OllamaClient
from backend.core.store import Store
from backend.core.users import UserStore
from backend.core.vectorstore import VectorStore
from backend.ingest.audio import WhisperEngine
from backend import mcp_server
from backend.ingest.documents import render_pdf_page
from backend.models.schemas import (
    AuditEntry,
    DocumentOut,
    HealthResponse,
    ModelSelection,
    PdfPageResponse,
    PendingUpdate,
    QueryRequest,
    QueryResponse,
    RuntimeSettings,
    SeedResponse,
)
from backend.services.chat_update import parse_update_command, propose_update
from backend.core.access import (
    can_admin,
    can_mutate_document,
    can_see_department,
    can_write,
    normalize_department,
    normalize_role,
    effective_department,
    upload_department,
    visible_departments,
)
from backend.services.pipeline import AUDIO_TYPES, IMAGE_TYPES, OFFICE_TYPES, PDF_TYPES, TEXT_TYPES, IngestionPipeline
from backend.services.suggestions import questions_from_content, suggest_questions
from backend.services.llm_router import google_api_key, groq_api_key
from backend.services.rag import RAGService

logger = logging.getLogger("omnivault")
logging.getLogger("httpx").setLevel(logging.WARNING)
CURRENT_USER: ContextVar[Optional[Dict[str, Any]]] = ContextVar("omnivault_user", default=None)
PUBLIC_API = {"/api/health", "/api/auth/login", "/api/auth/demo"}
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


class AppState:
    settings: Settings
    store: Store
    events: EventBus
    ollama: OllamaClient
    embeddings: EmbeddingService
    vectors: VectorStore
    whisper: WhisperEngine
    pipeline: IngestionPipeline
    rag: RAGService
    users: UserStore


STATE = AppState()


def _persist_model_defaults(settings: Settings, store: Store, ollama: OllamaClient) -> Dict[str, Any]:
    current = store.get_settings()
    chat = current.get("chat_model") or settings.chat_model or ollama.select_chat_model() or ""
    vision = current.get("vision_model") or settings.vision_model or ollama.select_vision_model() or ""
    embed = current.get("embed_model") or settings.embed_model or ollama.select_embed_model() or ""
    patch = {
        "chat_model": chat,
        "vision_model": vision,
        "embed_model": embed,
        "privacy_shield": bool(current.get("privacy_shield", settings.privacy_shield)),
        "top_k": int(current.get("top_k", settings.top_k)),
        "strict_guard": bool(current.get("strict_guard", settings.strict_guard)),
    }
    store.set_settings(patch)
    return patch


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    privacy_error = settings.validate_privacy()
    if privacy_error:
        raise RuntimeError(privacy_error)
    settings.ensure_dirs()

    STATE.settings = settings
    STATE.events = EventBus()
    STATE.events.bind_loop(asyncio.get_running_loop())
    STATE.store = Store(settings.db_path)
    STATE.users = UserStore(settings.data_dir / "users.db", settings.admin_user, settings.admin_pass)
    STATE.ollama = OllamaClient(settings.ollama_url, timeout=settings.ollama_timeout)
    STATE.ollama.refresh_models(force=True)
    defaults = _persist_model_defaults(settings, STATE.store, STATE.ollama)
    STATE.embeddings = EmbeddingService(
        STATE.ollama,
        model=defaults.get("embed_model") or "",
        keep_alive=settings.embed_keep_alive,
    )
    STATE.vectors = VectorStore(settings, STATE.embeddings)
    STATE.whisper = WhisperEngine(
        model_size=settings.whisper_model,
        download_root=settings.models_dir / "whisper",
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        beam_size=settings.whisper_beam_size,
    )
    STATE.pipeline = IngestionPipeline(
        settings,
        STATE.store,
        STATE.vectors,
        STATE.ollama,
        STATE.whisper,
        STATE.events,
    )
    STATE.rag = RAGService(settings, STATE.store, STATE.vectors, STATE.ollama)
    for document in STATE.store.list_documents():
        meta = dict(document.get("meta") or {})
        if str(meta.get("department") or "").lower() in {"public", "genel", "general"}:
            meta["department"] = "yonetim"
            STATE.store.update_document(document["id"], meta=meta)
    for legacy in ("public", "genel", "general"):
        STATE.vectors.retag_department(legacy, "yonetim")
    current = STATE.store.get_settings()
    renamed = []
    changed = False
    for item in list(current.get("custom_departments") or []):
        slug = item.get("slug") if isinstance(item, dict) else item
        if slug == "i-slami-i-limler":
            renamed.append({"slug": "islami-ilimler", "name": "İslami İlimler"})
            changed = True
            for person in STATE.users.list_users():
                if person.get("department") == "i-slami-i-limler":
                    STATE.users.set_department(int(person["id"]), "islami-ilimler")
            for document in STATE.store.list_documents():
                meta = dict(document.get("meta") or {})
                if meta.get("department") == "i-slami-i-limler":
                    meta["department"] = "islami-ilimler"
                    STATE.store.update_document(document["id"], meta=meta)
            STATE.vectors.retag_department("i-slami-i-limler", "islami-ilimler")
        else:
            renamed.append(item if isinstance(item, dict) else {"slug": item, "name": "İslami İlimler" if item == "i-slami-i-limler" else item})
    if changed:
        current["custom_departments"] = renamed
        STATE.store.set_settings(current)
    STATE.pipeline.start()
    threading.Thread(target=STATE.pipeline.repair_textless_portraits, name="portrait-captions", daemon=True).start()
    threading.Thread(target=STATE.pipeline.repair_short_note_briefings, name="note-briefings", daemon=True).start()
    removed = STATE.pipeline.purge_origin("demo")
    if removed:
        logger.info("Removed %s pre-seeded demo document(s)", removed)
    demo_dir = settings.demo_dir
    if demo_dir.exists():
        shutil.rmtree(demo_dir, ignore_errors=True)
    from backend.services.briefing import sanitize_briefing

    for document in STATE.store.list_documents():
        briefing = document.get("briefing") or {}
        cleaned = sanitize_briefing(briefing)
        if (
            cleaned.get("summary") != (briefing.get("summary") or [])
            or cleaned.get("tags") != (briefing.get("tags") or [])
            or cleaned.get("entities") != (briefing.get("entities") or [])
        ):
            STATE.store.update_document(document["id"], briefing=cleaned)
    _refresh_suggested_questions()
    STATE.store.audit("app.start", {"version": __version__, "models": defaults})
    logger.info(
        "OmniVault ready on %s:%s · chat=%s vision=%s embed=%s",
        settings.host,
        settings.port,
        defaults.get("chat_model"),
        defaults.get("vision_model"),
        defaults.get("embed_model"),
    )
    try:
        yield
    finally:
        STATE.pipeline.stop()
        STATE.ollama.close()
        STATE.store.close()


app = FastAPI(
    title="OmniVault",
    description="On-premise multimodal enterprise memory & RAG platform",
    version=__version__,
    lifespan=lifespan,
)

class SessionAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope.get("headers") or []}
        token = ""
        authorization = headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        query = scope.get("query_string") or b""
        if not token and query:
            for part in query.decode("latin1").split("&"):
                if part.startswith("token="):
                    token = part.split("=", 1)[1]
        user = None
        users = getattr(STATE, "users", None)
        if users and token:
            user = users.resolve(token)
        if path.startswith("/api") and path not in PUBLIC_API and user is None:
            await JSONResponse({"detail": "Sign in required."}, status_code=401)(scope, receive, send)
            return
        context = CURRENT_USER.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT_USER.reset(context)


app.add_middleware(SessionAuth)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _doc_out(document: Dict[str, Any]) -> DocumentOut:
    from backend.services.briefing import sanitize_briefing

    cleaned = dict(document)
    if cleaned.get("briefing"):
        cleaned["briefing"] = sanitize_briefing(cleaned.get("briefing"))
    return DocumentOut.model_validate(cleaned)


def _role(header: Optional[str]) -> str:
    user = CURRENT_USER.get()
    if user:
        return normalize_role(str(user.get("role") or "reader"))
    return normalize_role(header)


def _session_department() -> Optional[str]:
    user = CURRENT_USER.get()
    if not user:
        return None
    return str(user.get("department") or "public")


def _permission() -> str:
    user = CURRENT_USER.get() or {}
    if user.get("role") == "admin":
        return "dept_admin"
    value = str(user.get("permission") or "editor")
    return value if value in {"read_only", "editor", "dept_admin"} else "editor"


def _require_write(role: str) -> None:
    if not can_write(role) or _permission() == "read_only":
        raise HTTPException(403, "This account can view and ask, but cannot add files.")


def _department_of(document: Dict[str, Any]) -> str:
    return str((document.get("meta") or {}).get("department") or "public")


def _active_department(role: str, header: Optional[str]) -> str:
    user = CURRENT_USER.get()
    if user and user.get("role") == "admin":
        raw = (header or "all").strip().lower()
        if raw in {"", "all", "tumu", "tümü", "*"}:
            return "all"
        from backend.core.access import normalize_department

        return normalize_department(raw)
    locked = _session_department()
    if locked and locked != "all":
        return locked
    raw = (header or "").strip().lower()
    if normalize_role(role) == "admin" and raw in {"all", "tumu", "tümü", "*"}:
        return "all"
    return effective_department(role, header)


def _stamp_department(role: str, explicit: Optional[str], header: Optional[str]) -> str:
    locked = _session_department()
    if locked and locked != "all" and normalize_role(role) != "admin":
        return upload_department(role, locked)
    choice = (explicit or "").strip()
    if not choice or choice.lower() in {"all", "tumu", "tümü"}:
        choice = header or "public"
    if choice.lower() in {"all", "tumu", "tümü"}:
        choice = "public"
    return upload_department(role, choice)


class LoginIn(BaseModel):
    username: str
    password: str


class UserIn(BaseModel):
    username: str
    password: str
    full_name: str
    role: str = "member"
    department: str = "public"
    permission: str = "editor"
    title: str = ""


class DemoIn(BaseModel):
    persona: str


@app.post("/api/auth/login")
def login(body: LoginIn) -> Dict[str, Any]:
    user = STATE.users.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(401, "Invalid username or password.")
    return user


@app.post("/api/auth/demo")
def demo_login(body: DemoIn) -> Dict[str, Any]:
    persona = body.persona.strip().lower()
    if persona not in {"admin", "ik", "finans", "teknik"}:
        raise HTTPException(400, "Unknown demo persona.")
    user = STATE.users.demo_login(persona)
    if not user:
        raise HTTPException(404, "That demo account is not in the database.")
    return user


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)) -> Dict[str, bool]:
    token = (authorization or "").replace("Bearer ", "", 1).strip()
    if token:
        STATE.users.logout(token)
    return {"ok": True}


@app.get("/api/auth/me")
def current_account() -> Dict[str, Any]:
    user = CURRENT_USER.get()
    if not user:
        raise HTTPException(401, "Sign in required.")
    return user


class DepartmentIn(BaseModel):
    department: Optional[str] = None
    password: Optional[str] = None
    permission: Optional[str] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    title: Optional[str] = None


class ProfileIn(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None


@app.patch("/api/auth/profile")
def update_profile(body: ProfileIn) -> Dict[str, Any]:
    user = CURRENT_USER.get()
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "Only an administrator can change these credentials.")
    try:
        if body.username and body.username.strip():
            STATE.users.set_username(int(user["id"]), body.username.strip())
        if body.password and body.password.strip():
            STATE.users.set_password(int(user["id"]), body.password.strip())
    except Exception as exc:
        raise HTTPException(400, "That username is already in use.") from exc
    return {"ok": True}


@app.get("/api/users")
def list_users() -> list:
    if not can_admin(_role(None)):
        raise HTTPException(403, "Only an administrator can manage users.")
    return STATE.users.list_users()


@app.post("/api/users")
def create_user(body: UserIn) -> Dict[str, Any]:
    if not can_admin(_role(None)):
        raise HTTPException(403, "Only an administrator can add a user.")
    role = "member" if body.role != "reader" else "reader"
    try:
        return STATE.users.create(
            body.username,
            body.password,
            body.full_name,
            role,
            body.department or "yonetim",
            body.permission,
            body.title,
        )
    except Exception as exc:
        raise HTTPException(400, "That username is already in use.") from exc


@app.patch("/api/users/{user_id}")
def update_user_department(user_id: int, body: DepartmentIn) -> Dict[str, bool]:
    if not can_admin(_role(None)):
        raise HTTPException(403, "Only an administrator can manage users.")
    target = STATE.users.get(user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    root = target.get("role") == "admin"
    try:
        if body.username is not None:
            STATE.users.set_username(user_id, body.username)
        if body.full_name is not None:
            STATE.users.set_full_name(user_id, body.full_name)
        if body.title is not None:
            STATE.users.set_title(user_id, body.title)
    except ValueError as exc:
        detail = "Bu kullanıcı adı zaten kullanılıyor." if "taken" in str(exc) else "Ad ve kullanıcı adı boş bırakılamaz."
        raise HTTPException(409, detail) from exc
    if body.department and not root:
        STATE.users.set_department(user_id, body.department)
    if body.password and body.password.strip():
        STATE.users.set_password(user_id, body.password.strip())
    if body.permission and not root:
        STATE.users.set_permission(user_id, body.permission)
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int) -> Dict[str, bool]:
    if not can_admin(_role(None)):
        raise HTTPException(403, "Only an administrator can manage users.")
    current = CURRENT_USER.get() or {}
    if int(current.get("id") or 0) == user_id:
        raise HTTPException(400, "You cannot delete the account you are using.")
    removed = next((item for item in STATE.users.list_users() if int(item["id"]) == user_id), None)
    username = str((removed or {}).get("username") or "")
    if username:
        for document in STATE.store.list_documents():
            meta = dict(document.get("meta") or {})
            if meta.get("uploaded_by_username") == username:
                meta["uploaded_by_name"] = "Kurumsal Arşiv (Genel)"
                STATE.store.update_document(document["id"], meta=meta)
    STATE.users.delete_user(user_id)
    return {"ok": True}


@app.get("/api/health", response_model=HealthResponse)
@app.get("/api/status", response_model=HealthResponse)
def health(
    department: Optional[str] = Query(default=None),
    x_omnivault_role: Optional[str] = Header(default=None),
    x_omnivault_department: Optional[str] = Header(default=None),
) -> HealthResponse:
    role = _role(x_omnivault_role)
    active = _active_department(role, department or x_omnivault_department)
    documents = [
        document
        for document in STATE.store.list_documents()
        if can_see_department(role, _department_of(document), active)
    ]
    allowed = visible_departments(role, active)
    if allowed is None:
        vectors = STATE.vectors.count()
    else:
        vectors = sum(STATE.vectors.count_department(item) for item in allowed)
    ollama_status = STATE.ollama.status()
    return HealthResponse(
        status="ok" if ollama_status.get("online") else "degraded",
        version=__version__,
        ollama=ollama_status,
        whisper=STATE.whisper.status(),
        vector_count=vectors,
        document_count=len(documents),
        privacy_shield=bool(STATE.store.get_settings().get("privacy_shield", STATE.settings.privacy_shield)),
        ram_gb=round(psutil.virtual_memory().total / (1024**3), 1),
        platform=f"{platform.system()} {platform.machine()}",
    )


@app.get("/api/settings", response_model=RuntimeSettings)
def get_runtime_settings() -> RuntimeSettings:
    persisted = STATE.store.get_settings()
    models = STATE.ollama.list_models()
    available = {
        "chat": [
            {
                "name": m.name,
                "size": m.size,
                "parameter_size": m.parameter_size,
                "family": m.family,
                "quantization": m.quantization,
            }
            for m in models
            if m.is_chat and not m.is_vision
        ],
        "vision": [
            {
                "name": m.name,
                "size": m.size,
                "parameter_size": m.parameter_size,
                "family": m.family,
                "quantization": m.quantization,
            }
            for m in models
            if m.is_vision
        ],
        "embed": [
            {
                "name": m.name,
                "size": m.size,
                "parameter_size": m.parameter_size,
                "family": m.family,
                "quantization": m.quantization,
                "embedding_length": m.embedding_length,
            }
            for m in models
            if m.is_embedding
        ],
    }
    # Also allow selecting vision-capable chat models in the chat dropdown when useful.
    for model in models:
        if model.is_chat and model.is_vision:
            available["chat"].append(
                {
                    "name": model.name,
                    "size": model.size,
                    "parameter_size": model.parameter_size,
                    "family": model.family,
                    "quantization": model.quantization,
                }
            )
    return RuntimeSettings(
        chat_model=str(persisted.get("chat_model") or STATE.ollama.select_chat_model() or ""),
        vision_model=str(persisted.get("vision_model") or STATE.ollama.select_vision_model() or ""),
        embed_model=str(persisted.get("embed_model") or STATE.ollama.select_embed_model() or ""),
        privacy_shield=bool(persisted.get("privacy_shield", STATE.settings.privacy_shield)),
        top_k=int(persisted.get("top_k", STATE.settings.top_k)),
        strict_guard=bool(persisted.get("strict_guard", STATE.settings.strict_guard)),
        chat_keep_alive=STATE.settings.chat_keep_alive,
        embed_keep_alive=STATE.settings.embed_keep_alive,
        chat_num_ctx=STATE.settings.chat_num_ctx,
        ollama_url=STATE.settings.ollama_url,
        available=available,
        gemini_key_set=bool(google_api_key(STATE.store, STATE.settings)),
        groq_key_set=bool(groq_api_key(STATE.store, STATE.settings)),
        engines=STATE.rag.router.catalog(),
    )


@app.put("/api/settings", response_model=RuntimeSettings)
def update_runtime_settings(body: ModelSelection) -> RuntimeSettings:
    current = STATE.store.get_settings()
    if body.chat_model is not None:
        current["chat_model"] = body.chat_model
    if body.vision_model is not None:
        current["vision_model"] = body.vision_model
    if body.embed_model is not None:
        current["embed_model"] = body.embed_model
        STATE.embeddings.set_model(body.embed_model)
    if body.privacy_shield is not None:
        current["privacy_shield"] = body.privacy_shield
    if body.top_k is not None:
        current["top_k"] = body.top_k
    if body.strict_guard is not None:
        current["strict_guard"] = body.strict_guard
    if body.gemini_api_key and body.gemini_api_key.strip():
        current["gemini_api_key"] = body.gemini_api_key.strip()
    if body.groq_api_key and body.groq_api_key.strip():
        current["groq_api_key"] = body.groq_api_key.strip()
    STATE.store.set_settings(current)
    public_settings = {key: value for key, value in current.items() if "api_key" not in key and not key.endswith("_key")}
    STATE.store.audit("settings.update", public_settings)
    STATE.events.publish("settings", public_settings)
    STATE.rag.router.refresh_models(force=True)
    return get_runtime_settings()


@app.get("/api/documents", response_model=List[DocumentOut])
def list_documents(
    x_omnivault_role: Optional[str] = Header(default="admin"),
    x_omnivault_department: Optional[str] = Header(default="public"),
) -> List[DocumentOut]:
    role = _role(x_omnivault_role)
    active = _active_department(role, x_omnivault_department)
    visible = [
        document
        for document in STATE.store.list_documents()
        if can_see_department(role, _department_of(document), active)
    ]
    return [_doc_out(document) for document in visible]


@app.get("/api/documents/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: str) -> DocumentOut:
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    return _doc_out(document)


@app.post("/api/documents/upload", response_model=List[DocumentOut])
async def upload_documents(
    files: List[UploadFile] = File(...),
    lang: str = Query(default="en"),
    department: str = Query(default="public"),
    x_omnivault_role: Optional[str] = Header(default="admin"),
    x_omnivault_department: Optional[str] = Header(default="public"),
) -> List[DocumentOut]:
    role = _role(x_omnivault_role)
    _require_write(role)
    try:
        stamped = _stamp_department(role, department, x_omnivault_department)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if not files:
        raise HTTPException(400, "No files uploaded")
    accepted = PDF_TYPES | TEXT_TYPES | OFFICE_TYPES | AUDIO_TYPES | IMAGE_TYPES
    created: List[DocumentOut] = []
    staging = STATE.settings.data_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    for upload in files:
        filename = Path(upload.filename or "upload.bin").name
        suffix = Path(filename).suffix.lower()
        if suffix not in accepted:
            raise HTTPException(400, f"Unsupported file type for {filename}")
        target = staging / f"{int(time.time() * 1000)}_{filename}"
        size = 0
        with target.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > STATE.settings.max_upload_mb * 1024 * 1024:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, f"{filename} exceeds {STATE.settings.max_upload_mb} MB limit")
                handle.write(chunk)
        document = STATE.pipeline.register_file(
            target, filename=filename, origin="upload", copy=False, lang=lang, department=stamped, role=role
        )
        created.append(_doc_out(document))
    return created


@app.delete("/api/documents/{doc_id}")
def delete_document(
    doc_id: str,
    x_omnivault_role: Optional[str] = Header(default="admin"),
    x_omnivault_department: Optional[str] = Header(default="public"),
) -> Dict[str, Any]:
    role = _role(x_omnivault_role)
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    if _permission() != "dept_admin" or not can_mutate_document(role, _department_of(document), x_omnivault_department):
        raise HTTPException(403, "You cannot remove this document.")
    if not STATE.pipeline.delete_document(doc_id):
        raise HTTPException(404, "Document not found")
    return {"ok": True, "id": doc_id}


class TextEditIn(BaseModel):
    text: str


def _document_body(document: Dict[str, Any]) -> str:
    edited = str((document.get("meta") or {}).get("edited_text") or "").strip()
    if len(edited) > 40:
        return edited
    return STATE.vectors.document_text(str(document["id"]), limit=6)


def _refresh_suggested_questions() -> None:
    for document in STATE.store.list_documents():
        if document.get("status") != "ready":
            continue
        body = _document_body(document)
        briefing = document.get("briefing") or {}
        questions = (
            questions_from_content(body, [str(item) for item in (briefing.get("entities") or [])])
            if body.strip()
            else {"tr": [], "en": []}
        )
        meta = dict(document.get("meta") or {})
        if (
            meta.get("suggested_questions_tr") != questions["tr"]
            or meta.get("suggested_questions_en") != questions["en"]
            or meta.get("suggested_questions") != questions["tr"]
        ):
            meta["suggested_questions"] = questions["tr"]
            meta["suggested_questions_tr"] = questions["tr"]
            meta["suggested_questions_en"] = questions["en"]
            STATE.store.update_document(document["id"], meta=meta)


@app.get("/api/documents/{doc_id}/text")
def read_document_text(doc_id: str) -> Dict[str, str]:
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    return {"text": _document_body(document)}


@app.put("/api/documents/{doc_id}/text", response_model=DocumentOut)
def edit_document_text(doc_id: str, body: TextEditIn) -> DocumentOut:
    role = _role(None)
    _require_write(role)
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    updated = STATE.pipeline.replace_text(doc_id, body.text.strip())
    if not updated:
        raise HTTPException(400, "That text could not be saved.")
    return _doc_out(updated)


@app.post("/api/documents/{doc_id}/update-text", response_model=DocumentOut)
def update_document_text(
    doc_id: str,
    body: TextEditIn,
    x_omnivault_department: Optional[str] = Header(default="public"),
) -> DocumentOut:
    """Confirm a chat-proposed edit: rewrite text, briefing, and vectors."""
    role = _role(None)
    _require_write(role)
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    if not can_mutate_document(role, _department_of(document), _active_department(role, x_omnivault_department)):
        raise HTTPException(403, "You cannot update this document.")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Updated text is required.")
    updated = STATE.pipeline.replace_text(doc_id, text)
    if not updated:
        raise HTTPException(400, "That text could not be saved.")
    return _doc_out(updated)


@app.post("/api/documents/{doc_id}/reindex", response_model=DocumentOut)
def reindex_document(doc_id: str) -> DocumentOut:
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    if document.get("status") == "duplicate":
        raise HTTPException(400, "Duplicate placeholders cannot be reindexed")
    updated = STATE.store.update_document(
        doc_id,
        status="queued",
        stage="queued",
        progress=0.02,
        message="Re-queued for ingestion",
        error=None,
    )
    STATE.pipeline.enqueue(doc_id)
    STATE.events.publish("document", updated)
    return _doc_out(updated or document)


class TransferIn(BaseModel):
    target_department: str
    mode: str


def _department_name(slug: str) -> str:
    for item in STATE.store.list_departments():
        if item.get("slug") == slug:
            return str(item.get("name") or slug)
    return slug


@app.post("/api/documents/{doc_id}/transfer")
def transfer_document(
    doc_id: str,
    body: TransferIn,
    x_omnivault_department: Optional[str] = Header(default="public"),
) -> Dict[str, Any]:
    role = _role(None)
    _require_write(role)
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    if not can_mutate_document(role, _department_of(document), _active_department(role, x_omnivault_department)):
        raise HTTPException(403, "You cannot transfer this document.")
    try:
        updated = STATE.pipeline.transfer_document(doc_id, normalize_department(body.target_department), body.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    slug = str((updated.get("meta") or {}).get("department") or "")
    return {
        "document": _doc_out(updated).model_dump(),
        "mode": body.mode,
        "department": slug,
        "department_name": _department_name(slug),
    }


@app.get("/api/departments/options")
def department_options() -> List[Dict[str, str]]:
    return [{"slug": str(item["slug"]), "name": str(item["name"])} for item in STATE.store.list_departments()]


class McpCallIn(BaseModel):
    name: str
    arguments: Dict[str, Any] = {}


def _mcp_services() -> Dict[str, Any]:
    return {"store": STATE.store, "vectors": STATE.vectors, "pipeline": STATE.pipeline}


def _mcp_user() -> Dict[str, Any]:
    user = CURRENT_USER.get() or {}
    return {"role": user.get("role") or "reader", "department": user.get("department") or ""}


@app.get("/api/mcp/tools")
def mcp_tools() -> Dict[str, Any]:
    listed = mcp_server.catalog()
    return {"tools": listed["tools"], "count": listed["tool_count"], "cursor": mcp_server.cursor_config()}


@app.get("/api/mcp/resources")
def mcp_resources(uri: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    if uri:
        try:
            return mcp_server.read_resource(uri, services=_mcp_services(), user=_mcp_user())
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    listed = mcp_server.catalog()
    return {"resources": listed["resources"], "count": listed["resource_count"]}


@app.post("/api/mcp/call")
def mcp_call(body: McpCallIn) -> Dict[str, Any]:
    try:
        result = mcp_server.call_tool(body.name, body.arguments, services=_mcp_services(), user=_mcp_user())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"name": body.name, "result": result}


@app.get("/api/files/{doc_id}/raw")
@app.get("/api/documents/{doc_id}/media")
def document_media(doc_id: str):
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    path = Path(document["stored_path"])
    if not path.exists():
        raise HTTPException(404, "Media file missing")
    media_type = document.get("mime") or mimetypes_guess(path)
    return FileResponse(path, media_type=media_type, filename=document["filename"], content_disposition_type="inline")


@app.get("/api/documents/{doc_id}/thumbnail")
def document_thumbnail(doc_id: str):
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    thumb = STATE.settings.thumbnails_dir / f"{doc_id}.jpg"
    if thumb.exists():
        return FileResponse(thumb, media_type="image/jpeg")
    if document.get("modality") == "image":
        path = Path(document["stored_path"])
        if path.exists():
            return FileResponse(path)
    raise HTTPException(404, "Thumbnail not available")


@app.get("/api/documents/{doc_id}/page/{page}", response_model=PdfPageResponse)
def document_page(doc_id: str, page: int, scale: float = Query(default=1.6, ge=0.8, le=3.0)):
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    if document.get("modality") != "pdf":
        raise HTTPException(400, "Page preview is only available for PDF documents")
    path = Path(document["stored_path"])
    if not path.exists():
        raise HTTPException(404, "PDF missing on disk")
    try:
        png, width, height, text = render_pdf_page(path, page, scale=scale)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    cache = STATE.settings.thumbnails_dir / f"{doc_id}_p{page}.png"
    cache.write_bytes(png)
    page_count = int((document.get("meta") or {}).get("page_count") or 0)
    return PdfPageResponse(
        doc_id=doc_id,
        page=page,
        page_count=page_count,
        width=width,
        height=height,
        image_url=f"/api/documents/{doc_id}/page/{page}/image",
        text=text,
    )


@app.get("/api/documents/{doc_id}/page/{page}/image")
def document_page_image(doc_id: str, page: int, scale: float = Query(default=1.6, ge=0.8, le=3.0)):
    document = STATE.store.get_document(doc_id)
    if not document or document.get("modality") != "pdf":
        raise HTTPException(404, "PDF page not found")
    cache = STATE.settings.thumbnails_dir / f"{doc_id}_p{page}.png"
    if not cache.exists():
        path = Path(document["stored_path"])
        png, _, _, _ = render_pdf_page(path, page, scale=scale)
        cache.write_bytes(png)
    return FileResponse(cache, media_type="image/png")


@app.get("/api/documents/{doc_id}/transcript")
def document_transcript(doc_id: str):
    document = STATE.store.get_document(doc_id)
    if not document:
        raise HTTPException(404, "Document not found")
    transcript = STATE.settings.transcripts_dir / f"{doc_id}.json"
    if not transcript.exists():
        raise HTTPException(404, "Transcript not available")
    return FileResponse(transcript, media_type="application/json")


@app.get("/api/history")
def query_history(
    department: Optional[str] = Query(default=None),
    username: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    user = CURRENT_USER.get()
    if not user:
        raise HTTPException(401, "Sign in required.")
    if str(user.get("role") or "") != "admin":
        department = None
        username = None
    return {"items": STATE.users.list_queries(user, department=department, username=username)}


class ThreadTitleIn(BaseModel):
    conversation_id: str
    title: str


@app.patch("/api/history/thread")
def rename_history_thread(body: ThreadTitleIn) -> Dict[str, str]:
    user = CURRENT_USER.get()
    if not user:
        raise HTTPException(401, "Sign in required.")
    try:
        title = STATE.users.rename_thread(user, body.conversation_id.strip(), body.title)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"title": title}


@app.delete("/api/history/{query_id}")
def delete_query_item(query_id: int) -> Dict[str, bool]:
    user = CURRENT_USER.get()
    if not user:
        raise HTTPException(401, "Sign in required.")
    STATE.users.delete_query(user, query_id)
    return {"ok": True}


@app.delete("/api/history")
def clear_query_history() -> Dict[str, bool]:
    user = CURRENT_USER.get()
    if not user:
        raise HTTPException(401, "Sign in required.")
    STATE.users.clear_queries(user)
    return {"ok": True}


class SheetIn(BaseModel):
    filename: str = "omnivault"
    columns: List[str]
    rows: List[List[str]]


_SHEET_WIDTHS = {
    "Sohbet Başlığı": 32,
    "Soru": 35,
    "Yanıt": 85,
    "Tarih": 20,
    "Kullanıcı": 18,
    "Unvan": 18,
    "Departman": 16,
    "Güven Skoru": 14,
    "Model": 28,
}


def _xlsx_bytes(columns: List[str], rows: List[List[str]]) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = "OmniVault"
    sheet.append([str(column)[:200] for column in columns])
    for row in rows[:5000]:
        sheet.append(["" if cell is None else str(cell)[:8000] for cell in row])
    wrapped = Alignment(wrap_text=True, vertical="top")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = wrapped
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrapped
    for index, column in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(index)].width = _SHEET_WIDTHS.get(str(column), 18)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _attachment_name(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", filename or "omnivault")[:80] or "omnivault"


@app.post("/api/export.xlsx")
def export_xlsx(body: SheetIn) -> Response:
    if not CURRENT_USER.get():
        raise HTTPException(401, "Sign in required.")
    name = _attachment_name(body.filename)
    return Response(
        _xlsx_bytes(body.columns, body.rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )


class HistoryExportIn(BaseModel):
    filename: str = "sorgu-gecmisi"
    kind: str = "xlsx"
    department: str = ""
    username: str = ""
    search: str = ""
    conversation_id: str = ""
    query_ids: List[int] = []


_HISTORY_COLUMNS = ["Tarih", "Kullanıcı", "Unvan", "Departman", "Sohbet Başlığı", "Soru", "Yanıt", "Güven Skoru", "Model"]


def _history_rows(items: List[Dict[str, Any]]) -> List[List[str]]:
    names = {str(row.get("slug")): str(row.get("name") or row.get("slug")) for row in STATE.store.list_departments()}
    names["all"] = "Tüm Departmanlar"
    ordered = sorted(items, key=lambda item: item.get("created_at") or 0)

    def stamp(value: Any) -> str:
        try:
            return time.strftime("%d.%m.%Y %H:%M", time.localtime(float(value)))
        except (TypeError, ValueError, OSError):
            return ""

    rows = []
    for item in ordered:
        answer = item.get("answer") if isinstance(item.get("answer"), dict) else {}
        department = str(item.get("department") or "")
        rows.append(
            [
                stamp(item.get("created_at")),
                str(item.get("user_full_name") or item.get("username") or ""),
                str(item.get("user_title") or ""),
                names.get(department, department),
                str(item.get("thread_title") or ""),
                str(item.get("question") or ""),
                str(answer.get("answer") or ""),
                str(answer.get("grounding_score") if answer.get("grounding_score") is not None else ""),
                str(answer.get("model") or ""),
            ]
        )
    return rows


@app.post("/api/history/export")
def export_history(body: HistoryExportIn) -> Response:
    user = CURRENT_USER.get()
    if not user:
        raise HTTPException(401, "Sign in required.")
    items = STATE.users.list_queries(
        user,
        department=body.department,
        username=body.username,
        search=body.search,
        conversation_id=body.conversation_id,
        query_ids=body.query_ids,
    )
    name = _attachment_name(body.filename)
    if str(body.kind).lower() == "csv":
        import csv
        from io import StringIO

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(_HISTORY_COLUMNS)
        writer.writerows(_history_rows(items))
        payload = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        return Response(
            payload,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    return Response(
        _xlsx_bytes(_HISTORY_COLUMNS, _history_rows(items)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )


def _update_proposal_response(
    question: str,
    *,
    role: str,
    active: str,
    language: str,
    conversation_id: str,
) -> Optional[QueryResponse]:
    if not parse_update_command(question):
        return None
    if not can_write(role) or _permission() == "read_only":
        message = (
            "Bu kayıt yalnızca yetkili editör veya yönetici tarafından değiştirilebilir."
            if language == "tr"
            else "Only authorized editors or administrators can change vault records."
        )
        return QueryResponse(
            answer=message,
            citations=[],
            grounding_score=0.0,
            grounding_label="Weak",
            modalities_used=[],
            model="policy",
            retrieved=0,
            privacy_redactions={},
            latency_ms=0.0,
            engine="policy",
            provider="local",
            engine_label="Yetki" if language == "tr" else "Permission",
            conversation_id=conversation_id,
        )
    departments = visible_departments(role, active)
    proposal = propose_update(question, departments=departments, store=STATE.store, vectors=STATE.vectors)
    if proposal is None:
        return None
    if proposal.get("error") == "not_found":
        message = (
            f"Aktif departmanda “{proposal.get('subject')}” için güncellenecek bir kayıt bulunamadı."
            if language == "tr"
            else f"No updatable record for “{proposal.get('subject')}” was found in the active department."
        )
        return QueryResponse(
            answer=message,
            citations=[],
            grounding_score=0.0,
            grounding_label="Weak",
            modalities_used=[],
            model="policy",
            retrieved=0,
            privacy_redactions={},
            latency_ms=0.0,
            engine="policy",
            provider="local",
            engine_label="Güncelleme" if language == "tr" else "Update",
            conversation_id=conversation_id,
        )
    if proposal.get("error"):
        message = str(proposal.get("message") or "Bu kayıt güncellenemedi.")
        return QueryResponse(
            answer=message,
            citations=[],
            grounding_score=0.0,
            grounding_label="Weak",
            modalities_used=[],
            model="policy",
            retrieved=0,
            privacy_redactions={},
            latency_ms=0.0,
            engine="policy",
            provider="local",
            engine_label="Güncelleme" if language == "tr" else "Update",
            conversation_id=conversation_id,
        )
    pending = PendingUpdate(
        doc_id=str(proposal["doc_id"]),
        filename=str(proposal["filename"]),
        department=str(proposal.get("department") or ""),
        old_snippet=str(proposal["old_snippet"]),
        new_snippet=str(proposal["new_snippet"]),
        new_text=str(proposal["new_text"]),
        kind=str(proposal.get("kind") or ""),
        subject=str(proposal.get("subject") or ""),
    )
    answer = (
        "Değişiklik henüz uygulanmadı. Aşağıdaki öneriyi onaylarsanız kayıt, özet ve vektörler güncellenir."
        if language == "tr"
        else "Nothing has been changed yet. Confirm the proposal below to update the record, summary, and vectors."
    )
    return QueryResponse(
        answer=answer,
        citations=[],
        grounding_score=0.0,
        grounding_label="Weak",
        modalities_used=["text"],
        model="policy",
        retrieved=1,
        privacy_redactions={},
        latency_ms=0.0,
        engine="policy",
        provider="local",
        engine_label="Onay Bekliyor" if language == "tr" else "Awaiting confirmation",
        conversation_id=conversation_id,
        pending_update=pending,
    )


@app.post("/api/query", response_model=QueryResponse)
def query_memory(
    body: QueryRequest,
    x_omnivault_role: Optional[str] = Header(default="admin"),
    x_omnivault_department: Optional[str] = Header(default="public"),
) -> QueryResponse:
    role = _role(x_omnivault_role)
    active = _active_department(role, x_omnivault_department)
    conversation_id = (body.conversation_id or "").strip() or uuid.uuid4().hex
    language = body.language or body.lang or "tr"
    try:
        proposed = _update_proposal_response(
            body.question,
            role=role,
            active=active,
            language=language,
            conversation_id=conversation_id,
        )
        if proposed is not None:
            user = CURRENT_USER.get()
            if user:
                STATE.users.add_query(
                    int(user["id"]),
                    active,
                    body.question,
                    proposed.model_dump(),
                    username=str(user.get("username") or ""),
                    full_name=str(user.get("full_name") or ""),
                    title=str(user.get("title") or ""),
                    conversation_id=conversation_id,
                )
            return proposed
        prior = [{"question": turn.question, "answer": turn.answer} for turn in (body.history or [])][-4:]
        prior_docs = STATE.users.cited_doc_ids(conversation_id)
        result = STATE.rag.query(
            body.question,
            top_k=body.top_k,
            modalities=body.modalities,
            doc_ids=body.doc_ids,
            chat_model=body.chat_model,
            lang=language,
            departments=visible_departments(role, active),
            persona_department="public" if active in {"all", "tumu", "tümü"} else active,
            engine=body.engine,
            history=prior,
            prior_doc_ids=prior_docs,
        )
        result.conversation_id = conversation_id
        user = CURRENT_USER.get()
        if user:
            STATE.users.add_query(
                int(user["id"]),
                active,
                body.question,
                result.model_dump(),
                username=str(user.get("username") or ""),
                full_name=str(user.get("full_name") or ""),
                title=str(user.get("title") or ""),
                conversation_id=conversation_id,
            )
        return result
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("Query failed")
        raise HTTPException(500, f"Query failed: {exc}") from exc


class NoteIn(BaseModel):
    title: str
    body: str
    department: str = "public"
    lang: str = "en"


@app.post("/api/notes", response_model=DocumentOut)
def create_note(
    body: NoteIn,
    x_omnivault_role: Optional[str] = Header(default="admin"),
    x_omnivault_department: Optional[str] = Header(default="public"),
) -> DocumentOut:
    role = _role(x_omnivault_role)
    _require_write(role)
    title = body.title.strip()
    text = body.body.strip()
    if not title or not text:
        raise HTTPException(400, "A title and a note are required.")
    try:
        department = _stamp_department(role, body.department, x_omnivault_department)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    staging = STATE.settings.data_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "-" for ch in title).strip().replace(" ", "-") or "note"
    path = staging / f"{int(time.time() * 1000)}_{safe[:80]}.md"
    path.write_text(f"# {title}\n\nDepartment: {department}\n\n{text}\n", encoding="utf-8")
    document = STATE.pipeline.register_file(
        path,
        filename=f"{title[:120]}.md",
        origin="note",
        copy=False,
        lang=body.lang,
        department=department,
        role=role,
    )
    return _doc_out(document)


@app.get("/api/suggestions")
def suggestions(
    lang: str = Query(default="en"),
    x_omnivault_role: Optional[str] = Header(default="admin"),
    x_omnivault_department: Optional[str] = Header(default="public"),
) -> Dict[str, Any]:
    role = _role(x_omnivault_role)
    active = _active_department(role, x_omnivault_department)
    _refresh_suggested_questions()
    visible = [
        document
        for document in STATE.store.list_documents()
        if can_see_department(role, _department_of(document), active)
    ]
    return {"questions": suggest_questions(visible, lang=lang), "persona": active}


class DepartmentNameIn(BaseModel):
    name: str


BUILTIN_DEPARTMENTS = (
    ("hr", "İK"),
    ("finance", "Finans"),
    ("engineering", "Teknik"),
    ("yonetim", "Yönetim"),
)


def _department_rows() -> List[Dict[str, Any]]:
    current = STATE.store.get_settings()
    for item in list(current.get("custom_departments") or []):
        if isinstance(item, dict):
            STATE.store.add_department(str(item.get("slug")), str(item.get("name") or item.get("slug")))
        elif str(item) not in {"i-slami-i-limler"}:
            STATE.store.add_department(str(item), str(item))
        else:
            STATE.store.add_department("islami-ilimler", "İslami İlimler")
    users = STATE.users.list_users()
    documents = STATE.store.list_documents()
    rows = []
    for item in STATE.store.list_departments():
        slug = item["slug"]
        user_count = sum(1 for user in users if user.get("department") == slug or (slug == "yonetim" and user.get("role") == "admin"))
        rows.append(
            {
                "slug": slug,
                "name": item["name"],
                "sort_order": item["sort_order"],
                "users": user_count,
                "files": sum(1 for document in documents if _department_of(document) == slug),
            }
        )
    return rows


@app.get("/api/departments")
def list_departments() -> List[Dict[str, Any]]:
    if not can_admin(_role(None)):
        raise HTTPException(403, "Only an administrator can manage departments.")
    return _department_rows()


@app.post("/api/departments")
def add_department(body: DepartmentNameIn, x_omnivault_role: Optional[str] = Header(default="admin")) -> List[Dict[str, Any]]:
    if not can_admin(_role(x_omnivault_role)):
        raise HTTPException(403, "Only an administrator can add a department.")
    from backend.core.access import normalize_department

    slug = normalize_department(body.name)
    if not slug or slug in {"public", "all"}:
        raise HTTPException(400, "Enter a department name.")
    current = STATE.store.get_settings()
    custom = list(current.get("custom_departments") or [])
    known = {item[0] for item in BUILTIN_DEPARTMENTS}
    known_slugs = {item.get("slug") if isinstance(item, dict) else item for item in custom}
    if slug not in known and slug not in known_slugs:
        custom.append({"slug": slug, "name": body.name.strip()})
        current["custom_departments"] = custom
        STATE.store.set_settings(current)
    STATE.store.add_department(slug, body.name.strip())
    return _department_rows()


class ReorderIn(BaseModel):
    slug: str
    direction: str


@app.post("/api/departments/reorder")
def reorder_department(body: ReorderIn) -> List[Dict[str, Any]]:
    if not can_admin(_role(None)):
        raise HTTPException(403, "Only an administrator can reorder departments.")
    STATE.store.move_department(body.slug, "up" if body.direction == "up" else "down")
    return _department_rows()


@app.delete("/api/departments/{slug}")
def delete_department(slug: str) -> List[Dict[str, Any]]:
    if not can_admin(_role(None)):
        raise HTTPException(403, "Only an administrator can delete a department.")
    row = next((item for item in _department_rows() if item["slug"] == slug), None)
    if not row:
        raise HTTPException(404, "Department not found.")
    if row["users"] or row["files"]:
        raise HTTPException(409, "Bu departmanda aktif veri veya kullanıcı bulunduğu için silinemez. Önce içeriği boşaltın veya aktarın.")
    current = STATE.store.get_settings()
    current["custom_departments"] = [
        item for item in (current.get("custom_departments") or [])
        if (item.get("slug") if isinstance(item, dict) else item) != slug
    ]
    STATE.store.set_settings(current)
    STATE.store.delete_department_row(slug)
    return _department_rows()


def _polish_page(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# "):
            lines = lines[index:]
            break
    noise = (
        "Unmute", "Playback Speed", "Quality", "Subtitles", "Empower Player",
        "1920p", "1280p", "640p", "256p", "Deftere Ekle", "Ben de Yaptım",
        "Eline Sağlık", "Uygulamada Aç", "Tarifi Yazdır", "Paylaşmadan Gitme",
        "WhatsApp kanalı", "Youtube kanalımızda", "AI'a Sor", "Tarif Gönder",
    )
    cleaned = []
    for line in lines:
        if any(token.lower() in line.lower() for token in noise):
            continue
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\[\[\d+\]\]\([^)]*\)", "", line)
        line = re.sub(r"\[\d+\]", "", line)
        line = re.sub(r"\[değiştir \| kaynağı değiştir\]", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\[[^\]]*\]\(javascript:.*\)", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = line.strip()
        if line:
            cleaned.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()


class UrlIn(BaseModel):
    url: str
    lang: str = "en"
    department: str = "public"
    preview: bool = False


@app.post("/api/urls")
def ingest_url(
    body: UrlIn,
    x_omnivault_role: Optional[str] = Header(default="admin"),
    x_omnivault_department: Optional[str] = Header(default="public"),
):
    role = _role(x_omnivault_role)
    _require_write(role)
    from urllib.parse import urlparse
    import httpx
    from bs4 import BeautifulSoup

    def _clean_article(html: str) -> tuple[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:180]
        noise = ("nav", "header", "footer", "aside", "form", "button", "script", "style", "svg", "noscript")
        for tag in soup.find_all(noise):
            tag.decompose()
        pattern = re.compile(r"nav|menu|header|footer|sidebar|login|signup|auth|cookie|popup|modal|comment|share|social|breadcrumb|ad", re.I)
        for node in list(soup.find_all(True)):
            marker = " ".join([str(node.get("id") or ""), " ".join(node.get("class") or [])])
            if pattern.search(marker):
                node.decompose()
        root = soup.find("article") or soup.find("main") or soup.body or soup
        heading = soup.find("h1")
        lines = []
        if heading:
            lines.append(heading.get_text(" ", strip=True))
        for node in root.find_all(["h1", "h2", "h3", "p", "li"]):
            line = node.get_text(" ", strip=True)
            if not line or line in lines:
                continue
            if len(line) < 20 and not re.search(r"[.!?:,;]|\d", line):
                continue
            lines.append(line)
        return title, "\n".join(lines)

    try:
        parsed = urlparse(body.url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(400, "Enter an http or https address.")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }
        text = ""
        title = parsed.netloc
        try:
            response = httpx.get(body.url.strip(), headers=headers, follow_redirects=True, timeout=15.0, verify=False)
            response.raise_for_status()
            title, text = _clean_article(response.text)
            title = title or parsed.netloc
        except Exception:
            text = ""
        if len(text.strip()) < 40:
            mirror = httpx.get(
                f"https://r.jina.ai/{body.url.strip()}",
                headers={"User-Agent": headers["User-Agent"]},
                follow_redirects=True,
                timeout=30.0,
                verify=False,
            )
            mirror.raise_for_status()
            text = mirror.text.strip()
            first = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
            if first:
                title = first[:180]
        text = _polish_page(text)
        if len(text.strip()) < 40:
            raise HTTPException(422, "That page did not contain enough readable text.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Could not read that page: {exc}") from exc
    if body.preview:
        return {"title": title, "text": text[:50000]}
    department = _stamp_department(role, body.department, x_omnivault_department)
    staging = STATE.settings.data_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"{int(time.time() * 1000)}_page.md"
    path.write_text(f"# {title}\n\nSource: {body.url.strip()}\n\n{text}\n", encoding="utf-8")
    document = STATE.pipeline.register_file(
        path,
        filename=f"{title[:80]}.md",
        origin="url",
        copy=False,
        lang=body.lang,
        department=department,
        role=role,
    )
    return _doc_out(document)


@app.post("/api/transcribe")
async def transcribe_question(
    file: UploadFile = File(...),
    lang: str = Query(default="tr"),
    x_omnivault_role: Optional[str] = Header(default="admin"),
) -> Dict[str, Any]:
    _role(x_omnivault_role)
    staging = STATE.settings.data_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "question.webm").suffix.lower() or ".webm"
    target = staging / f"question_{int(time.time() * 1000)}{suffix}"
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty recording")
    target.write_bytes(data)
    try:
        extraction = STATE.whisper.transcribe(
            target,
            doc_id=f"q-{int(time.time())}",
            filename=target.name,
            transcript_dir=STATE.settings.transcripts_dir,
            language="en" if str(lang).lower().startswith("en") else "tr",
        )
    except Exception as exc:
        logger.exception("Question transcription failed")
        raise HTTPException(500, f"Transcription failed: {exc}") from exc
    finally:
        target.unlink(missing_ok=True)
    return {"text": extraction.full_text.strip(), "language": extraction.language}


@app.get("/api/revisions")
def list_revisions(x_omnivault_role: Optional[str] = Header(default="admin")) -> List[Dict[str, Any]]:
    if not can_admin(_role(x_omnivault_role)):
        raise HTTPException(403, "Only an administrator can view the change history.")
    rows = STATE.store.list_revisions(limit=40)
    for row in rows:
        detail = row.get("detail") or {}
        if not detail.get("chunk_count"):
            document = STATE.store.get_document(str(row.get("doc_id") or ""))
            if document:
                detail["chunk_count"] = int(document.get("chunk_count") or 0)
                row["detail"] = detail
    return rows


@app.delete("/api/vectors/{doc_id}")
def purge_vectors(doc_id: str, x_omnivault_role: Optional[str] = Header(default="admin")) -> Dict[str, Any]:
    if not can_admin(_role(x_omnivault_role)):
        raise HTTPException(403, "Only an administrator can remove vectors.")
    document = STATE.store.get_document(doc_id)
    if not document:
        STATE.vectors.delete_document(doc_id)
        return {"ok": True, "doc_id": doc_id, "removed": False}
    STATE.pipeline.delete_document(doc_id)
    return {"ok": True, "doc_id": doc_id, "removed": True}


@app.post("/api/revisions/{revision_id}/rollback")
def rollback_revision(
    revision_id: int,
    x_omnivault_role: Optional[str] = Header(default="admin"),
) -> Dict[str, Any]:
    if not can_admin(_role(x_omnivault_role)):
        raise HTTPException(403, "Only an administrator can roll back a change.")
    revision = STATE.store.get_revision(revision_id)
    if not revision:
        raise HTTPException(404, "Revision not found")
    action = revision["action"]
    if action == "ingested":
        document = STATE.store.get_document(revision["doc_id"])
        if document:
            STATE.pipeline.delete_document(revision["doc_id"])
        STATE.store.audit("revision.rollback", {"id": revision_id, "action": action})
        return {"ok": True, "action": "removed", "revision_id": revision_id}
    if action == "deleted":
        restored = STATE.pipeline.restore_deleted(revision)
        if not restored:
            raise HTTPException(409, "The original file is no longer in the local trash.")
        STATE.store.audit("revision.rollback", {"id": revision_id, "action": action, "restored": restored["id"]})
        return {"ok": True, "action": "restored", "document": restored}
    raise HTTPException(400, f"Cannot roll back action '{action}'")


@app.get("/api/audit", response_model=List[AuditEntry])
def audit_log(limit: int = Query(default=100, ge=1, le=500)) -> List[AuditEntry]:
    return [AuditEntry.model_validate(entry) for entry in STATE.store.list_audit(limit=limit)]


@app.get("/api/events")
async def event_stream() -> StreamingResponse:
    async def generator() -> AsyncIterator[str]:
        async with STATE.events.subscribe() as queue:
            yield _sse("hello", {"version": __version__, "ts": time.time()})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield _sse(message["event"], message["data"])
                except asyncio.TimeoutError:
                    yield _sse("ping", {"ts": time.time()})

    return StreamingResponse(generator(), media_type="text/event-stream")


def _sse(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def mimetypes_guess(path: Path) -> str:
    import mimetypes

    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


@app.get("/api/system")
def system_snapshot() -> Dict[str, Any]:
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(str(STATE.settings.data_dir))
    return {
        "ram": {
            "total_gb": round(memory.total / (1024**3), 2),
            "available_gb": round(memory.available / (1024**3), 2),
            "percent": memory.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": round((disk.used / disk.total) * 100, 1),
        },
        "ollama_loaded": STATE.ollama.loaded_models(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "data_dir": str(STATE.settings.data_dir),
    }


# Serve the polished SPA when a production build is present.
_frontend = get_settings().frontend_dist
if _frontend.exists():
    app.mount("/assets", StaticFiles(directory=str(_frontend / "assets")), name="assets")

    @app.get("/")
    async def spa_index():
        return FileResponse(_frontend / "index.html")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "Not found")
        candidate = _frontend / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_frontend / "index.html")
else:

    @app.get("/")
    async def root_message():
        return JSONResponse(
            {
                "name": "OmniVault",
                "version": __version__,
                "message": "API is online. Build the frontend (cd frontend && npm run build) or run the Vite dev server.",
                "docs": "/docs",
            }
        )
