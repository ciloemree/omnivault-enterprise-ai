"""Spreadsheet routing. CSV and Excel stay as pandas frames and are not embedded.

The agent asks the selected OmniVault model for a pandas calculation, then runs
that calculation on the in-memory frames. Text files never enter this path.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from backend.models.schemas import Citation, QueryResponse

TABLE_EXTENSIONS = {".csv", ".xlsx", ".xls"}
_CUES = (
    "excel", "xlsx", "xls", "csv", "spreadsheet", "calisma sayfasi", "çalışma sayfası",
    "tablodaki", "tabloda", "sutun", "sütun", "kolon", "sayfadaki",
)
_FORBIDDEN = re.compile(
    r"(\b(import|open|exec|eval|compile|globals|locals|getattr|setattr|delattr|os|sys|subprocess|pathlib|socket|shutil|input)\b|__|read_|to_csv|to_excel|to_pickle|to_json|to_sql|to_parquet|to_html|to_feather|to_clipboard)",
    re.IGNORECASE,
)


def is_tabular_path(path: Path) -> bool:
    return path.suffix.lower() in TABLE_EXTENSIONS


def is_tabular_document(document: Dict[str, Any]) -> bool:
    if (document.get("meta") or {}).get("table_agent"):
        return True
    name = str(document.get("filename") or document.get("stored_path") or "")
    return Path(name).suffix.lower() in TABLE_EXTENSIONS


def _fold(text: str) -> str:
    return (
        (text or "")
        .replace("İ", "i")
        .replace("I", "ı")
        .lower()
        .replace("ı", "i")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
    )


def load_frames(path: Path) -> Dict[str, pd.DataFrame]:
    """Read a spreadsheet into one frame per sheet. The file is not chunked."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path, encoding="utf-8-sig")
        return {path.stem: _clean(frame)}
    engine = "xlrd" if suffix == ".xls" else "openpyxl"
    book = pd.ExcelFile(path, engine=engine)
    frames = {}
    for sheet in book.sheet_names:
        frame = book.parse(sheet)
        frames[str(sheet)] = _clean(frame)
    return frames or {path.stem: pd.DataFrame()}


def profile_frames(frames: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    sheets = []
    headers: List[str] = []
    rows = 0
    for name, frame in frames.items():
        columns = [str(column) for column in frame.columns]
        headers.extend(columns)
        rows += int(len(frame))
        sheets.append({"name": name, "rows": int(len(frame)), "columns": columns[:40]})
    return {"sheets": sheets, "columns": _unique(headers)[:40], "row_count": rows}


def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    if len(frame) > 100_000:
        frame = frame.head(100_000)
    return frame


def _unique(items: Sequence[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


class TableAgent:
    def __init__(self, store: Any, router: Any, settings: Any) -> None:
        self.store = store
        self.router = router
        self.settings = settings
        self._cache: Dict[str, tuple] = {}

    def answer(
        self,
        question: str,
        *,
        departments: Optional[Sequence[str]],
        selected: Dict[str, Any],
        model: str,
        language: str,
        started: float,
        history: Optional[Sequence[Dict[str, str]]] = None,
        prior_doc_ids: Optional[Sequence[str]] = None,
    ) -> Optional[QueryResponse]:
        chosen = self.select_documents(question, departments, history=history, prior_doc_ids=prior_doc_ids)
        if not chosen:
            return None
        loaded = []
        for document in chosen:
            frames = self.frames_for(document)
            if frames:
                loaded.append((document, frames))
        if not loaded:
            return None
        code = self._generate(question, loaded, selected, model, language)
        result, error = self.execute(code, loaded)
        if error:
            code = self._generate(question, loaded, selected, model, language, error=error)
            result, error = self.execute(code, loaded)
        if error:
            text = (
                "Tablo okundu fakat hesap tamamlanamadı. Soruyu sütun adıyla yeniden sorun."
                if language == "tr"
                else "The spreadsheet was loaded, but the calculation did not finish. Ask again using a column name."
            )
        else:
            rendered = render_result(result)
            lead = "Hesap tablodan yapıldı." if language == "tr" else "Calculated from the spreadsheet."
            text = f"{lead}\n\n{rendered}" if rendered else lead
        citations = [self._citation(document, index) for index, (document, _) in enumerate(loaded, start=1)]
        label = "Tablo Ajanı" if language == "tr" else "Table agent"
        return QueryResponse(
            answer=text,
            citations=citations,
            grounding_score=96.0 if not error else 0.0,
            grounding_label="High" if not error else "Weak",
            modalities_used=["office"],
            model=model or selected.get("model") or "table-agent",
            retrieved=len(citations),
            privacy_redactions={},
            latency_ms=round((time.time() - started) * 1000, 1),
            engine=selected.get("id") or "local",
            provider=selected.get("provider") or "local",
            engine_label=label,
        )

    def select_documents(
        self,
        question: str,
        departments: Optional[Sequence[str]],
        *,
        history: Optional[Sequence[Dict[str, str]]] = None,
        prior_doc_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        allowed = {
            str(item).strip().lower()
            for item in (departments or [])
            if str(item).strip().lower() not in {"", "all", "tumu", "tümü", "*"}
        }
        tables = []
        for document in self.store.list_documents():
            if document.get("status") != "ready" or not is_tabular_document(document):
                continue
            owner = str((document.get("meta") or {}).get("department") or "").lower()
            if allowed and owner not in allowed:
                continue
            tables.append(document)
        if not tables:
            return []
        folded = _fold(question)
        named = []
        for document in tables:
            stem = _fold(Path(str(document.get("filename") or "")).stem)
            if stem and stem in folded:
                named.append(document)
        if named:
            return named[:4]
        scored = []
        for document in tables:
            headers = [str(item) for item in ((document.get("meta") or {}).get("columns") or [])]
            if not headers:
                headers = [
                    str(column)
                    for frame in self.frames_for(document).values()
                    for column in frame.columns
                ]
            overlap = [header for header in headers if len(_fold(header)) >= 4 and _fold(header) in folded]
            if overlap:
                scored.append((len(overlap), document))
        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            return [document for _, document in scored[:3]]
        if any(cue in folded for cue in _CUES):
            return tables[:4]
        by_id = {str(document.get("id") or ""): document for document in tables}
        prior_tables = [by_id[item] for item in (prior_doc_ids or []) if item in by_id]
        follow = ("toplam", "ortalama", "kac", "kaç", "satir", "satır", "yuzde", "yüzde", "adet", "sum", "average", "count")
        previous = _fold(" ".join(str(turn.get("question") or "") for turn in (history or [])))
        short_follow = len(folded.split()) <= 8 and any(cue in previous for cue in _CUES)
        if prior_tables and (any(word in folded for word in follow) or short_follow):
            return prior_tables[:4]
        return []

    def frames_for(self, document: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
        doc_id = str(document.get("id") or "")
        path = Path(str(document.get("stored_path") or ""))
        if not path.exists():
            return {}
        stamp = path.stat().st_mtime
        cached = self._cache.get(doc_id)
        if cached and cached[0] == stamp:
            return cached[1]
        frames = load_frames(path)
        self._cache[doc_id] = (stamp, frames)
        return frames

    def execute(self, code: str, loaded: Sequence[tuple]) -> tuple:
        cleaned = _extract_code(code)
        if not cleaned or _FORBIDDEN.search(cleaned):
            return None, "The model did not return a safe pandas calculation."
        namespace: Dict[str, Any] = {"pd": pd, "result": None}
        for document, frames in loaded:
            stem = Path(str(document.get("filename") or "table")).stem
            safe_stem = re.sub(r"\W+", "_", stem) or "table"
            if len(frames) == 1:
                namespace[safe_stem] = next(iter(frames.values()))
            for sheet, frame in frames.items():
                key = re.sub(r"\W+", "_", f"{safe_stem}_{sheet}") or safe_stem
                namespace[key] = frame
        namespace["dfs"] = {key: value for key, value in namespace.items() if isinstance(value, pd.DataFrame)}
        try:
            exec(cleaned, {"__builtins__": _SAFE_BUILTINS, "pd": pd}, namespace)  # noqa: S102
        except Exception as exc:
            return None, str(exc)[:300]
        return namespace.get("result"), ""

    def _generate(self, question: str, loaded: Sequence[tuple], selected: Dict[str, Any], model: str, language: str, error: str = "") -> str:
        schema = []
        for document, frames in loaded:
            stem = re.sub(r"\W+", "_", Path(str(document.get("filename") or "table")).stem) or "table"
            for sheet, frame in frames.items():
                key = stem if len(frames) == 1 else re.sub(r"\W+", "_", f"{stem}_{sheet}")
                preview = frame.head(4).to_csv(index=False)
                schema.append(f"Variable `{key}` columns={list(frame.columns)} rows={len(frame)}\n{preview}")
        repair = f"\nThe previous code failed: {error}\nReturn corrected code." if error else ""
        instruction = (
            "You are a pandas analyst. Use only the dataframes below. "
            "Assign the final value to `result`. Prefer a DataFrame or a number. "
            "Do not import anything and do not read files. Return only a python code block."
            if language != "tr"
            else "Pandas analistisin. Yalnızca aşağıdaki tabloları kullan. "
            "Sonucu `result` değişkenine yaz. DataFrame veya sayı döndür. "
            "Import etme ve dosya okuma. Yalnızca bir python kod bloğu yaz."
        )
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": f"Soru: {question}\n\n" + "\n\n".join(schema) + repair},
        ]
        response = self.router.complete(
            selected.get("id") or "local",
            messages,
            local_model=model,
            temperature=0.0,
            max_tokens=700,
            language=language,
        )
        return str(response.get("text") or "")

    @staticmethod
    def _citation(document: Dict[str, Any], index: int) -> Citation:
        meta = document.get("meta") or {}
        columns = ", ".join(str(item) for item in (meta.get("columns") or [])[:8])
        return Citation(
            id=f"{document.get('id')}:table",
            index=index,
            modality="office",
            doc_id=str(document.get("id") or ""),
            filename=str(document.get("filename") or ""),
            snippet=columns or str(document.get("filename") or ""),
            relevance=0.96,
            distance=0.04,
            department=str(meta.get("department") or ""),
            uploader=str(meta.get("uploaded_by_name") or ""),
            created_at=float(document.get("updated_at") or document.get("created_at") or 0) or None,
        )


_SAFE_BUILTINS = {
    "len": len,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "float": float,
    "int": int,
    "str": str,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
}


def _extract_code(text: str) -> str:
    fenced = re.findall(r"```(?:python)?\s*([\s\S]*?)```", text or "", flags=re.IGNORECASE)
    body = fenced[0] if fenced else (text or "")
    lines = []
    for line in body.splitlines():
        if line.strip().startswith("```"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def render_result(result: Any) -> str:
    if isinstance(result, pd.DataFrame):
        return _frame_markdown(result.head(30))
    if isinstance(result, pd.Series):
        return _frame_markdown(result.reset_index().head(30))
    if result is None:
        return ""
    return str(result)


def _frame_markdown(frame: pd.DataFrame) -> str:
    headers = [str(column) for column in frame.columns]
    if not headers:
        return ""
    rows = []
    for _, row in frame.iterrows():
        cells = []
        for value in row.tolist():
            if value is None or (isinstance(value, float) and pd.isna(value)):
                cells.append("—")
            else:
                cells.append(str(value).replace("|", "/").replace("\n", " "))
        rows.append(cells)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for cells in rows:
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
