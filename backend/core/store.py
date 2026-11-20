"""SQLite-backed registry for documents, runtime settings and the compliance audit trail."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    modality TEXT NOT NULL,
    file_type TEXT NOT NULL,
    mime TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'upload',
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    error TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    embed_model TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    briefing TEXT,
    privacy TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    indexed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_documents_sha ON documents(sha256);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    doc_id TEXT NOT NULL,
    action TEXT NOT NULL,
    filename TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT 'public',
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_revisions_ts ON revisions(ts);

CREATE TABLE IF NOT EXISTS departments (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);
"""

JSON_FIELDS = ("meta", "briefing", "privacy")
DOCUMENT_COLUMNS = (
    "id",
    "filename",
    "modality",
    "file_type",
    "mime",
    "size_bytes",
    "sha256",
    "stored_path",
    "origin",
    "status",
    "stage",
    "progress",
    "message",
    "error",
    "chunk_count",
    "embed_model",
    "meta",
    "briefing",
    "privacy",
    "created_at",
    "updated_at",
    "indexed_at",
)


class Store:
    def __init__(self, path: Path) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._ensure_departments()

    def _ensure_departments(self) -> None:
        defaults = (("yonetim", "Yönetim", 1), ("hr", "İK", 2), ("finance", "Finans", 3), ("engineering", "Teknik", 4))
        for slug, name, order in defaults:
            self._conn.execute(
                "INSERT INTO departments (slug, name, sort_order) VALUES (?, ?, ?) ON CONFLICT(slug) DO UPDATE SET name = excluded.name",
                (slug, name, order),
            )

    def list_departments(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT slug, name, sort_order FROM departments ORDER BY sort_order, name").fetchall()
        return [dict(row) for row in rows]

    def add_department(self, slug: str, name: str) -> None:
        with self._lock:
            current = self._conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS n FROM departments").fetchone()
            order = int(current["n"]) + 1
            self._conn.execute(
                "INSERT INTO departments (slug, name, sort_order) VALUES (?, ?, ?) ON CONFLICT(slug) DO UPDATE SET name = excluded.name",
                (slug, name, order),
            )

    def delete_department_row(self, slug: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM departments WHERE slug = ?", (slug,))

    def move_department(self, slug: str, direction: str) -> None:
        with self._lock:
            rows = self._conn.execute("SELECT slug, sort_order FROM departments ORDER BY sort_order, name").fetchall()
            index = next((i for i, row in enumerate(rows) if row["slug"] == slug), None)
            if index is None:
                return
            swap = index - 1 if direction == "up" else index + 1
            if swap < 0 or swap >= len(rows):
                return
            left, right = rows[index], rows[swap]
            self._conn.execute("UPDATE departments SET sort_order = ? WHERE slug = ?", (right["sort_order"], left["slug"]))
            self._conn.execute("UPDATE departments SET sort_order = ? WHERE slug = ?", (left["sort_order"], right["slug"]))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _encode(field: str, value: Any) -> Any:
        if field in JSON_FIELDS and value is not None and not isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        return value

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row)
        for field in JSON_FIELDS:
            raw = data.get(field)
            if isinstance(raw, str) and raw:
                try:
                    data[field] = json.loads(raw)
                except json.JSONDecodeError:
                    data[field] = None
        if data.get("meta") is None:
            data["meta"] = {}
        return data

    # ------------------------------------------------------------------ documents
    def create_document(self, **fields: Any) -> Dict[str, Any]:
        now = time.time()
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
        fields.setdefault("meta", {})
        columns = [c for c in fields if c in DOCUMENT_COLUMNS]
        values = [self._encode(c, fields[c]) for c in columns]
        placeholders = ", ".join("?" for _ in columns)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO documents ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
        document = self.get_document(fields["id"])
        assert document is not None
        return document

    def update_document(self, doc_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        fields = {k: v for k, v in fields.items() if k in DOCUMENT_COLUMNS and k != "id"}
        if not fields:
            return self.get_document(doc_id)
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in fields)
        values = [self._encode(column, value) for column, value in fields.items()]
        with self._lock:
            self._conn.execute(f"UPDATE documents SET {assignments} WHERE id = ?", [*values, doc_id])
        return self.get_document(doc_id)

    def merge_meta(self, doc_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            document = self.get_document(doc_id)
            if document is None:
                return None
            meta = dict(document.get("meta") or {})
            meta.update(patch)
            return self.update_document(doc_id, meta=meta)

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return self._row_to_dict(row)

    def list_documents(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [d for d in (self._row_to_dict(r) for r in rows) if d is not None]

    def documents_with_status(self, statuses: Iterable[str]) -> List[Dict[str, Any]]:
        statuses = list(statuses)
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM documents WHERE status IN ({placeholders}) ORDER BY created_at ASC",
                statuses,
            ).fetchall()
        return [d for d in (self._row_to_dict(r) for r in rows) if d is not None]

    def find_by_sha(self, sha256: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE sha256 = ? ORDER BY created_at ASC LIMIT 1", (sha256,)
            ).fetchone()
        return self._row_to_dict(row)

    def find_by_origin(self, origin: str, filename: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE origin = ? AND filename = ? ORDER BY created_at ASC LIMIT 1",
                (origin, filename),
            ).fetchone()
        return self._row_to_dict(row)

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ settings
    def get_settings(self) -> Dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        result: Dict[str, Any] = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                result[row["key"]] = row["value"]
        return result

    def set_settings(self, values: Dict[str, Any]) -> None:
        with self._lock:
            for key, value in values.items():
                if value is None:
                    self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                else:
                    self._conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, json.dumps(value)),
                    )

    # ------------------------------------------------------------------ audit trail
    def audit(self, action: str, detail: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (ts, action, detail) VALUES (?, ?, ?)",
                (time.time(), action, json.dumps(detail or {}, ensure_ascii=False)),
            )
            self._conn.execute(
                "DELETE FROM audit_log WHERE id NOT IN (SELECT id FROM audit_log ORDER BY id DESC LIMIT 5000)"
            )

    def add_revision(
        self,
        *,
        doc_id: str,
        action: str,
        filename: str,
        department: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO revisions (ts, doc_id, action, filename, department, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), doc_id, action, filename, department, json.dumps(detail or {}, ensure_ascii=False)),
            )
            row_id = cursor.lastrowid
        return self.get_revision(int(row_id)) or {}

    def annotate_revision(self, doc_id: str, action: str, patch: Dict[str, Any]) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, detail FROM revisions WHERE doc_id = ? AND action = ? ORDER BY id DESC LIMIT 1",
                (doc_id, action),
            ).fetchone()
            if row is None:
                return
            try:
                detail = json.loads(row["detail"] or "{}")
            except json.JSONDecodeError:
                detail = {}
            detail.update(patch)
            self._conn.execute("UPDATE revisions SET detail = ? WHERE id = ?", (json.dumps(detail, ensure_ascii=False), row["id"]))

    def get_revision(self, revision_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM revisions WHERE id = ?", (revision_id,)).fetchone()
        if row is None:
            return None
        detail = {}
        try:
            detail = json.loads(row["detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        return {
            "id": row["id"],
            "ts": row["ts"],
            "doc_id": row["doc_id"],
            "action": row["action"],
            "filename": row["filename"],
            "department": row["department"],
            "detail": detail,
        }

    def list_revisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM revisions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [item for item in (self.get_revision(row["id"]) for row in rows) if item]

    def list_audit(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, action, detail FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        entries = []
        for row in rows:
            try:
                detail = json.loads(row["detail"])
            except json.JSONDecodeError:
                detail = {}
            entries.append({"id": row["id"], "ts": row["ts"], "action": row["action"], "detail": detail})
        return entries
