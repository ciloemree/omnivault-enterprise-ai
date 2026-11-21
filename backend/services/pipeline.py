"""Single-worker ingestion pipeline: extract → embed → brief → ready."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.config import Settings
from backend.core.events import EventBus
from backend.core.ollama import OllamaClient
from backend.core.store import Store
from backend.core.vectorstore import VectorStore
from backend.ingest.audio import WhisperEngine, cloud_transcription_allowed
from backend.ingest.documents import extract_document
from backend.ingest.images import extract_image
from backend.core.chunking import chunk_text
from backend.services.briefing import briefing_from_vision, generate_briefing
from backend.services.suggestions import questions_from_content
from backend.services.table_agent import is_tabular_path, load_frames, profile_frames

logger = logging.getLogger("omnivault.pipeline")


def _uploader() -> Dict[str, str]:
    try:
        from backend.main import CURRENT_USER

        user = CURRENT_USER.get() or {}
    except Exception:
        user = {}
    return {
        "uploaded_by_username": str(user.get("username") or ""),
        "uploaded_by_name": str(user.get("full_name") or "Kurumsal Arşiv (Genel)"),
    }

PDF_TYPES = {".pdf"}
TEXT_TYPES = {".txt", ".md", ".markdown"}
OFFICE_TYPES = {".csv", ".xlsx", ".xls", ".docx"}
AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".webm"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


_PERSON = re.compile(
    r"ad[ıi]m\s*:\s*([A-Za-zÇĞİÖŞÜçğıöşü]{3,})\s+soyad[ıi]m\s*:\s*([A-Za-zÇĞİÖŞÜçğıöşü]{3,})",
    re.IGNORECASE,
)


def _fold_name(token: str) -> str:
    return token.replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")


def person_key(text: str) -> Optional[str]:
    match = _PERSON.search(text or "")
    if not match:
        return None
    return f"{_fold_name(match.group(1))}|{_fold_name(match.group(2))}"


class NameCollision(Exception):
    """An active document in the same department already uses this name or filename."""

    def __init__(self, payload: Dict[str, Any]) -> None:
        super().__init__("conflict_detected")
        self.payload = payload


_AGE = re.compile(r"(?:ya[sş]\s*[:\-]?\s*(\d{1,3})|(\d{1,3})\s*ya[sş])", re.IGNORECASE)
_ROLE = re.compile(r"(?:görev|unvan|pozisyon|rol(?:ü|u)?|title)\s*[:\-]\s*(.+)", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_IDENT = re.compile(r"(?:tckn|tc\s*kimlik|kimlik\s*no|sicil(?:\s*no)?)\s*[:\-]\s*(\d{5,11})", re.IGNORECASE)
_MONEY = re.compile(r"(\d+(?:[.\s]\d{3})*|\d+)\s*(bin)?\s*tl\b", re.IGNORECASE)
_DEPT_LINE = re.compile(r"(?:department|departman)\s*[:\-]\s*([^\n]+)", re.IGNORECASE)
_HEADING_NAME = re.compile(r"^#\s+([^\n#]{3,80})$", re.MULTILINE)


def _fold_phrase(value: str) -> str:
    return " ".join(_fold_name(part) for part in re.split(r"\s+", (value or "").strip()) if part)


def _names_in_text(text: str, filename: str = "") -> set[str]:
    found: set[str] = set()
    match = _PERSON.search(text or "")
    if match:
        found.add(_fold_phrase(f"{match.group(1)} {match.group(2)}"))
    heading = _HEADING_NAME.search(text or "")
    if heading:
        title = heading.group(1).strip()
        if 2 <= len(title.split()) <= 4:
            found.add(_fold_phrase(title))
    stem = Path(filename).stem.replace("-", " ").replace("_", " ")
    folded = _fold_phrase(stem)
    if len(folded.split()) >= 2:
        found.add(folded)
    return {item for item in found if item}


def _first_age(text: str) -> Optional[int]:
    match = _AGE.search(text or "")
    if not match:
        return None
    try:
        value = int(match.group(1) or match.group(2))
    except (TypeError, ValueError):
        return None
    return value if 0 < value < 120 else None


def _record_fields(text: str) -> Dict[str, str]:
    role = _ROLE.search(text or "")
    email = _EMAIL.search(text or "")
    ident = _IDENT.search(text or "")
    money = _MONEY.search(text or "")
    department = _DEPT_LINE.search(text or "")
    age = _first_age(text or "")
    amount = ""
    if money:
        number = money.group(1).replace(" ", "")
        amount = f"{number}{' bin' if money.group(2) else ''} TL"
    return {
        "age": str(age) if age else "",
        "role": (role.group(1).strip() if role else "")[:80],
        "amount": amount,
        "email": email.group(0) if email else "",
        "ident": ident.group(1) if ident else "",
        "department": department.group(1).strip()[:80] if department else "",
    }


def _namesake(left: Dict[str, str], right: Dict[str, str]) -> bool:
    if left.get("age") and right.get("age"):
        try:
            if abs(int(left["age"]) - int(right["age"])) > 2:
                return True
        except ValueError:
            pass
    if left.get("role") and right.get("role") and _fold_phrase(left["role"]) != _fold_phrase(right["role"]):
        return True
    if left.get("email") and right.get("email") and left["email"].lower() != right["email"].lower():
        return True
    if left.get("ident") and right.get("ident") and left["ident"] != right["ident"]:
        return True
    if left.get("department") and right.get("department") and _fold_phrase(left["department"]) != _fold_phrase(right["department"]):
        return True
    return False


def _preview_text(path: Path) -> str:
    if path.suffix.lower() not in {".md", ".txt", ".markdown", ".text"}:
        return ""
    try:
        if not path.exists() or path.stat().st_size > 200_000:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def classify_path(path: Path) -> Tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in PDF_TYPES:
        return "pdf", "pdf"
    if suffix in TEXT_TYPES:
        return "text", suffix.lstrip(".") or "txt"
    if suffix in OFFICE_TYPES:
        return "office", suffix.lstrip(".") or "office"
    if suffix in AUDIO_TYPES:
        return "audio", suffix.lstrip(".") or "audio"
    if suffix in IMAGE_TYPES:
        return "image", suffix.lstrip(".") or "image"
    raise ValueError(f"Unsupported file type: {suffix or path.name}")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


class IngestionPipeline:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        vectors: VectorStore,
        ollama: OllamaClient,
        whisper: WhisperEngine,
        events: EventBus,
    ) -> None:
        self.settings = settings
        self.store = store
        self.vectors = vectors
        self.ollama = ollama
        self.whisper = whisper
        self.events = events
        self._queue: list[str] = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="omnivault-ingest", daemon=True)
        self._worker.start()
        # Resume anything left mid-flight after a crash/restart.
        for document in self.store.documents_with_status(("queued", "processing", "indexing", "briefing")):
            self.enqueue(document["id"])

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def enqueue(self, doc_id: str) -> None:
        with self._lock:
            if doc_id not in self._queue:
                self._queue.append(doc_id)
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            doc_id = None
            with self._lock:
                if self._queue:
                    doc_id = self._queue.pop(0)
            if not doc_id:
                self._wake.wait(timeout=0.5)
                self._wake.clear()
                continue
            try:
                self._process(doc_id)
            except Exception as exc:
                logger.exception("Ingestion failed for %s", doc_id)
                self._fail(doc_id, str(exc))

    def register_file(
        self,
        source_path: Path,
        *,
        filename: Optional[str] = None,
        origin: str = "upload",
        copy: bool = True,
        lang: str = "en",
        department: str = "public",
        role: str = "admin",
        engine: str = "local",
        resolution: str = "",
    ) -> Dict[str, Any]:
        filename = filename or source_path.name
        modality, file_type = classify_path(Path(filename))
        digest = sha256_file(source_path)
        preview = _preview_text(source_path)
        choice = (resolution or "").strip().lower()
        target = None if choice == "separate" else self._version_target(department or "public", filename, preview)
        if target and target.get("sha256") == digest and target.get("status") == "ready":
            if not copy and source_path.exists():
                source_path.unlink()
            return target
        if target and choice == "update":
            return self._replace_version(
                target,
                source_path,
                filename=filename,
                modality=modality,
                file_type=file_type,
                digest=digest,
                origin=origin,
                copy=copy,
                lang=lang,
                department=department or "public",
                role=role,
                engine=engine,
            )
        if target:
            raise NameCollision(self._conflict_payload(target, filename, preview))
        existing = self.store.find_by_sha(digest)
        if existing and existing.get("status") == "ready":
            duplicate = self.store.create_document(
                id=str(uuid.uuid4()),
                filename=filename,
                modality=modality,
                file_type=file_type,
                mime=mimetypes.guess_type(filename)[0],
                size_bytes=source_path.stat().st_size,
                sha256=digest,
                stored_path=existing["stored_path"],
                origin=origin,
                status="duplicate",
                stage="done",
                progress=1.0,
                message=f"Identical to existing document {existing['id'][:8]}…",
                chunk_count=existing.get("chunk_count") or 0,
                embed_model=existing.get("embed_model"),
                meta={"duplicate_of": existing["id"], **(existing.get("meta") or {})},
                briefing=existing.get("briefing"),
                privacy=existing.get("privacy"),
                indexed_at=existing.get("indexed_at"),
            )
            self.store.audit("document.duplicate", {"id": duplicate["id"], "of": existing["id"], "filename": filename})
            self.events.publish("document", duplicate)
            return duplicate

        doc_id = str(uuid.uuid4())
        dest_dir = self.settings.uploads_dir / doc_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(filename).name
        if copy:
            shutil.copy2(source_path, dest)
        else:
            shutil.move(str(source_path), str(dest))

        document = self.store.create_document(
            id=doc_id,
            filename=filename,
            modality=modality,
            file_type=file_type,
            mime=mimetypes.guess_type(filename)[0],
            size_bytes=dest.stat().st_size,
            sha256=digest,
            stored_path=str(dest),
            origin=origin,
            status="queued",
            stage="queued",
            progress=0.02,
            message="Queued for multimodal ingestion",
            meta={
                "lang": "tr" if str(lang).lower().startswith("tr") else "en",
                "answer_engine": (engine or "local").strip().lower() or "local",
                "department": department or "public",
                "role": role,
                "version": 1,
                "version_at": time.time(),
                **_uploader(),
            },
        )
        self.store.add_revision(
            doc_id=doc_id,
            action="ingested",
            filename=filename,
            department=department or "public",
            detail={"origin": origin, "modality": modality, "stored_path": str(dest), "role": role, "chunk_count": 0},
        )
        self.store.audit("document.queued", {"id": doc_id, "filename": filename, "modality": modality, "origin": origin, "department": department})
        self.events.publish("document", document)
        self.enqueue(doc_id)
        return document

    def _document_names(self, document: Dict[str, Any]) -> set[str]:
        text = _preview_text(Path(str(document.get("stored_path") or "")))
        names = _names_in_text(text, str(document.get("filename") or ""))
        for entity in (document.get("briefing") or {}).get("entities") or []:
            folded = _fold_phrase(str(entity))
            if len(folded.split()) >= 2:
                names.add(folded)
        return names

    def _version_target(self, department: str, filename: str, preview: str) -> Optional[Dict[str, Any]]:
        """An active document in this department with the same filename or entity name."""
        incoming = _names_in_text(preview, filename)
        name_match: Optional[Dict[str, Any]] = None
        for document in self.store.list_documents():
            if document.get("status") == "duplicate":
                continue
            owner = str((document.get("meta") or {}).get("department") or "public")
            if owner != (department or "public"):
                continue
            if document.get("filename") == filename:
                return document
            if incoming and incoming & self._document_names(document):
                if name_match is None or float(document.get("updated_at") or 0) > float(name_match.get("updated_at") or 0):
                    name_match = document
        return name_match

    def _conflict_payload(self, existing: Dict[str, Any], filename: str, incoming_text: str) -> Dict[str, Any]:
        current = _preview_text(Path(str(existing.get("stored_path") or "")))
        if not current:
            current = str((existing.get("meta") or {}).get("edited_text") or "")
        left = _record_fields(current)
        right = _record_fields(incoming_text)
        fields = ("age", "role", "amount")
        return {
            "conflict_detected": True,
            "namesake": _namesake(left, right),
            "existing": {
                "id": existing.get("id"),
                "filename": existing.get("filename") or "",
                "version": int((existing.get("meta") or {}).get("version") or 1),
                "fields": {key: left.get(key) or "" for key in fields},
                "text": current[:4000],
            },
            "incoming": {
                "filename": filename,
                "fields": {key: right.get(key) or "" for key in fields},
                "text": (incoming_text or "")[:4000],
            },
        }

    def _remember_version(self, document: Dict[str, Any]) -> None:
        text = _preview_text(Path(str(document.get("stored_path") or "")))
        if not text:
            text = str((document.get("meta") or {}).get("edited_text") or "")
        if not text.strip():
            return
        self.store.save_document_version(
            doc_id=str(document.get("id") or ""),
            version=int((document.get("meta") or {}).get("version") or 1),
            filename=str(document.get("filename") or ""),
            body=text,
        )

    def _replace_version(
        self,
        existing: Dict[str, Any],
        source_path: Path,
        *,
        filename: str,
        modality: str,
        file_type: str,
        digest: str,
        origin: str,
        copy: bool,
        lang: str,
        department: str,
        role: str,
        engine: str = "local",
    ) -> Dict[str, Any]:
        doc_id = str(existing["id"])
        self._remember_version(existing)
        self.vectors.delete_document(doc_id)
        dest_dir = self.settings.uploads_dir / doc_id
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(filename).name
        if copy:
            shutil.copy2(source_path, dest)
        else:
            shutil.move(str(source_path), str(dest))
        version = int((existing.get("meta") or {}).get("version") or 1) + 1
        now = time.time()
        meta = dict(existing.get("meta") or {})
        meta.update(
            {
                "lang": "tr" if str(lang).lower().startswith("tr") else "en",
                "answer_engine": (engine or "local").strip().lower() or "local",
                "department": department or "public",
                "role": role,
                "version": version,
                "version_at": now,
                "replaced": True,
                **_uploader(),
            }
        )
        document = self.store.update_document(
            doc_id,
            filename=filename,
            modality=modality,
            file_type=file_type,
            mime=mimetypes.guess_type(filename)[0],
            size_bytes=dest.stat().st_size,
            sha256=digest,
            stored_path=str(dest),
            origin=origin,
            status="queued",
            stage="queued",
            progress=0.02,
            message="Queued as a newer version",
            error=None,
            chunk_count=0,
            briefing=None,
            meta=meta,
            indexed_at=None,
        )
        self.store.add_revision(
            doc_id=doc_id,
            action="versioned",
            filename=filename,
            department=department or "public",
            detail={"version": version, "origin": origin},
        )
        self.events.publish("document", document)
        self.enqueue(doc_id)
        return document or existing

    def transfer_document(self, doc_id: str, target_department: str, mode: str) -> Dict[str, Any]:
        """Move or copy a ready document without OCR, Whisper, or a new embedding pass."""
        document = self.store.get_document(doc_id)
        if not document:
            raise ValueError("Document not found.")
        if document.get("status") == "duplicate":
            raise ValueError("A duplicate placeholder cannot be transferred.")
        mode = (mode or "").strip().lower()
        if mode not in {"move", "copy"}:
            raise ValueError("Mode must be move or copy.")
        target = (target_department or "").strip().lower()
        known = {str(item.get("slug") or "") for item in self.store.list_departments()}
        if target not in known:
            raise ValueError("That department does not exist.")
        source = str((document.get("meta") or {}).get("department") or "public")
        if target == source:
            raise ValueError("The document is already in that department.")
        if mode == "move":
            return self._move_document(document, target)
        return self._copy_document(document, target)

    def _move_document(self, document: Dict[str, Any], target: str) -> Dict[str, Any]:
        doc_id = str(document["id"])
        self._rewrite_stored_department(Path(str(document.get("stored_path") or "")), target)
        moved = self.vectors.set_document_department(doc_id, target)
        meta = dict(document.get("meta") or {})
        meta["department"] = target
        updated = self.store.update_document(doc_id, meta=meta) or document
        self.store.add_revision(
            doc_id=doc_id,
            action="moved",
            filename=str(document.get("filename") or doc_id),
            department=target,
            detail={"from": (document.get("meta") or {}).get("department") or "", "vectors": moved},
        )
        self.events.publish("document", updated)
        return updated

    def _copy_document(self, document: Dict[str, Any], target: str) -> Dict[str, Any]:
        source_id = str(document["id"])
        doc_id = str(uuid.uuid4())
        stored = Path(str(document.get("stored_path") or ""))
        dest_dir = self.settings.uploads_dir / doc_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (stored.name or f"{doc_id}.bin")
        if stored.exists():
            shutil.copy2(stored, dest)
            self._rewrite_stored_department(dest, target)
        else:
            dest.write_text("", encoding="utf-8")
        meta = dict(document.get("meta") or {})
        meta["department"] = target
        meta["copied_from"] = source_id
        meta["version"] = 1
        meta["version_at"] = time.time()
        meta.pop("replaced", None)
        clone = self.store.create_document(
            id=doc_id,
            filename=document.get("filename") or dest.name,
            modality=document.get("modality") or "text",
            file_type=document.get("file_type") or "txt",
            mime=document.get("mime"),
            size_bytes=dest.stat().st_size if dest.exists() else 0,
            sha256=document.get("sha256") or sha256_file(dest),
            stored_path=str(dest),
            origin="copy",
            status=document.get("status") or "ready",
            stage="ready",
            progress=1.0,
            message="Copied into another department",
            chunk_count=0,
            embed_model=document.get("embed_model"),
            meta=meta,
            briefing=document.get("briefing"),
            privacy=document.get("privacy"),
            indexed_at=document.get("indexed_at") or time.time(),
        )
        copied = self.vectors.clone_document_vectors(source_id, doc_id, target)
        clone = self.store.update_document(doc_id, chunk_count=copied) or clone
        self.store.add_revision(
            doc_id=doc_id,
            action="copied",
            filename=str(clone.get("filename") or doc_id),
            department=target,
            detail={"from": source_id, "vectors": copied},
        )
        self.events.publish("document", clone)
        return clone

    @staticmethod
    def _rewrite_stored_department(path: Path, department: str) -> None:
        if path.suffix.lower() not in {".md", ".txt", ".markdown"} or not path.exists():
            return
        if path.stat().st_size > 200_000:
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        updated = re.sub(r"(?im)^(Department:\s*)\S+", rf"\1{department}", text, count=1)
        if updated != text:
            path.write_text(updated, encoding="utf-8")

    def delete_document(self, doc_id: str) -> bool:
        document = self.store.get_document(doc_id)
        if not document:
            return False
        department = str((document.get("meta") or {}).get("department") or "public")
        trash_dir = self.settings.data_dir / "trash" / doc_id
        stored = Path(document["stored_path"])
        upload_dir = stored.parent if stored.exists() else self.settings.uploads_dir / doc_id
        if upload_dir.exists() and upload_dir.is_dir() and str(self.settings.uploads_dir) in str(upload_dir.resolve()):
            trash_dir.parent.mkdir(parents=True, exist_ok=True)
            if trash_dir.exists():
                shutil.rmtree(trash_dir, ignore_errors=True)
            shutil.move(str(upload_dir), str(trash_dir))
        self.vectors.delete_document(doc_id)
        self.store.add_revision(
            doc_id=doc_id,
            action="deleted",
            filename=document.get("filename") or doc_id,
            department=department,
            detail={
                "trash_dir": str(trash_dir) if trash_dir.exists() else "",
                "modality": document.get("modality"),
                "lang": (document.get("meta") or {}).get("lang") or "en",
                "role": (document.get("meta") or {}).get("role") or "admin",
                "chunk_count": document.get("chunk_count") or 0,
            },
        )
        for auxiliary in (
            self.settings.thumbnails_dir / f"{doc_id}.jpg",
            self.settings.transcripts_dir / f"{doc_id}.json",
            self.settings.analysis_dir / f"{doc_id}.md",
        ):
            try:
                auxiliary.unlink(missing_ok=True)
            except Exception:
                pass
        self.store.delete_document(doc_id)
        self.store.audit("document.deleted", {"id": doc_id, "filename": document.get("filename")})
        self.events.publish("document.deleted", {"id": doc_id})
        return True

    def _runtime_models(self) -> Dict[str, str]:
        persisted = self.store.get_settings()
        chat = persisted.get("chat_model") or self.settings.chat_model or self.ollama.select_chat_model() or ""
        vision = persisted.get("vision_model") or self.settings.vision_model or self.ollama.select_vision_model() or ""
        embed = persisted.get("embed_model") or self.settings.embed_model or self.ollama.select_embed_model() or ""
        return {"chat": chat, "vision": vision, "embed": embed}

    def _version_moved(self, doc_id: str, seen_version: str) -> bool:
        current = self.store.get_document(doc_id) or {}
        return str((current.get("meta") or {}).get("version_at") or "") != seen_version

    def _update(self, doc_id: str, **fields: Any) -> Dict[str, Any]:
        document = self.store.update_document(doc_id, **fields)
        if document:
            self.events.publish("document", document)
        return document or {}

    def _fail(self, doc_id: str, error: str) -> None:
        self._update(
            doc_id,
            status="error",
            stage="error",
            progress=1.0,
            message="Ingestion failed",
            error=error[:2000],
        )
        self.store.audit("document.error", {"id": doc_id, "error": error[:500]})

    def _progress(self, doc_id: str, stage: str, value: float, message: str) -> None:
        self._update(
            doc_id,
            status="processing" if stage not in {"indexing", "briefing"} else stage,
            stage=stage,
            progress=max(0.0, min(0.99, value)),
            message=message,
            error=None,
        )

    def _index_table(self, doc_id: str, document: Dict[str, Any], path: Path, seen_version: str) -> None:
        """Keep CSV and Excel as data frames. They are not chunked or embedded."""
        try:
            profile = profile_frames(load_frames(path))
        except Exception as exc:
            self._fail(doc_id, f"Spreadsheet could not be opened: {exc}")
            return
        self.vectors.delete_document(doc_id)
        if self._version_moved(doc_id, seen_version):
            return
        columns = [str(item) for item in profile["columns"]]
        rows = int(profile["row_count"])
        sheets = profile["sheets"]
        sheet_names = ", ".join(str(sheet.get("name") or "") for sheet in sheets[:6])
        summary = [
            f"{rows} satır, {len(columns)} sütun.",
            f"Sütunlar: {', '.join(columns[:12])}." if columns else "Sütun başlığı okunamadı.",
            f"Sayfalar: {sheet_names}." if sheet_names else "Tek çalışma sayfası.",
        ]
        briefing = {
            "summary": summary,
            "tags": ["tablo", path.suffix.lower().lstrip(".")],
            "entities": [],
            "risk_flags": [],
            "source": "table-agent",
        }
        current = self.store.get_document(doc_id) or document
        ready = self._update(
            doc_id,
            status="ready",
            stage="ready",
            progress=1.0,
            message="Loaded for the table agent",
            error=None,
            chunk_count=0,
            embed_model="table-agent",
            meta={
                **(current.get("meta") or {}),
                "table_agent": True,
                "columns": columns,
                "row_count": rows,
                "sheets": sheets,
                "has_tables": True,
            },
            briefing=briefing,
            indexed_at=time.time(),
        )
        self.store.annotate_revision(doc_id, "ingested", {"chunk_count": 0, "table_agent": True})
        self.store.audit(
            "document.ready",
            {"id": doc_id, "filename": document.get("filename"), "chunks": 0, "modality": "table"},
        )
        self.events.publish("document", ready)

    def _process(self, doc_id: str) -> None:
        document = self.store.get_document(doc_id)
        if not document:
            return
        if document.get("status") in {"ready", "duplicate", "error"} and document.get("status") != "queued":
            # Allow explicit re-queue of error/queued only.
            if document.get("status") != "queued":
                return

        seen_version = str((document.get("meta") or {}).get("version_at") or "")
        path = Path(document["stored_path"])
        if not path.exists():
            self._fail(doc_id, f"Stored file missing: {path}")
            return

        if is_tabular_path(path):
            self._index_table(doc_id, document, path, seen_version)
            return

        models = self._runtime_models()
        if not models["embed"]:
            self._fail(doc_id, "No embedding model available. Pull nomic-embed-text in Ollama.")
            return

        self.vectors.embeddings.set_model(models["embed"])
        modality = document["modality"]
        filename = document["filename"]
        self._progress(doc_id, "extract", 0.08, f"Starting {modality} extraction")

        chunks = []
        meta_patch: Dict[str, Any] = {}
        briefing_source = ""
        image_caption = None

        def progress(stage: str, value: float, message: str) -> None:
            self._progress(doc_id, stage, value, message)

        if modality in {"pdf", "text", "office"}:
            extraction = extract_document(
                path,
                doc_id=doc_id,
                filename=filename,
                chunk_size=self.settings.chunk_size,
                chunk_overlap=self.settings.chunk_overlap,
                ocr_limit=self.settings.scanned_page_ocr_limit,
                progress=progress,
            )
            chunks = [{"text": c.text, "metadata": c.metadata} for c in extraction.chunks]
            briefing_source = "\n\n".join(c.text for c in extraction.chunks[:12])
            meta_patch.update(
                {
                    "page_count": extraction.page_count,
                    "title": extraction.title,
                    "has_tables": extraction.has_tables,
                    "ocr_pages": extraction.ocr_pages,
                    "char_count": extraction.char_count,
                }
            )
        elif modality == "audio":
            # Free Ollama RAM before loading Whisper on Apple Silicon.
            for resident in self.ollama.loaded_models():
                name = resident.get("name") or ""
                if name and "embed" not in name.lower():
                    self.ollama.unload(name)
            spoken = str((document.get("meta") or {}).get("lang") or "")
            answer_engine = str((document.get("meta") or {}).get("answer_engine") or "local")
            extraction = self.whisper.transcribe(
                path,
                doc_id=doc_id,
                filename=filename,
                transcript_dir=self.settings.transcripts_dir,
                progress=progress,
                language="tr" if spoken.startswith("tr") else ("en" if spoken.startswith("en") else None),
                allow_cloud=cloud_transcription_allowed(answer_engine),
            )
            chunks = extraction.chunks
            briefing_source = extraction.full_text
            meta_patch.update(
                {
                    "duration": extraction.duration,
                    "language": extraction.language,
                    "transcript_path": extraction.transcript_path,
                    "transcript_engine": extraction.engine,
                }
            )
        elif modality == "image":
            if not models["vision"]:
                logger.warning("No vision model; OCR-only path for %s", filename)
            from backend.services.llm_router import google_api_key

            extraction = extract_image(
                path,
                doc_id=doc_id,
                filename=filename,
                ollama=self.ollama,
                vision_model=models["vision"],
                gemini_key=google_api_key(self.store, self.settings),
                thumbnails_dir=self.settings.thumbnails_dir,
                analysis_dir=self.settings.analysis_dir,
                max_side=self.settings.vision_max_side,
                progress=progress,
            )
            chunks = extraction.chunks
            briefing_source = "\n\n".join(c["text"] for c in chunks)
            image_caption = (extraction.description, extraction.ocr_text)
            meta_patch.update(
                {
                    "width": extraction.width,
                    "height": extraction.height,
                    "thumbnail_path": extraction.thumbnail_path,
                    "analysis_path": extraction.analysis_path,
                    "vision_model": extraction.vision_model,
                    "ocr_engine": extraction.ocr_engine,
                    "ocr_chars": len(extraction.ocr_text or ""),
                }
            )
        else:
            self._fail(doc_id, f"Unsupported modality: {modality}")
            return

        if not chunks:
            fallback = ""
            stored = Path(document.get("stored_path") or "")
            if stored.exists() and stored.suffix.lower() in {".md", ".txt", ".markdown"}:
                fallback = stored.read_text(encoding="utf-8", errors="ignore").strip()
            if fallback:
                chunks = [{"text": fallback, "metadata": {"modality": modality}}]
            else:
                self._fail(doc_id, "No extractable content found in this file.")
                return

        # Stable chunk IDs keep re-indexing idempotent.
        for index, chunk in enumerate(chunks):
            chunk["id"] = f"{doc_id}:{index}"
            chunk["metadata"] = {
                **chunk.get("metadata", {}),
                "doc_id": doc_id,
                "source": filename,
                "modality": modality if modality != "text" else chunk.get("metadata", {}).get("modality", "text"),
                "department": str((document.get("meta") or {}).get("department") or "public"),
            }

        if self._version_moved(doc_id, seen_version):
            return
        self._progress(doc_id, "indexing", 0.78, f"Embedding {len(chunks)} chunks with {models['embed']}")
        self.vectors.delete_document(doc_id)
        self.vectors.upsert_chunks(chunks)

        self._progress(doc_id, "briefing", 0.9, "Generating executive briefing")
        persisted = self.store.get_settings()
        language = str((document.get("meta") or {}).get("lang") or persisted.get("lang") or "en")
        if image_caption is not None:
            briefing = briefing_from_vision(
                description=image_caption[0],
                ocr_text=image_caption[1],
                filename=filename,
            )
        else:
            briefing = generate_briefing(
                ollama=self.ollama,
                chat_model=models["chat"],
                filename=filename,
                modality=modality,
                text=briefing_source,
                num_ctx=self.settings.chat_num_ctx,
                keep_alive=self.settings.chat_keep_alive,
                privacy_shield=bool(persisted.get("privacy_shield", self.settings.privacy_shield)),
                lang=language,
            )

        questions = questions_from_content(
            briefing_source,
            [str(item) for item in (briefing.get("entities") or [])],
        )
        question_meta = {
            "suggested_questions": questions["tr"],
            "suggested_questions_tr": questions["tr"],
            "suggested_questions_en": questions["en"],
        }
        current = self.store.get_document(doc_id) or document
        if str((current.get("meta") or {}).get("version_at") or "") != seen_version:
            return
        ready = self._update(
            doc_id,
            status="ready",
            stage="ready",
            progress=1.0,
            message="Indexed into OmniVault memory",
            error=None,
            chunk_count=len(chunks),
            embed_model=models["embed"],
            meta={**(current.get("meta") or {}), **meta_patch, **question_meta},
            briefing=briefing,
            indexed_at=time.time(),
        )
        self.store.annotate_revision(doc_id, "ingested", {"chunk_count": len(chunks)})
        self.store.audit(
            "document.ready",
            {"id": doc_id, "filename": filename, "chunks": len(chunks), "modality": modality},
        )
        self.events.publish("document", ready)

    def replace_text(self, doc_id: str, text: str) -> Optional[Dict[str, Any]]:
        document = self.store.get_document(doc_id)
        if not document:
            return None
        path = Path(str(document.get("stored_path") or ""))
        if is_tabular_path(path):
            return document
        self._remember_version(document)
        if path.suffix.lower() in {".md", ".txt", ".markdown"} and path.parent.exists():
            try:
                path.write_text(text, encoding="utf-8")
            except OSError:
                pass
        pieces = chunk_text(text, chunk_size=self.settings.chunk_size, chunk_overlap=self.settings.chunk_overlap)
        if not pieces:
            return document
        department = str((document.get("meta") or {}).get("department") or "public")
        chunks = []
        for index, piece in enumerate(pieces):
            chunks.append(
                {
                    "id": f"{doc_id}:{index}",
                    "text": piece,
                    "metadata": {
                        "doc_id": doc_id,
                        "source": document.get("filename"),
                        "modality": document.get("modality") or "text",
                        "department": department,
                        "chunk_index": index,
                    },
                }
            )
        self.vectors.delete_document(doc_id)
        models = self._runtime_models()
        embed_model = models.get("embed") or document.get("embed_model") or ""
        self.vectors.upsert_chunks(chunks)
        language = str((document.get("meta") or {}).get("lang") or "tr")
        modality_name = str(document.get("modality") or "text")
        if modality_name == "image":
            briefing = briefing_from_vision(description=text, ocr_text="", filename=str(document.get("filename") or "image"))
        else:
            briefing = generate_briefing(
                ollama=self.ollama,
                chat_model=models["chat"],
                filename=str(document.get("filename") or "note"),
                modality=modality_name,
                text=text,
                lang=language,
            )
        questions = questions_from_content(text, [str(item) for item in (briefing.get("entities") or [])])
        version = int((document.get("meta") or {}).get("version") or 1) + 1
        meta = {
            **(document.get("meta") or {}),
            "suggested_questions": questions["tr"],
            "suggested_questions_tr": questions["tr"],
            "suggested_questions_en": questions["en"],
            "edited_text": text[:50000],
            "version": version,
            "version_at": time.time(),
        }
        updated = self._update(
            doc_id,
            chunk_count=len(chunks),
            embed_model=embed_model,
            briefing=briefing,
            meta=meta,
            message="Text updated",
            status="ready",
            stage="ready",
            progress=1.0,
        )
        if updated:
            self.events.publish("document", updated)
        return updated

    def rebrief(self, doc_id: str, lang: str) -> Optional[Dict[str, Any]]:
        """Regenerate only the executive briefing in the requested language."""
        document = self.store.get_document(doc_id)
        if not document or document.get("status") not in {"ready", "duplicate"}:
            return document
        source = self.vectors.document_text(doc_id)
        if not source:
            return document
        models = self._runtime_models()
        persisted = self.store.get_settings()
        language = "tr" if str(lang).lower().startswith("tr") else "en"
        if document.get("modality") == "image":
            briefing = briefing_from_vision(description=source, ocr_text="", filename=document["filename"])
        else:
            briefing = generate_briefing(
                ollama=self.ollama,
                chat_model=models["chat"],
                filename=document["filename"],
                modality=document["modality"],
                text=source,
                num_ctx=self.settings.chat_num_ctx,
                keep_alive=self.settings.chat_keep_alive,
                privacy_shield=bool(persisted.get("privacy_shield", self.settings.privacy_shield)),
                lang=language,
            )
        meta = dict(document.get("meta") or {})
        meta["lang"] = language
        updated = self._update(doc_id, briefing=briefing, meta=meta, message="Briefing refreshed")
        self.store.audit("document.rebrief", {"id": doc_id, "lang": language})
        return updated

    def purge_origin(self, origin: str) -> int:
        removed = 0
        for document in list(self.store.list_documents()):
            if document.get("origin") == origin:
                if self.delete_document(document["id"]):
                    removed += 1
        return removed

    def repair_short_note_briefings(self) -> None:
        """Replace model commentary on short notes with the facts written in the note."""
        from backend.services.briefing import _note_body, factual_briefing

        for document in self.store.list_documents():
            if document.get("status") != "ready" or document.get("modality") not in {"text", "office"}:
                continue
            path = Path(str(document.get("stored_path") or ""))
            if not path.exists() or path.suffix.lower() not in {".txt", ".md", ".text"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if len(_note_body(text)) >= 400:
                continue
            current = document.get("briefing") or {}
            if current.get("source") == "extract":
                continue
            briefing = factual_briefing(
                text,
                str(document.get("filename") or "note"),
                str(document.get("modality") or "text"),
                "tr",
            )
            updated = self.store.update_document(
                document["id"],
                briefing=briefing,
                message="Short note summary refreshed",
            )
            if updated:
                self.events.publish("document", updated)

    def repair_textless_portraits(self) -> None:
        """Replace moondream guesses on textless photos with Gemma or a filename summary."""
        from backend.ingest.images import _gemma_caption, _prepare_vision_jpeg, portrait_label
        from backend.services.briefing import briefing_from_vision
        from backend.services.llm_router import google_api_key

        key = google_api_key(self.store, self.settings)
        for document in self.store.list_documents():
            if document.get("modality") != "image" or document.get("status") != "ready":
                continue
            meta = dict(document.get("meta") or {})
            summary = " ".join(str(item) for item in ((document.get("briefing") or {}).get("summary") or []))
            ocr_chars = int(meta.get("ocr_chars") or 0)
            vision = str(meta.get("vision_model") or "")
            lowered = summary.lower()
            broken = (
                "ıslam olacak" in lowered
                or "islam olacak" in lowered
                or ("moondream" in vision and ocr_chars == 0)
            )
            if not broken:
                continue
            description = ""
            model = "filename"
            path = Path(document.get("stored_path") or "")
            if key and path.exists():
                try:
                    description = _gemma_caption(_prepare_vision_jpeg(path), key)
                except Exception as exc:
                    logger.warning("Portrait caption refresh failed: %s", exc)
                    description = ""
                if description:
                    model = "gemma-4-26b-a4b-it"
            if not description:
                description = portrait_label(str(document.get("filename") or "Görsel"))
            briefing = briefing_from_vision(
                description=description,
                ocr_text="",
                filename=str(document.get("filename") or "Görsel"),
            )
            department = str(meta.get("department") or "public")
            chunk = {
                "id": f"{document['id']}:0",
                "text": description,
                "metadata": {
                    "doc_id": document["id"],
                    "source": document.get("filename"),
                    "modality": "image",
                    "department": department,
                    "chunk_index": 0,
                    "vision_model": model,
                },
            }
            try:
                self.vectors.delete_document(document["id"])
                self.vectors.upsert_chunks([chunk])
            except Exception as exc:
                logger.warning("Portrait reindex failed: %s", exc)
            meta["vision_model"] = model
            updated = self.store.update_document(
                document["id"],
                briefing=briefing,
                meta=meta,
                chunk_count=1,
                message="Image caption refreshed",
            )
            if updated:
                self.events.publish("document", updated)

    def restore_deleted(self, revision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        detail = revision.get("detail") or {}
        trash = Path(detail.get("trash_dir") or "")
        if not trash.exists():
            return None
        files = [path for path in trash.iterdir() if path.is_file()]
        if not files:
            return None
        source = files[0]
        return self.register_file(
            source,
            filename=revision.get("filename") or source.name,
            origin="restore",
            copy=True,
            lang=str(detail.get("lang") or "en"),
            department=str(revision.get("department") or "public"),
            resolution="separate",
        )
