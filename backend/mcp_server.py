"""Model Context Protocol for OmniVault.

HTTP clients keep using the catalog below. Running this file directly starts a
stdio MCP server so Cursor or Claude Desktop can open the same SQLite and
Chroma vault. Tool handlers never evaluate arbitrary code.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core.access import can_mutate_document, can_see_department, normalize_department, normalize_role

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_department_vault",
        "description": "Hybrid retrieval inside one department. Returns grounded snippets, filenames, and timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "department": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["department", "query"],
        },
    },
    {
        "name": "list_department_documents",
        "description": "Active documents, versions, and summaries in one department.",
        "inputSchema": {
            "type": "object",
            "properties": {"department": {"type": "string"}},
            "required": ["department"],
        },
    },
    {
        "name": "search_vault",
        "description": "Hybrid retrieval inside one department. Returns grounded chunks and confidence scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "department": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["department", "query"],
        },
    },
    {
        "name": "calculate_department_metrics",
        "description": "Sum, count, average, or a percentage of salary and expense figures stored in one department.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "department": {"type": "string"},
                "expression": {"type": "string", "description": "Examples: sum, maas toplam, gider * 0.2, sum percent 18"},
            },
            "required": ["department", "expression"],
        },
    },
    {
        "name": "transfer_document",
        "description": "Move or copy a document into another department without re-ingesting it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string"},
                "target_department": {"type": "string"},
                "mode": {"type": "string", "enum": ["move", "copy"]},
            },
            "required": ["doc_id", "target_department", "mode"],
        },
    },
    {
        "name": "apply_document_update",
        "description": "Apply a confirmed text update to a document and refresh its vectors and briefing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["doc_id", "new_text"],
        },
    },
]

RESOURCES: List[Dict[str, Any]] = [
    {
        "uri": "vault://{department}/documents",
        "name": "Department documents",
        "description": "Indexed documents, versions, and summaries for one department.",
        "mimeType": "application/json",
    }
]


def catalog() -> Dict[str, Any]:
    return {"tools": TOOLS, "resources": RESOURCES, "tool_count": len(TOOLS), "resource_count": len(RESOURCES)}


def call_tool(name: str, arguments: Optional[Dict[str, Any]], *, services: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    arguments = arguments or {}
    role = normalize_role(str(user.get("role") or "reader"))
    if name in {"search_vault", "search_department_vault"}:
        found = _search(str(arguments.get("department") or ""), str(arguments.get("query") or ""), services, role, user)
        return _with_timestamps(found, services["store"])
    if name == "list_department_documents":
        return read_resource(
            f"vault://{normalize_department(str(arguments.get('department') or ''))}/documents",
            services=services,
            user=user,
        )
    if name == "calculate_department_metrics":
        return _metrics(str(arguments.get("department") or ""), str(arguments.get("expression") or ""), services, role, user)
    if name == "transfer_document":
        return _transfer(
            str(arguments.get("doc_id") or ""),
            str(arguments.get("target_department") or ""),
            str(arguments.get("mode") or ""),
            services,
            role,
            user,
        )
    if name == "apply_document_update":
        return _apply_update(str(arguments.get("doc_id") or ""), str(arguments.get("new_text") or ""), services, role, user)
    raise ValueError(f"Unknown MCP tool: {name}")


def read_resource(uri: str, *, services: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    match = re.fullmatch(r"vault://([a-z0-9\-]+)/documents", (uri or "").strip().lower())
    if not match:
        raise ValueError("Resource URI must look like vault://{department}/documents.")
    department = normalize_department(match.group(1))
    role = normalize_role(str(user.get("role") or "reader"))
    active = str(user.get("department") or "")
    if not can_see_department(role, department, active):
        raise PermissionError("You cannot read that department.")
    documents = []
    for document in services["store"].list_documents():
        owner = str((document.get("meta") or {}).get("department") or "")
        if normalize_department(owner) != department or document.get("status") == "duplicate":
            continue
        briefing = document.get("briefing") or {}
        documents.append(
            {
                "id": document.get("id"),
                "filename": document.get("filename"),
                "status": document.get("status"),
                "version": int((document.get("meta") or {}).get("version") or 1),
                "summary": [str(item) for item in (briefing.get("summary") or [])[:4]],
                "created_at": document.get("created_at"),
                "updated_at": document.get("updated_at"),
            }
        )
    return {"uri": f"vault://{department}/documents", "department": department, "documents": documents}


def _guard_department(role: str, department: str, user: Dict[str, Any]) -> str:
    slug = normalize_department(department)
    if not slug:
        raise ValueError("A department is required.")
    if not can_see_department(role, slug, str(user.get("department") or "")):
        raise PermissionError("You cannot access that department.")
    return slug


def _search(department: str, query: str, services: Dict[str, Any], role: str, user: Dict[str, Any]) -> Dict[str, Any]:
    slug = _guard_department(role, department, user)
    question = query.strip()
    if not question:
        raise ValueError("A query is required.")
    hits = services["vectors"].query(question, top_k=6, departments=(slug,), pool=24)
    chunks = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        relevance = float(hit.get("relevance") or 0.0)
        chunks.append(
            {
                "doc_id": str(meta.get("doc_id") or ""),
                "filename": str(meta.get("source") or ""),
                "text": str(hit.get("text") or "")[:1200],
                "relevance": round(relevance, 4),
                "confidence": round(max(0.0, min(1.0, relevance)) * 100, 1),
            }
        )
    return {"department": slug, "query": question, "chunks": chunks}


def _with_timestamps(result: Dict[str, Any], store: Any) -> Dict[str, Any]:
    index = {str(document.get("id") or ""): document for document in store.list_documents()}
    for chunk in result.get("chunks") or []:
        document = index.get(str(chunk.get("doc_id") or "")) or {}
        meta = document.get("meta") or {}
        raw = meta.get("version_at") or document.get("updated_at") or document.get("created_at")
        chunk["timestamp"] = _clock(raw)
        chunk["updated_at"] = raw
        chunk["version"] = int(meta.get("version") or 1)
    return result


def _clock(value: Any) -> str:
    try:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(float(value)))
    except (TypeError, ValueError, OSError):
        return ""


def cursor_config() -> Dict[str, Any]:
    """stdio launch snippet a local MCP host can paste into mcp.json."""
    python = _ROOT / ".venv" / "bin" / "python"
    command = str(python if python.exists() else Path(sys.executable))
    return {
        "mcpServers": {
            "omnivault": {
                "command": command,
                "args": [str(Path(__file__).resolve())],
            }
        }
    }


_VAULT: Optional[Dict[str, Any]] = None


def _local_operator() -> Dict[str, str]:
    """The stdio process is launched by the person who owns this Mac."""
    return {"role": "admin", "department": "all"}


def _open_vault() -> Dict[str, Any]:
    global _VAULT
    if _VAULT is not None:
        return _VAULT
    from backend.config import get_settings
    from backend.core.embeddings import EmbeddingService
    from backend.core.events import EventBus
    from backend.core.ollama import OllamaClient
    from backend.core.store import Store
    from backend.core.vectorstore import VectorStore
    from backend.ingest.audio import WhisperEngine
    from backend.services.pipeline import IngestionPipeline

    settings = get_settings()
    settings.ensure_dirs()
    store = Store(settings.db_path)
    current = store.get_settings()
    ollama = OllamaClient(settings.ollama_url, timeout=settings.ollama_timeout)
    embeddings = EmbeddingService(
        ollama,
        model=str(current.get("embed_model") or settings.embed_model or ""),
        keep_alive=settings.embed_keep_alive,
    )
    vectors = VectorStore(settings, embeddings)
    whisper = WhisperEngine(
        model_size=settings.whisper_model,
        download_root=settings.models_dir / "whisper",
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        beam_size=settings.whisper_beam_size,
    )
    pipeline = IngestionPipeline(settings, store, vectors, ollama, whisper, EventBus())
    _VAULT = {"store": store, "vectors": vectors, "pipeline": pipeline}
    return _VAULT


def _stdio_server():
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("OmniVault")

    @server.tool()
    def search_department_vault(department: str, query: str) -> Dict[str, Any]:
        """Hybrid search inside one department. Returns grounded snippets, filenames, and timestamps."""
        return call_tool(
            "search_department_vault",
            {"department": department, "query": query},
            services=_open_vault(),
            user=_local_operator(),
        )

    @server.tool()
    def list_department_documents(department: str) -> Dict[str, Any]:
        """List active documents, versions, and summaries in one department."""
        return call_tool(
            "list_department_documents",
            {"department": department},
            services=_open_vault(),
            user=_local_operator(),
        )

    @server.tool()
    def transfer_document(doc_id: str, target_department: str, mode: str) -> Dict[str, Any]:
        """Move or copy a document into another department without OCR, Whisper, or a new embedding pass."""
        return call_tool(
            "transfer_document",
            {"doc_id": doc_id, "target_department": target_department, "mode": mode},
            services=_open_vault(),
            user=_local_operator(),
        )

    @server.resource(
        "vault://{department}/documents",
        name="Department documents",
        description="Indexed documents, versions, and summaries for one department.",
        mime_type="application/json",
    )
    def department_documents(department: str) -> str:
        payload = read_resource(
            f"vault://{department}/documents",
            services=_open_vault(),
            user=_local_operator(),
        )
        return json.dumps(payload, ensure_ascii=False)

    return server


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s", stream=sys.stderr)
    _stdio_server().run(transport="stdio")


def _metrics(department: str, expression: str, services: Dict[str, Any], role: str, user: Dict[str, Any]) -> Dict[str, Any]:
    slug = _guard_department(role, department, user)
    amounts = _amounts_for(services["store"], slug, expression)
    value = evaluate_expression(expression, amounts)
    return {
        "department": slug,
        "expression": expression,
        "value": value,
        "samples": len(amounts),
        "unit": "TL",
    }


def _apply_update(doc_id: str, new_text: str, services: Dict[str, Any], role: str, user: Dict[str, Any]) -> Dict[str, Any]:
    if role == "reader":
        raise PermissionError("This account cannot update documents.")
    document = services["store"].get_document(doc_id)
    if not document:
        raise ValueError("Document not found.")
    source = str((document.get("meta") or {}).get("department") or "")
    if not can_mutate_document(role, source, str(user.get("department") or "")):
        raise PermissionError("You cannot update this document.")
    text = (new_text or "").strip()
    if not text:
        raise ValueError("Updated text is required.")
    updated = services["pipeline"].replace_text(doc_id, text)
    if not updated:
        raise ValueError("That text could not be saved.")
    return {"id": updated.get("id"), "filename": updated.get("filename"), "status": updated.get("status")}


def _transfer(doc_id: str, target: str, mode: str, services: Dict[str, Any], role: str, user: Dict[str, Any]) -> Dict[str, Any]:
    if role == "reader":
        raise PermissionError("This account cannot move documents.")
    document = services["store"].get_document(doc_id)
    if not document:
        raise ValueError("Document not found.")
    source = str((document.get("meta") or {}).get("department") or "")
    if not can_mutate_document(role, source, str(user.get("department") or "")):
        raise PermissionError("You cannot transfer this document.")
    updated = services["pipeline"].transfer_document(doc_id, normalize_department(target), mode)
    names = {str(item.get("slug") or ""): str(item.get("name") or "") for item in services["store"].list_departments()}
    slug = str((updated.get("meta") or {}).get("department") or "")
    return {
        "id": updated.get("id"),
        "filename": updated.get("filename"),
        "mode": mode,
        "department": slug,
        "department_name": names.get(slug) or slug,
    }


_MONEY = re.compile(r"(\d+(?:[.\s]\d{3})*|\d+)\s*(bin)?\s*tl\b", re.IGNORECASE)
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[A-Za-zÇĞİÖŞÜçğıöşü]+|[+\-*/()]|%")
_FILTER_WORDS = re.compile(
    r"\b(maas|maaş|salary|ucret|ücret|gider|giderler|expense|butce|bütçe)\b",
    re.IGNORECASE,
)
_WORDS = {
    "sum": "sum",
    "toplam": "sum",
    "count": "count",
    "adet": "count",
    "avg": "avg",
    "ortalama": "avg",
    "percent": "percent",
    "yuzde": "percent",
    "yüzde": "percent",
}


def _amounts_for(store: Any, department: str, expression: str) -> List[float]:
    folded = expression.lower()
    want_salary = any(word in folded for word in ("maas", "maaş", "salary", "ucret", "ücret"))
    want_expense = any(word in folded for word in ("gider", "expense", "butce", "bütçe"))
    amounts: List[float] = []
    for document in store.list_documents():
        owner = normalize_department(str((document.get("meta") or {}).get("department") or ""))
        if owner != department or document.get("status") not in {"ready", "processing", "indexing", "briefing"}:
            continue
        text = _document_text(document)
        lowered = text.lower()
        if want_salary and not any(word in lowered for word in ("maaş", "maas", "ücret", "tl")):
            continue
        if want_expense and not any(word in lowered for word in ("gider", "harcama", "bütçe", "butce")):
            continue
        if want_salary or want_expense:
            amounts.extend(_money_amounts(text))
        elif not want_salary and not want_expense:
            amounts.extend(_money_amounts(text))
    return amounts


def _document_text(document: Dict[str, Any]) -> str:
    from pathlib import Path

    path = Path(str(document.get("stored_path") or ""))
    if path.suffix.lower() in {".md", ".txt", ".markdown"} and path.exists() and path.stat().st_size < 200_000:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
    briefing = document.get("briefing") or {}
    return " ".join(str(item) for item in (briefing.get("summary") or []))


def _money_amounts(text: str) -> List[float]:
    found = []
    for match in _MONEY.finditer(text or ""):
        raw = match.group(1).replace(" ", "")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
            value = float(raw.replace(".", ""))
        else:
            value = float(raw.replace(",", "."))
        if match.group(2):
            value *= 1000
        found.append(value)
    return found


def evaluate_expression(expression: str, amounts: Sequence[float]) -> float:
    """Arithmetic over sum, count, and average. Names other than those words are rejected."""
    text = _FILTER_WORDS.sub(" ", expression or "sum")
    text = re.sub(r"\s+", " ", text).strip() or "sum"
    if not text or len(text) > 200:
        raise ValueError("The expression must be between 1 and 200 characters.")
    tokens = _TOKEN.findall(text)
    if not tokens:
        raise ValueError("The expression is empty.")
    joined = "".join(tokens)
    compact = re.sub(r"\s+", "", text)
    if joined.lower() != compact.lower():
        raise ValueError("The expression contains characters that cannot be calculated.")
    env = {
        "sum": float(sum(amounts)),
        "count": float(len(amounts)),
        "avg": (float(sum(amounts)) / len(amounts)) if amounts else 0.0,
    }
    parser = _Parser(tokens, env)
    value = parser.parse()
    if parser.remaining():
        raise ValueError("The expression could not be read completely.")
    return round(value, 2)


class _Parser:
    def __init__(self, tokens: List[str], env: Dict[str, float]) -> None:
        self.tokens = tokens
        self.env = env
        self.index = 0

    def remaining(self) -> bool:
        return self.index < len(self.tokens)

    def parse(self) -> float:
        value = self._term()
        while self._peek() in {"+", "-"}:
            op = self._take()
            right = self._term()
            value = value + right if op == "+" else value - right
        return value

    def _term(self) -> float:
        value = self._factor()
        while self._peek() in {"*", "/"}:
            op = self._take()
            right = self._factor()
            if op == "/" and right == 0:
                raise ValueError("Division by zero.")
            value = value * right if op == "*" else value / right
        return value

    def _factor(self) -> float:
        token = self._take()
        if token == "(":
            value = self.parse()
            if self._take() != ")":
                raise ValueError("A closing parenthesis is missing.")
            return self._percent(value)
        if token == "-":
            return -self._factor()
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return self._percent(float(token))
        key = _WORDS.get(token.lower())
        if key in {"sum", "count", "avg"}:
            return self._percent(self.env[key])
        raise ValueError(f"Unknown value: {token}")

    def _percent(self, value: float) -> float:
        if self._peek() == "%":
            self._take()
            return value / 100.0
        if _WORDS.get((self._peek() or "").lower()) == "percent":
            self._take()
            percent = self._factor()
            return value * percent / 100.0
        return value

    def _peek(self) -> str:
        if self.index >= len(self.tokens):
            return ""
        return self.tokens[self.index]

    def _take(self) -> str:
        if self.index >= len(self.tokens):
            raise ValueError("The expression ended early.")
        token = self.tokens[self.index]
        self.index += 1
        return token


if __name__ == "__main__":
    main()
