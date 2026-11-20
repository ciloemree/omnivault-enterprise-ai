"""Detect explicit vault-update commands and prepare a confirmable proposal."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_UPDATE = re.compile(
    r"(?:"
    r"(?P<subject>.+?)\s*(?:['’]?(?:nın|nin|nun|nün|ın|in|un|ün))\s+"
    r"(?P<field>maa[sş]|ya[sş]|ücret|ucret|gider)"
    r"(?:\s*(?:ı|i|u|ü|ını|ini|unu|ünü|nı|ni|nu|nü))?"
    r"\s*(?:yı|yi|yu|yü|yı|nı|ni)?"
    r"\s*(?P<value>\d[\d.\s]*?(?:\s*bin)?\s*(?:TL|tl|yaş|yas)?)"
    r"\s*(?:olarak\s+)?"
    r"(?P<verb>yap|güncelle|guncelle|değiştir|degistir|çek|cek)"
    r"|"
    r"(?P<field2>kira|elektrik|su|doğalgaz|dogalgaz|internet|maaş|maas|gider)\s*"
    r"(?:gideri(?:ni)?|tutarı(?:nı)?|tutari(?:ni)?)?\s*"
    r"(?P<value2>\d[\d.\s]*?(?:\s*bin)?\s*TL)\s*"
    r"(?:olarak\s+)?"
    r"(?P<verb2>yap|güncelle|guncelle|değiştir|degistir)"
    r")",
    re.IGNORECASE,
)

_MONEY = re.compile(r"(\d+(?:[.\s]\d{3})*|\d+)\s*(bin)?\s*tl\b", re.IGNORECASE)
_AGE = re.compile(r"(\d{1,3})\s*ya[sş]\w*", re.IGNORECASE)


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


def _normalize_money(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", (raw or "").strip())
    match = re.search(r"(\d[\d.\s]*)(?:\s*(bin))?\s*(?:TL|tl)?", cleaned, flags=re.IGNORECASE)
    if not match:
        return cleaned
    digits = match.group(1).replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", digits):
        number = digits
    else:
        number = digits.replace(".", "")
    if match.group(2):
        return f"{number} bin TL"
    return f"{number} TL"


def _normalize_age(raw: str) -> str:
    match = re.search(r"(\d{1,3})", raw or "")
    return match.group(1) if match else raw.strip()


def parse_update_command(question: str) -> Optional[Dict[str, str]]:
    """Return subject, field, and value when the user clearly asks to change a vault fact."""
    text = re.sub(r"\s+", " ", (question or "").strip())
    if not text:
        return None
    match = _UPDATE.search(text)
    if not match:
        return None
    if match.group("field"):
        subject = re.sub(r"\s+", " ", (match.group("subject") or "").strip(" ,."))
        field = _fold(match.group("field") or "")
        value = match.group("value") or ""
    else:
        subject = match.group("field2") or ""
        field = _fold(match.group("field2") or "")
        value = match.group("value2") or ""
    if field in {"maas", "ucret"}:
        kind = "salary"
        value = _normalize_money(value)
    elif field in {"yas"}:
        kind = "age"
        value = _normalize_age(value)
    else:
        kind = "expense"
        value = _normalize_money(value)
        if not subject:
            subject = match.group("field2") or "gider"
    subject = re.sub(r"^(lütfen|lutfen|admin|lütfen)\s+", "", subject, flags=re.IGNORECASE).strip()
    if len(_fold(subject)) < 3:
        return None
    return {"subject": subject, "kind": kind, "value": value}


def _read_body(document: Dict[str, Any], vectors: Any = None) -> str:
    edited = str((document.get("meta") or {}).get("edited_text") or "").strip()
    if len(edited) > 40:
        return edited
    path = Path(str(document.get("stored_path") or ""))
    if path.suffix.lower() in {".md", ".txt", ".markdown"} and path.exists() and path.stat().st_size < 200_000:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
    if vectors is not None:
        return vectors.document_text(str(document.get("id") or ""), limit=8)
    return ""


def _score_document(body: str, subject: str, kind: str) -> int:
    folded = _fold(body)
    subject_fold = _fold(subject)
    tokens = [token for token in re.findall(r"[a-zçğıöşü]{3,}", subject_fold) if token not in {"gider", "maas", "yas"}]
    score = 0
    for token in tokens:
        if token in folded:
            score += 3
    if subject_fold and subject_fold in folded:
        score += 5
    if kind == "salary" and ("maas" in folded or "tl" in folded):
        score += 2
    if kind == "age" and "yas" in folded:
        score += 2
    if kind == "expense" and any(word in folded for word in ("gider", "kira", "harcama", "butce")):
        score += 2
    return score


def _replace_salary(text: str, value: str) -> Tuple[str, str, str]:
    match = _MONEY.search(text or "")
    if not match:
        raise ValueError("No salary amount was found in that record.")
    old = match.group(0)
    updated = text[: match.start()] + value + text[match.end() :]
    return updated, old, value


def _replace_age(text: str, value: str) -> Tuple[str, str, str]:
    match = _AGE.search(text or "")
    if not match:
        raise ValueError("No age was found in that record.")
    old = match.group(0)
    updated = text[: match.start()] + f"{value} yaşında" + text[match.end() :]
    return updated, old, f"{value} yaş"


def _replace_expense(text: str, subject: str, value: str) -> Tuple[str, str, str]:
    folded_subject = _fold(subject)
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if folded_subject in _fold(line) and _MONEY.search(line):
            match = _MONEY.search(line)
            assert match is not None
            old = match.group(0)
            lines[index] = line[: match.start()] + value + line[match.end() :]
            return "\n".join(lines), old, value
    match = _MONEY.search(text or "")
    if not match:
        raise ValueError("No expense amount was found in that record.")
    old = match.group(0)
    updated = text[: match.start()] + value + text[match.end() :]
    return updated, old, value


def apply_change(text: str, kind: str, subject: str, value: str) -> Tuple[str, str, str]:
    if kind == "salary":
        return _replace_salary(text, value)
    if kind == "age":
        return _replace_age(text, value)
    return _replace_expense(text, subject, value)


def propose_update(
    question: str,
    *,
    departments: Optional[Sequence[str]],
    store: Any,
    vectors: Any,
) -> Optional[Dict[str, Any]]:
    command = parse_update_command(question)
    if not command:
        return None
    allowed = {
        str(item).strip().lower()
        for item in (departments or [])
        if str(item).strip().lower() not in {"", "all", "tumu", "tümü", "*"}
    }
    candidates: List[Tuple[int, Dict[str, Any], str]] = []
    for document in store.list_documents():
        if document.get("status") not in {"ready"}:
            continue
        owner = str((document.get("meta") or {}).get("department") or "").lower()
        if allowed and owner not in allowed:
            continue
        body = _read_body(document, vectors)
        if not body.strip():
            continue
        score = _score_document(body, command["subject"], command["kind"])
        if score >= 5:
            candidates.append((score, document, body))
    if not candidates:
        return {
            "error": "not_found",
            "subject": command["subject"],
            "kind": command["kind"],
            "value": command["value"],
        }
    candidates.sort(key=lambda item: (-item[0], len(item[2])))
    _, document, body = candidates[0]
    try:
        new_text, old_value, new_value = apply_change(body, command["kind"], command["subject"], command["value"])
    except ValueError as exc:
        return {"error": "no_value", "message": str(exc), "filename": document.get("filename")}
    if new_text == body:
        return {"error": "unchanged", "filename": document.get("filename")}
    return {
        "doc_id": str(document["id"]),
        "filename": str(document.get("filename") or ""),
        "department": str((document.get("meta") or {}).get("department") or ""),
        "old_snippet": old_value,
        "new_snippet": new_value,
        "old_text": body,
        "new_text": new_text,
        "kind": command["kind"],
        "subject": command["subject"],
    }
