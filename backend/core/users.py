"""SQLite accounts. Vectors never store users."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

SEED = (
    ("admin", "admin123", "Yönetici", "admin", "all"),
    ("ik_uzmani", "ik123", "İK Uzmanı", "member", "hr"),
    ("finans_uzmani", "finans123", "Finans Uzmanı", "member", "finance"),
    ("teknik_ekip", "teknik123", "Teknik Ekip", "member", "engineering"),
)


_ITERATIONS = 200_000
_LEGACY_ITERATIONS = 120_000


def _hash_password(password: str, salt: str, iterations: int = _ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return digest.hex()


class UserStore:
    def __init__(self, path: Path, admin_user: str = "admin", admin_pass: str = "admin123") -> None:
        self._admin_user = admin_user or "admin"
        self._admin_pass = admin_pass or "admin123"
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    department TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    department TEXT NOT NULL,
                    question TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            try:
                self._conn.execute("ALTER TABLE users ADD COLUMN permission TEXT NOT NULL DEFAULT 'editor'")
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute("ALTER TABLE users ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            for column in ("username", "user_full_name", "user_title", "conversation_id", "thread_title"):
                try:
                    self._conn.execute(f"ALTER TABLE queries ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
                except sqlite3.OperationalError:
                    pass
            try:
                self._conn.execute("ALTER TABLE queries ADD COLUMN title_custom INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            self._conn.execute(
                """
                UPDATE queries
                SET username = COALESCE((SELECT username FROM users WHERE users.id = queries.user_id), '')
                WHERE username IS NULL OR username = ''
                """
            )
            self._conn.execute(
                """
                UPDATE queries
                SET user_full_name = COALESCE((SELECT full_name FROM users WHERE users.id = queries.user_id), '')
                WHERE user_full_name IS NULL OR user_full_name = ''
                """
            )
            self._conn.execute(
                """
                UPDATE queries
                SET user_title = COALESCE((SELECT title FROM users WHERE users.id = queries.user_id), '')
                WHERE user_title IS NULL OR user_title = ''
                """
            )
            for username, title in (
                ("admin", "Sistem Yöneticisi"),
                ("ik_uzmani", "Kıdemli İK Uzmanı"),
                ("finans_uzmani", "Finans Müdürü"),
                ("teknik_ekip", "Yazılım Mimarı"),
            ):
                self._conn.execute(
                    "UPDATE users SET title = ? WHERE username = ? AND (title IS NULL OR title = '')",
                    (title, username),
                )
        self.seed()

    def seed(self) -> None:
        with self._lock:
            accounts = ((self._admin_user, self._admin_pass, "Yönetici", "admin", "all"),) + SEED[1:]
            for username, password, full_name, role, department in accounts:
                row = self._conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                if row:
                    continue
                salt = secrets.token_hex(16)
                self._conn.execute(
                    "INSERT INTO users (username, password_hash, salt, full_name, role, department) VALUES (?, ?, ?, ?, ?, ?)",
                    (username, _hash_password(password, salt), salt, full_name, role, department),
                )
            for short, keep in (("ik", "ik_uzmani"), ("finans", "finans_uzmani"), ("teknik", "teknik_ekip")):
                kept = self._conn.execute("SELECT id FROM users WHERE username = ?", (keep,)).fetchone()
                extra = self._conn.execute("SELECT id FROM users WHERE username = ?", (short,)).fetchone()
                if kept and extra:
                    self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (extra["id"],))
                    self._conn.execute("DELETE FROM users WHERE id = ?", (extra["id"],))
            for username, title in (
                (self._admin_user, "Sistem Yöneticisi"),
                ("ik_uzmani", "Kıdemli İK Uzmanı"),
                ("finans_uzmani", "Finans Müdürü"),
                ("teknik_ekip", "Yazılım Mimarı"),
            ):
                self._conn.execute(
                    "UPDATE users SET title = ? WHERE username = ? AND (title IS NULL OR title = '')",
                    (title, username),
                )

    def _password_ok(self, row: sqlite3.Row, password: str) -> bool:
        salt = str(row["salt"])
        stored = str(row["password_hash"])
        current = _hash_password(password, salt, _ITERATIONS)
        if hmac.compare_digest(current, stored):
            return True
        legacy = _hash_password(password, salt, _LEGACY_ITERATIONS)
        if hmac.compare_digest(legacy, stored):
            self.set_password(int(row["id"]), password)
            return True
        return False

    def _start_session(self, row: sqlite3.Row) -> Dict[str, Any]:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (token, row["id"], time.time()),
            )
        user = self._public(row)
        user["token"] = token
        return user

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        if row is None or not self._password_ok(row, password):
            return None
        return self._start_session(row)

    def demo_login(self, persona: str) -> Optional[Dict[str, Any]]:
        """Open a session for the demo room without checking its current password."""
        query = {
            "admin": "SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1",
            "ik": "SELECT * FROM users WHERE department = 'hr' ORDER BY id LIMIT 1",
            "finans": "SELECT * FROM users WHERE department = 'finance' ORDER BY id LIMIT 1",
            "teknik": "SELECT * FROM users WHERE department = 'engineering' ORDER BY id LIMIT 1",
        }.get(persona)
        if not query:
            return None
        with self._lock:
            row = self._conn.execute(query).fetchone()
        if row is None:
            return None
        return self._start_session(row)

    def logout(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def resolve(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT users.* FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token = ?
                """,
                (token,),
            ).fetchone()
        return self._public(row) if row else None

    def list_users(self) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, username, full_name, role, department, permission, title FROM users ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_password(self, user_id: int, password: str) -> None:
        salt = secrets.token_hex(16)
        with self._lock:
            self._conn.execute(
                "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                (_hash_password(password, salt), salt, user_id),
            )

    def set_username(self, user_id: int, username: str) -> None:
        cleaned = username.strip()
        if not cleaned:
            raise ValueError("empty username")
        with self._lock:
            taken = self._conn.execute(
                "SELECT id FROM users WHERE username = ? AND id != ?",
                (cleaned, user_id),
            ).fetchone()
            if taken:
                raise ValueError("username taken")
            self._conn.execute("UPDATE users SET username = ? WHERE id = ?", (cleaned, user_id))

    def set_title(self, user_id: int, title: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET title = ? WHERE id = ?", (title.strip(), user_id))

    def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._public(row) if row else None

    def set_full_name(self, user_id: int, full_name: str) -> None:
        cleaned = full_name.strip()
        if not cleaned:
            raise ValueError("empty name")
        with self._lock:
            self._conn.execute("UPDATE users SET full_name = ? WHERE id = ?", (cleaned, user_id))

    def set_permission(self, user_id: int, permission: str) -> None:
        if permission not in {"read_only", "editor", "dept_admin"}:
            return
        with self._lock:
            self._conn.execute("UPDATE users SET permission = ? WHERE id = ?", (permission, user_id))

    def set_department(self, user_id: int, department: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET department = ? WHERE id = ?", (department, user_id))

    def delete_user(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def create(self, username: str, password: str, full_name: str, role: str, department: str, permission: str = "editor", title: str = "") -> Dict[str, Any]:
        salt = secrets.token_hex(16)
        permission = permission if permission in {"read_only", "editor", "dept_admin"} else "editor"
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO users (username, password_hash, salt, full_name, role, department, permission, title) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (username.strip(), _hash_password(password, salt), salt, full_name.strip(), role, department, permission, title.strip()),
            )
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._public(row)

    def add_query(
        self,
        user_id: int,
        department: str,
        question: str,
        payload: Dict[str, Any],
        username: str = "",
        full_name: str = "",
        title: str = "",
        conversation_id: str = "",
    ) -> int:
        thread_title, custom = self._thread_title(conversation_id, question)
        with self._lock:
            if conversation_id and not custom:
                self._conn.execute(
                    "UPDATE queries SET thread_title = ? WHERE conversation_id = ? AND title_custom = 0",
                    (thread_title, conversation_id),
                )
            cursor = self._conn.execute(
                """
                INSERT INTO queries (user_id, department, question, payload, created_at, username, user_full_name, user_title, conversation_id, thread_title, title_custom)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    department,
                    question,
                    json.dumps(payload, ensure_ascii=False),
                    time.time(),
                    username,
                    full_name,
                    title,
                    conversation_id,
                    thread_title,
                    1 if custom else 0,
                ),
            )
        return int(cursor.lastrowid)

    def _thread_title(self, conversation_id: str, question: str) -> tuple:
        from backend.services.rag import suggest_thread_title

        if not conversation_id:
            return suggest_thread_title([question]), False
        with self._lock:
            rows = self._conn.execute(
                "SELECT question, thread_title, title_custom FROM queries WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        if any(int(row["title_custom"] or 0) for row in rows):
            titled = next((row["thread_title"] for row in rows if int(row["title_custom"] or 0) and row["thread_title"]), "")
            return titled or suggest_thread_title([question]), True
        questions = [str(row["question"] or "") for row in rows]
        questions.append(question)
        return suggest_thread_title(questions), False

    def cited_doc_ids(self, conversation_id: str) -> list:
        if not conversation_id:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM queries WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        found: list = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            for citation in (payload or {}).get("citations") or []:
                doc_id = str((citation or {}).get("doc_id") or "")
                if doc_id and doc_id not in found:
                    found.append(doc_id)
        return found

    def rename_thread(self, viewer: Dict[str, Any], conversation_id: str, title: str) -> str:
        cleaned = re.sub(r"\s+", " ", (title or "")).strip()[:80]
        if not conversation_id or not cleaned:
            raise ValueError("A conversation and a title are required.")
        admin = str(viewer.get("role") or "") == "admin"
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id FROM queries WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
            if not rows:
                raise ValueError("Conversation not found.")
            if not admin and all(int(row["user_id"]) != int(viewer["id"]) for row in rows):
                raise PermissionError("You cannot rename this conversation.")
            self._conn.execute(
                "UPDATE queries SET thread_title = ?, title_custom = 1 WHERE conversation_id = ?",
                (cleaned, conversation_id),
            )
        return cleaned

    def list_queries(
        self,
        viewer: Dict[str, Any],
        department: Optional[str] = None,
        username: Optional[str] = None,
        limit: int = 500,
        search: Optional[str] = None,
        conversation_id: Optional[str] = None,
        query_ids: Optional[Sequence[int]] = None,
    ) -> list:
        from backend.services.rag import (
            answer_is_full_refusal,
            strip_citation_markers,
            strip_instruction_leak,
            suggest_thread_title,
        )

        admin = str(viewer.get("role") or "") == "admin"
        clauses = []
        params: list = []
        if not admin:
            clauses.append("COALESCE(NULLIF(q.username, ''), u.username) = ?")
            params.append(str(viewer.get("username") or ""))
            home = str(viewer.get("department") or "")
            if home and home != "all":
                clauses.append("q.department = ?")
                params.append(home)
        else:
            chosen = str(department or "").strip().lower()
            if chosen and chosen not in {"all", "tumu", "tümü", "*"}:
                clauses.append("q.department = ?")
                params.append(chosen)
            person = str(username or "").strip()
            if person and person not in {"all", "*"}:
                clauses.append("COALESCE(NULLIF(q.username, ''), u.username) = ?")
                params.append(person)
        thread = str(conversation_id or "").strip()
        if thread:
            clauses.append("q.conversation_id = ?")
            params.append(thread)
        ids = [int(item) for item in (query_ids or []) if str(item).strip()]
        if ids:
            clauses.append(f"q.id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT q.id, q.question, q.department, q.payload, q.created_at, q.conversation_id, q.thread_title, q.title_custom,
                   COALESCE(NULLIF(q.username, ''), u.username, '') AS username,
                   COALESCE(NULLIF(q.user_full_name, ''), u.full_name, '') AS user_full_name,
                   COALESCE(NULLIF(q.user_title, ''), u.title, '') AS user_title
            FROM queries q
            LEFT JOIN users u ON u.id = q.user_id
            {where}
            ORDER BY q.id DESC
            LIMIT ?
        """
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        items = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict) and payload.get("answer"):
                payload = dict(payload)
                payload["answer"] = strip_citation_markers(strip_instruction_leak(str(payload.get("answer") or "")))
                if answer_is_full_refusal(str(payload.get("answer") or "")):
                    payload["grounding_score"] = 0
                    payload["citations"] = []
            items.append(
                {
                    "id": row["id"],
                    "question": row["question"],
                    "department": row["department"],
                    "username": row["username"] or "",
                    "user_full_name": row["user_full_name"] or "",
                    "user_title": row["user_title"] or "",
                    "conversation_id": row["conversation_id"] or "",
                    "thread_title": row["thread_title"] or "",
                    "title_custom": int(row["title_custom"] or 0),
                    "created_at": row["created_at"],
                    "answer": payload,
                }
            )
        grouped: Dict[str, list] = {}
        for item in items:
            key = item["conversation_id"] or f"solo-{item['id']}"
            grouped.setdefault(key, []).append(item)
        for group in grouped.values():
            custom = next((item["thread_title"] for item in group if item["title_custom"] and item["thread_title"]), "")
            ordered = sorted(group, key=lambda item: item["created_at"])
            title = custom or suggest_thread_title([item["question"] for item in ordered])
            for item in group:
                item["thread_title"] = title
                item.pop("title_custom", None)
        needle = str(search or "").strip().casefold()
        if needle:
            kept = []
            for item in items:
                answer = item.get("answer") if isinstance(item.get("answer"), dict) else {}
                blob = " ".join(
                    [
                        str(item.get("question") or ""),
                        str(answer.get("answer") or ""),
                        str(item.get("user_full_name") or ""),
                        str(item.get("username") or ""),
                        str(item.get("thread_title") or ""),
                    ]
                ).casefold()
                if needle in blob:
                    kept.append(item)
            items = kept
        return items

    def delete_query(self, viewer: Dict[str, Any], query_id: int) -> None:
        with self._lock:
            if str(viewer.get("role") or "") == "admin":
                self._conn.execute("DELETE FROM queries WHERE id = ?", (query_id,))
            else:
                self._conn.execute(
                    "DELETE FROM queries WHERE id = ? AND user_id = ?",
                    (query_id, int(viewer["id"])),
                )

    def clear_queries(self, viewer: Dict[str, Any]) -> None:
        with self._lock:
            if str(viewer.get("role") or "") == "admin":
                self._conn.execute("DELETE FROM queries")
                return
            home = str(viewer.get("department") or "")
            if home and home != "all":
                self._conn.execute(
                    "DELETE FROM queries WHERE user_id = ? AND department = ?",
                    (int(viewer["id"]), home),
                )
            else:
                self._conn.execute("DELETE FROM queries WHERE user_id = ?", (int(viewer["id"]),))

    @staticmethod
    def _public(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "role": row["role"],
            "department": row["department"],
            "permission": row["permission"] if "permission" in row.keys() else "editor",
            "title": row["title"] if "title" in row.keys() else "",
        }
