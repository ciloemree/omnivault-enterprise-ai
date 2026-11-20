"""Answerable questions only.

A prompt is stored when the same text states the fact it asks about.
Conditional or unanswered clauses are skipped. Filenames never appear.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_BANNED = (
    "belge",
    "dosya",
    "görsel",
    "gorsel",
    "kayıt",
    "kayit",
    "nasıl açıklan",
    "belgesinde",
    "dosyasında",
    "görselde",
    "gorselde",
)
_FILE = re.compile(r"\S+\.(?:md|pdf|png|jpe?g|docx|xlsx|wav|mp3|webm|m4a|ogg|txt)\b", re.IGNORECASE)
_ACRONYM = {"ik", "uk", "hr", "bt", "it", "ai"}
_NAMED = re.compile(
    r"ad[ıi]m\s*:\s*([A-Za-zÇĞİÖŞÜçğıöşü]{2,})(?:\s+soyad[ıi]m\s*:\s*([A-Za-zÇĞİÖŞÜçğıöşü]{2,}))?",
    re.IGNORECASE,
)
_FULL_NAME = re.compile(
    r"ad\s+soyad\s*:\s*([A-Za-zÇĞİÖŞÜçğıöşü]{2,}(?:\s+[A-Za-zÇĞİÖŞÜçğıöşü]{2,})?)",
    re.IGNORECASE,
)
_SHORT_POSSESSIVE = re.compile(r"^(\S{1,2})'(?:un|ın|in|ün|u|ı)\b", re.IGNORECASE)


def _fold(value: str) -> str:
    text = value.replace("İ", "i").replace("I", "ı").lower()
    return text.translate(str.maketrans("ışğüöçâîû", "isguocaiu"))


def _proper(word: str) -> str:
    lowered = word.replace("İ", "i").replace("I", "ı").lower()
    return lowered[:1].upper() + lowered[1:]


def _possessive(name: str) -> str:
    lowered = name.replace("İ", "i").replace("I", "ı").lower()
    vowels = [char for char in lowered if char in "aeıioöuü"]
    last = vowels[-1] if vowels else "e"
    suffix = {"a": "ın", "ı": "ın", "e": "in", "i": "in", "o": "un", "u": "un", "ö": "ün", "ü": "ün"}[last]
    if lowered[-1:] in "aeıioöuü":
        suffix = "n" + suffix
    return f"{name}'{suffix}"


def _ok(question: str, english: bool = False) -> bool:
    words = question.split()
    folded = _fold(question)
    if _FILE.search(question):
        return False
    if english:
        low = question.lower()
        banned = ("filename", "document file", "image file", "the file", "the document")
        return 4 <= len(words) <= 12 and not any(token in low for token in banned)
    return 4 <= len(words) <= 9 and not any(token in folded for token in _BANNED)


def _prepare(text: str) -> str:
    cleaned = _FILE.sub(" ", text or "")
    cleaned = re.sub(r"(?im)^department\s*:\s*\S+\s*$", " ", cleaned)
    cleaned = re.sub(r"[{}#>*_\[\]()]", " ", cleaned)
    cleaned = re.sub(r"^[\-\*\u2022]+\s*", "", cleaned, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _real_name(token: str) -> Optional[str]:
    cleaned = re.sub(r"[^A-Za-zÇĞİÖŞÜçğıöşü]", "", token or "")
    if len(cleaned) < 3 or _fold(cleaned) in _ACRONYM:
        return None
    return _proper(cleaned)


def _explicit_person(text: str) -> Optional[str]:
    """A person only when the note says Adım: / Ad Soyad:, never a guessed capital token."""
    named = _NAMED.search(text or "")
    if named:
        first = _real_name(named.group(1))
        last = _real_name(named.group(2) or "")
        if first and last:
            return f"{first} {last}"
        return first
    full = _FULL_NAME.search(text or "")
    if not full:
        return None
    parts = [_real_name(part) for part in full.group(1).split()]
    kept = [part for part in parts if part]
    return " ".join(kept) or None


def _acceptable_prompt(question: str, english: bool = False) -> bool:
    if not _ok(question, english=english):
        return False
    if _SHORT_POSSESSIVE.match(question.strip()):
        return False
    return True


def _dua_stated(folded: str) -> bool:
    return any(token in folded for token in ("affet", "bagisla", "dua et", "allahim", "allah'im"))


def _hadith_stated(folded: str) -> bool:
    names = any(token in folded for token in ("aise", "aişe"))
    told = any(token in folded for token in ("buyur", "hadis", "rivayet", "tirmiz", "mace"))
    return names and told


def _night_stated(folded: str) -> bool:
    """A specific night is named, rather than only asked about."""
    asked = any(token in folded for token in ("bilecek olursam", "hangi gece oldugunu", "hangi gece olduğunu"))
    named = bool(re.search(r"\b(21|23|25|27|29)\b", folded)) or any(
        token in folded for token in ("yirmi yedi", "yirmiyedi", "yirmi birinci")
    )
    if asked and not named:
        return False
    return named


def questions_from_content(text: str, entities: List[str] | None = None) -> Dict[str, List[str]]:
    """Up to two Turkish questions and their English equivalents."""
    clean = _prepare(text)
    folded = _fold(clean)
    turkish: List[str] = []
    english: List[str] = []

    def add(tr: str, en: str) -> None:
        tr = re.sub(r"\s+", " ", tr).strip()
        en = re.sub(r"\s+", " ", en).strip()
        if _ok(tr) and _ok(en, english=True) and tr not in turkish:
            turkish.append(tr)
            english.append(en)

    if "kadir gecesi" in folded:
        if _dua_stated(folded):
            add("Kadir Gecesi'nde hangi dua okunmalıdır?", "Which prayer should be recited on Laylat al-Qadr?")
        if _hadith_stated(folded):
            add("Hz. Aişe'nin Kadir Gecesi duası hadisi nedir?", "What is Aisha's Laylat al-Qadr hadith?")
        if _night_stated(folded):
            add("Kadir Gecesi hangi gecedir?", "Which night is Laylat al-Qadr?")
        return {"tr": turkish[:2], "en": english[:2]}

    person = _explicit_person(text or "")
    role_stated = any(token in folded for token in ("uzman", "personel", "developer", "gorev", "muhendis", "calis", "yazilim"))
    short = len(clean) < 600
    if person and short and role_stated:
        add(f"{_possessive(person)} görevi ve uzmanlık alanı nedir?", f"What is {person}'s role and expertise?")
        if re.search(r"\d", text or "") and "maas" in folded:
            add(f"{_possessive(person)} maaşı ne kadardır?", f"How much is {person}'s salary?")
        elif any(token in folded for token in ("sirket", "calist")):
            add(f"{person} hangi şirketlerde çalıştı?", f"Which companies did {person} work for?")
        if len(turkish) >= 2:
            return {"tr": turkish[:2], "en": english[:2]}

    hr = "insan kaynak" in folded
    duties = sum(token in folded for token in ("ise al", "mulakat", "ozluk", "performans", "egitim", "aday", "calisan"))
    if hr and duties >= 1:
        add("İnsan kaynakları departmanı ne iş yapar?", "What does the HR department do?")
        if sum(token in folded for token in ("mulakat", "aday", "pozisyon", "ise al", "gorusme")) >= 2:
            add("İşe alım süreci nasıl ilerler?", "How does the recruitment process work?")
        elif "ozluk" in folded:
            add("Özlük bilgileri neleri kapsar?", "What do personnel records cover?")
        if len(turkish) >= 2:
            return {"tr": turkish[:2], "en": english[:2]}

    if re.search(r"\d", text or "") and any(token in folded for token in ("kira", "maas", "gider", "masraf")):
        if "kira" in folded and "maas" in folded:
            add("Aylık kira ve maaş giderleri ne kadardır?", "How much are the monthly rent and salaries?")
        elif "kira" in folded:
            add("Aylık kira gideri ne kadardır?", "How much is the monthly rent?")
        elif "maas" in folded:
            add("Aylık maaş tutarı ne kadardır?", "How much is the monthly payroll?")
        if "sgk" in folded:
            add("İşçi SGK gideri ne kadardır?", "How much is the employee social-security cost?")
        elif "masraf" in folded and len(turkish) < 2:
            add("Diğer masraflar ne kadar tutar?", "How much are the other expenses?")
        if len(turkish) >= 2:
            return {"tr": turkish[:2], "en": english[:2]}

    if any(token in folded for token in ("react", "typescript", "developer", "yazilim", "gelistirici")):
        add("Yazılım uzmanlığı hangi teknolojileri kapsar?", "Which technologies does the software work cover?")
    return {"tr": turkish[:2], "en": english[:2]}


def suggest_questions(documents: List[Dict[str, Any]], lang: str = "en", limit: int = 4) -> List[str]:
    english = str(lang).lower().startswith("en")
    key = "suggested_questions_en" if english else "suggested_questions_tr"
    questions: List[str] = []
    for document in documents:
        if document.get("status") != "ready":
            continue
        meta = document.get("meta") or {}
        stored = meta.get(key) or ([] if english else meta.get("suggested_questions") or [])
        for item in stored:
            text = str(item)
            if text not in questions and _acceptable_prompt(text, english=english):
                questions.append(text)
        if len(questions) >= limit:
            break
    return questions[:limit]
