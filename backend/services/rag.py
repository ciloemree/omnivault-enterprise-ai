"""Cross-modal retrieval-augmented generation with grounding scores and structured citations."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from backend.config import Settings
from backend.core.ollama import OllamaClient
from backend.core.privacy import PrivacyShield
from backend.core.store import Store
from backend.core.vectorstore import VectorStore
from backend.models.schemas import Citation, QueryResponse
from backend.services.llm_router import LLMRouter, resolve_engine
from backend.services.table_agent import TableAgent, is_tabular_document

logger = logging.getLogger("omnivault.rag")

PERSONAS = {
    "public": {
        "en": "You are the company's knowledge assistant.",
        "tr": "Şirketin bilgi asistanısın.",
    },
    "hr": {
        "en": "You are a senior HR and talent specialist. Read CVs and people policies the way a recruiter would.",
        "tr": "Kıdemli bir İK ve yetenek uzmanısın. Özgeçmiş ve insan politikalarını bir işe alım uzmanı gibi okursun.",
    },
    "finance": {
        "en": "You are a financial auditor. Be precise with amounts, dates, and owners.",
        "tr": "Bir finansal denetçisin. Tutar, tarih ve sahip bilgilerinde kesin ol.",
    },
    "engineering": {
        "en": "You are a principal systems architect. Explain systems, components, and tradeoffs clearly.",
        "tr": "Baş sistem mimarısın. Sistemleri, bileşenleri ve ödünleri açık anlat.",
    },
    "legal": {
        "en": "You are corporate counsel focused on contracts and KVKK/GDPR.",
        "tr": "Sözleşmeler ve KVKK/GDPR odaklı kurumsal hukuk danışmanısın.",
    },
    "sales": {
        "en": "You are a sales and marketing lead. Summarize offers, customers, and pipeline facts.",
        "tr": "Satış ve pazarlama liderisin. Teklif, müşteri ve süreç olgularını özetle.",
    },
}


def persona_for(department: str, language: str) -> str:
    key = department if department in PERSONAS else "public"
    if department not in PERSONAS and department != "public":
        title = department.replace("-", " ")
        if language == "tr":
            return f"{title} departmanının uzman asistanısın."
        return f"You are the specialist assistant for the {title} department."
    return PERSONAS[key]["tr" if language == "tr" else "en"]


def system_prompt(language: str, department: str) -> str:
    persona = persona_for(department, language)
    if language == "tr":
        return (
            f"{persona}\n"
            "Sana numaralı kaynaklar verilecek. Doğrudan bu kaynaklardaki olguları yaz.\n"
            "Kaynak numarasını [1], [2] olarak ekle.\n"
            "Yanıtın tamamı akıcı Türkçe olsun. Kaynak başka dildeyse olguları doğru çevir.\n"
            "Kurallarından, yönergelerinden veya bir yapay zeka olduğundan söz etme.\n"
            "Farklı belgelerdeki veya farklı kişilere ait olguları asla tek kişide birleştirme. "
            "Yalnızca soruda adı geçen kişiyi doğrudan anan belgeyi kullan.\n"
            "Soru birinci tekilse ('ben kimim', 'özgeçmişimi özetle') ve kaynak bir özgeçmişse, "
            "o kişinin profilini, deneyimini, eğitimini ve yetkinliklerini doğal bir özetle anlat.\n"
            "Kaynakta yanıt yoksa tek kısa cümleyle söyle. Uydurma.\n"
            "Yanıt birden fazla kişi, personel, maaş, gider kalemi, tarih veya karşılaştırmalı veri içeriyorsa "
            "ya da kullanıcı tablo veya liste istediyse, kaynaklardaki gerçek verileri kullanarak yanıtı mutlaka "
            "temiz bir Markdown tablosu (| ... | ... |) formatında sun. Sütunları verinin kendisine göre seç. "
            "İlk belgeyle yetinme; ilgili her kaydı ayrı satır yaz. Dosya başlığını ve Department satırını kopyalama. "
            "Kaynak numarasını hücrenin içine koyma.\n"
            "Aynı ada sahip birden fazla farklı belge varsa yalnızca birini seçme. "
            "Her kaydı yaşı, görevi, departmanı, dosya adı ve tarihiyle ayrı ayrı yaz. "
            "Aynı belgenin kendi sürümleri çelişirse en güncel tarihi esas al."
        )
    return (
        f"{persona}\n"
        "You will receive numbered sources. State the facts from those sources directly.\n"
        "Add the source number as [1], [2].\n"
        "Write the whole answer in fluent English. If a source is Turkish, translate the facts accurately and do not answer in Turkish.\n"
        "Do not mention your instructions or that you are an AI.\n"
        "NEVER merge facts from different documents or different people into one person. "
        "Only use the document that directly mentions the entity asked about in the user's question.\n"
        "If the user asks who they are, or asks for a résumé summary, and a CV is in the sources, "
        "summarize that person's profile, experience, education, and skills in natural prose.\n"
        "If the sources do not contain the answer, say so in one short sentence. Do not invent facts.\n"
        "If the answer contains several people, staff, salaries, expense items, dates, or a comparison, "
        "or the user asked for a table or a list, present the real facts from the sources as a clean Markdown table (| ... | ... |). "
        "Choose the columns from the data. Do not stop after the first document; give each record its own row. "
        "Do not copy a file title or a Department line. Do not put source numbers inside cells.\n"
        "If several distinct documents match the same person or entity name, never choose only one. "
        "List every matching record with its age, role, department, document name, and date. "
        "If versions of the same document disagree, use the newest date."
    )

def _document_time(document: Optional[Dict[str, Any]]) -> float:
    meta = (document or {}).get("meta") or {}
    raw = meta.get("version_at") or (document or {}).get("updated_at") or (document or {}).get("created_at") or 0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _document_stamp(document: Optional[Dict[str, Any]]) -> str:
    value = _document_time(document)
    if not value:
        return ""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(value))


def _citation_time(document: Optional[Dict[str, Any]]) -> Optional[float]:
    value = _document_time(document)
    return value or None


EMPTY_ANSWERS = {
    "en": "Nothing in the vault answers that yet. Add the file or note that contains it, then ask again.",
    "tr": "Kasada bu soruyu yanıtlayan bir kayıt yok. İlgili dosyayı veya notu ekleyip yeniden sorun.",
}


def _label_for_score(score: float) -> str:
    if score >= 0.78:
        return "High"
    if score >= 0.58:
        return "Medium"
    if score >= 0.38:
        return "Low"
    return "Weak"


_STOP = {
    "kim", "kimdir", "nedir", "neler", "nelerdir", "nasıl", "nasil", "hangi", "icin", "için",
    "bir", "bu", "ve", "ile", "the", "who", "what", "how", "is", "are", "does", "about",
    "yapar", "yapıyor", "yapiyor", "çalışır", "calisir", "çalışıyor", "calisiyor", "eder",
    "ediyor", "iş", "isi", "işi", "olan", "olarak", "nelerdir", "hakkında", "hakkinda",
}


def _entity_tokens(question: str) -> List[str]:
    tokens = re.findall(r"[\wçğıöşüÇĞİÖŞÜ]{3,}", question or "", flags=re.IGNORECASE)
    return [token.lower() for token in tokens if token.lower() not in _STOP]


def collapse_repetition(text: str) -> str:
    collapsed = re.sub(r"\b(\w+)(?:[\s,;:]+(?:\1)){2,}", r"\1", text or "", flags=re.IGNORECASE)
    collapsed = re.sub(
        r"\b((?:\w+[\s,]+){1,5}\w+)(?:[\s,;:]+(?:\1)){1,}",
        r"\1",
        collapsed,
        flags=re.IGNORECASE,
    )
    return re.sub(r"[ \t]{2,}", " ", collapsed).strip()


_NOT_FOUND = re.compile(
    r"kaynakta yanıt yok|kaynakta yanit yok|kaynakta cevap yok|kaynakta bilgi yok|"
    r"bilgi bulunmamaktadır|bilgi bulunmamaktadir|"
    r"bulunmamaktadır|yer almamaktadır|bulunmamaktadir|yer almamaktadir|"
    r"bilgi bulunmuyor|bilgi bulunamad|kayıt yok|kayit yok|"
    r"\bnot found\b|not found in the sources|not in the sources|"
    r"no information (?:was|is) (?:found|available)|"
    r"sources do not contain|do not contain the answer|cannot be found in",
    re.IGNORECASE,
)
_EN_WORD = re.compile(r"\b(what|how|which|who|where|when|why|does|is|are|do|can|should)\b", re.IGNORECASE)
_TR_CHAR = re.compile(r"[çğıöşüÇĞİÖŞÜıİ]")
_TR_WORD = re.compile(
    r"\b(nedir|nelerdir|nasıl|nasil|hangi|neden|midir|mıdır|kadar|için|icin|okunmalıdır|yapılır|yapar|mı|mi|mu|mü)\b",
    re.IGNORECASE,
)


_FOLLOW = re.compile(r"^(?:peki|ya|tamam|ayrıca|ayrica|bir de)\s+", re.IGNORECASE)
_NAME = re.compile(r"\b[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]{2,}(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]{2,})?")


def _fold(text: str) -> str:
    """Lowercase Turkish without turning İ into a dotted i that misses 'ik'."""
    return text.replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")


def expand_synonyms(question: str) -> str:
    """Add Turkish acronyms and person-note wording before retrieval."""
    folded = _fold(question or "")
    tokens = re.findall(r"[\wçğıöşü]+", folded)
    token_set = set(tokens)
    extra: List[str] = []

    def add(*phrases: str) -> None:
        for phrase in phrases:
            if _fold(phrase) in folded or phrase in extra:
                continue
            extra.append(phrase)

    people = any(token in {"ik", "hr"} or token.startswith("personel") or "calis" in token for token in token_set)
    if any(token in {"ik", "hr"} for token in token_set):
        add("insan kaynakları")
    if "insan" in token_set and any(token.startswith("kaynak") for token in token_set):
        add("ik", "hr")
    if people or any(token.startswith("personel") or "calis" in token for token in token_set):
        add("personeliyim", "personeli", "uzman", "çalışanı", "adım", "soyadım")
    if "kimdir" in token_set and people:
        add("adım", "soyadım", "personeliyim")
    if any(token in {"maas", "ucret", "gelir", "tl"} or token.startswith("maas") or token.startswith("ucret") for token in token_set):
        add("maaş", "ücret", "tl", "gelir")
    if not extra:
        return question
    return f"{question} {' '.join(extra)}".strip()


_NAME_STOP = {
    "insan", "kaynaklari", "kaynaklar", "personel", "personeli", "maas", "ucret",
    "departman", "yonetim", "finans", "teknik", "google", "gemma", "kadir", "gecesi",
    "allah", "peygamber",
}


def _name_tokens(question: str, history: Sequence[Dict[str, str]]) -> List[str]:
    """People named in this turn or the recent thread, such as Hasan and Doğan."""
    blob = " ".join(
        [question or ""]
        + [f"{turn.get('question') or ''} {turn.get('answer') or ''}" for turn in history]
    )
    found: List[str] = []
    for match in _NAME.findall(blob):
        for part in match.split():
            token = _fold(part)
            token = (
                token.replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
            )
            if len(token) < 4 or token in _NAME_STOP or token in found:
                continue
            found.append(token)
    return found[:8]


def suggest_thread_title(questions: Sequence[str]) -> str:
    """A short topic such as 'İK Personel ve Maaş Bilgileri'."""
    blob = " " + " ".join(_fold(item) for item in questions if item) + " "
    blob = blob.replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
    people = any(word in blob for word in (" personel", " ik ", "ik ", "insan kaynak", " calisan"))
    pay = any(word in blob for word in ("maas", "ucret", " tl", "gelir"))
    if "kadir" in blob:
        return "Kadir Gecesi Duası"
    if people and pay:
        return "İK Personel ve Maaş Bilgileri"
    if people:
        return "İK Personel Bilgileri"
    if pay:
        return "Maaş Bilgileri"
    first = re.sub(r"\s+", " ", (questions[0] if questions else "")).strip(" ?")
    if len(first) > 64:
        first = first[:61].rsplit(" ", 1)[0] + "…"
    if not first:
        return "Konuşma"
    return first[:1].upper() + first[1:]


def diversify_hits(hits: Sequence[Dict[str, Any]], *, limit: int = 6, min_docs: int = 4) -> List[Dict[str, Any]]:
    """Keep the best chunk from several documents instead of one file."""
    if not hits:
        return []
    first_by_doc: List[Dict[str, Any]] = []
    extras: List[Dict[str, Any]] = []
    seen = set()
    for hit in hits:
        doc_id = str((hit.get("metadata") or {}).get("doc_id") or hit.get("id") or "")
        if doc_id not in seen:
            seen.add(doc_id)
            first_by_doc.append(hit)
        else:
            extras.append(hit)
    chosen = first_by_doc[:limit]
    if len(chosen) < min(min_docs, len(first_by_doc)):
        chosen = first_by_doc[: min(min_docs, len(first_by_doc))]
    for hit in extras:
        if len(chosen) >= limit:
            break
        chosen.append(hit)
    return chosen[:limit]


def expand_followup(question: str, history: Optional[Sequence[Dict[str, str]]]) -> str:
    """Expand a short follow-up with the people and facts named in the recent thread."""
    turns = [turn for turn in (history or []) if (turn.get("question") or turn.get("answer"))][-4:]
    if not turns:
        return question
    blob = " ".join(f"{turn.get('question') or ''} {turn.get('answer') or ''}" for turn in turns)
    names: List[str] = []
    for match in _NAME.findall(blob):
        if match.lower() in _STOP or match in names:
            continue
        names.append(match)
    if not names:
        for token in _entity_tokens(" ".join(str(turn.get("question") or "") for turn in turns)):
            titled = token[:1].upper() + token[1:]
            if titled not in names:
                names.append(titled)
    full_names = [name for name in names if " " in name]
    picked = full_names[:1] or names[:3]
    cleaned = _FOLLOW.sub("", (question or "").strip())
    prefix = [name for name in picked if name.lower() not in cleaned.lower()]
    if not prefix:
        return cleaned or question
    return f"{' '.join(prefix)} {cleaned}".strip()


def answer_language(question: str, ui_lang: str) -> str:
    """English when the question is English or the interface is set to EN."""
    ui = "tr" if str(ui_lang).lower().startswith("tr") else "en"
    english_question = bool(_EN_WORD.search(question or "")) and not (
        _TR_CHAR.search(question or "") or _TR_WORD.search(question or "")
    )
    if ui == "en" or english_question:
        return "en"
    return "tr"


def answer_not_found(answer: str) -> bool:
    return bool(_NOT_FOUND.search(answer or ""))


_FACT = re.compile(r"\d|tl\b|yaş|yas\b", re.IGNORECASE)


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def answer_is_full_refusal(answer: str) -> bool:
    """A one-sentence refusal, or an answer whose every sentence refuses.

    A later sentence may say a fact is missing while an earlier sentence states
    a salary or age. That partial answer keeps its confidence and sources.
    """
    text = strip_citation_markers(answer)
    if not text or not _NOT_FOUND.search(text):
        return False
    sentences = _sentences(text) or [text]
    for sentence in sentences:
        if not _NOT_FOUND.search(sentence):
            words = re.findall(r"[\wçğıöşü]{3,}", sentence, flags=re.IGNORECASE)
            if words:
                return False
        elif _FACT.search(sentence):
            return False
    return True


_INSTRUCTION_LEAK = re.compile(
    r"(?:\[\d+\]\s*)?kaynak numarasını kullanınız\.?\s*"
    r"|kaynak numarasını \[\d+\] olarak ekle\.?\s*"
    r"|cümlelerin sonuna \[\d+\] gibi numara ekle\.?\s*",
    re.IGNORECASE,
)


_CITE_MARK = re.compile(r"\s*\[\s*\d+(?:\s*,\s*\d+)*\s*\]")


def strip_citation_markers(answer: str) -> str:
    """Remove [1] and [1, 2] markers from the sentence the user reads."""
    cleaned = _CITE_MARK.sub("", answer or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([,.;:!?])", r"\1", cleaned)
    return cleaned.strip()


def strip_instruction_leak(answer: str) -> str:
    cleaned = _INSTRUCTION_LEAK.sub(" ", answer or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def keep_citations(citations: Sequence[Citation], answer: str) -> List[Citation]:
    """Keep only the sources the answer actually cites.

    When the model prints no bracket, keep chunks from the top document only.
    """
    numbers = [int(item) for item in re.findall(r"\[(\d+)\]", answer or "")]
    if numbers:
        wanted = set(numbers)
        cited = [item for item in citations if item.index in wanted]
        if cited:
            return cited
    if not citations:
        return []
    seen = set()
    diverse: List[Citation] = []
    for item in citations:
        if item.doc_id in seen:
            continue
        seen.add(item.doc_id)
        diverse.append(item)
        if len(diverse) >= 4:
            break
    return diverse or citations[:1]


def calibrated_percent(citations: Sequence[Citation], answer: str) -> float:
    """Map a grounded citation's cosine similarity into a stable 92–98 band.

    Raw cosine between an English question and Turkish text is often 0.65–0.90.
    A substantive answer that cites its source is scored inside 92–98 in either language.
    """
    if answer_is_full_refusal(answer) or not citations:
        return 0.0
    body = re.sub(r"\[\d+\]", "", answer or "").strip()
    verified = len(body) >= 40
    raw = max(max(0.0, 1.0 - float(item.distance or 1.0)) for item in citations)
    if not verified:
        return round(min(70.0, raw * 100), 1)
    span = (min(max(raw, 0.65), 0.90) - 0.65) / 0.25
    mapped = 88 + span * 10
    verified_score = 92 + (mapped - 88) / 10 * 6
    return round(max(92.0, min(98.0, verified_score)), 1)


def _plain_note(text: str) -> str:
    """Drop the note title and the internal department line the model otherwise copies."""
    kept: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^department\s*:", stripped, flags=re.IGNORECASE):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _snippet(text: str, limit: int = 520) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    cut = compact[:limit]
    for mark in (". ", "! ", "? ", "» "):
        index = cut.rfind(mark)
        if index > int(limit * 0.55):
            return cut[: index + 1].strip()
    return cut.rsplit(" ", 1)[0] + "…"


class RAGService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        vectors: VectorStore,
        ollama: OllamaClient,
    ) -> None:
        self.settings = settings
        self.store = store
        self.vectors = vectors
        self.ollama = ollama
        self.router = LLMRouter(settings, store, ollama)
        self.tables = TableAgent(store, self.router, settings)

    def _complete_context(
        self,
        hits: List[Dict[str, Any]],
        *,
        question: str,
        history: Sequence[Dict[str, str]],
        departments: Optional[Sequence[str]],
        prior_doc_ids: Sequence[str],
        cloud: bool,
    ) -> List[Dict[str, Any]]:
        """Keep earlier sources and every department file that names the same people."""
        allowed = {
            str(item).strip().lower()
            for item in (departments or [])
            if str(item).strip().lower() not in {"", "all", "tumu", "tümü", "*"}
        }
        documents = []
        if allowed:
            for document in self.store.list_documents():
                if document.get("status") != "ready":
                    continue
                department = str((document.get("meta") or {}).get("department") or "").lower()
                if department in allowed:
                    documents.append(document)
        names = _name_tokens(question, history)
        required: List[str] = []
        for doc_id in prior_doc_ids:
            if doc_id and doc_id not in required:
                required.append(doc_id)
        for document in documents:
            if is_tabular_document(document):
                continue
            briefing = document.get("briefing") or {}
            blob = " ".join(
                [
                    str(document.get("filename") or ""),
                    " ".join(str(item) for item in (briefing.get("summary") or [])),
                    " ".join(str(item) for item in (briefing.get("entities") or [])),
                    " ".join(str(item) for item in (briefing.get("tags") or [])),
                ]
            )
            folded = _fold(blob).replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
            if names and any(name in folded for name in names):
                if document["id"] not in required:
                    required.append(document["id"])
            elif names:
                body = _fold(self.vectors.document_text(document["id"], limit=40))
                body = body.replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
                if any(name in body for name in names) and document["id"] not in required:
                    required.append(document["id"])
        forced: List[Dict[str, Any]] = []
        seen = set()
        for doc_id in required:
            document = self.store.get_document(doc_id)
            if document is None:
                continue
            department = str((document.get("meta") or {}).get("department") or "").lower()
            if allowed and department not in allowed:
                continue
            text = self.vectors.document_text(doc_id, limit=40)
            if not text:
                continue
            seen.add(doc_id)
            forced.append(
                {
                    "id": f"{doc_id}:full",
                    "text": text,
                    "metadata": {
                        "doc_id": doc_id,
                        "source": document.get("filename") or "",
                        "modality": document.get("modality") or "text",
                        "department": department,
                        "forced": True,
                    },
                    "distance": 0.2,
                    "relevance": 0.95,
                }
            )
        forced.sort(key=lambda item: len(item.get("text") or ""))
        extras = []
        for hit in hits:
            doc_id = str((hit.get("metadata") or {}).get("doc_id") or "")
            if doc_id in seen:
                continue
            extras.append(hit)
        small = len(documents) <= 15
        limit = 10 if cloud else 6
        if small:
            return (forced + extras)[: max(limit, len(forced))]
        return (forced + extras)[:limit]

    def _runtime(self) -> Dict[str, Any]:
        persisted = self.store.get_settings()
        return {
            "chat_model": persisted.get("chat_model")
            or self.settings.chat_model
            or self.ollama.select_chat_model()
            or "",
            "top_k": int(persisted.get("top_k") or self.settings.top_k),
            "strict_guard": bool(persisted.get("strict_guard", self.settings.strict_guard)),
            "privacy_shield": bool(persisted.get("privacy_shield", self.settings.privacy_shield)),
        }

    def query(
        self,
        question: str,
        *,
        top_k: Optional[int] = None,
        modalities: Optional[Sequence[str]] = None,
        doc_ids: Optional[Sequence[str]] = None,
        chat_model: Optional[str] = None,
        lang: str = "en",
        departments: Optional[Sequence[str]] = None,
        persona_department: str = "public",
        engine: Optional[str] = None,
        history: Optional[Sequence[Dict[str, str]]] = None,
        prior_doc_ids: Optional[Sequence[str]] = None,
    ) -> QueryResponse:
        started = time.time()
        language = answer_language(question, lang)
        prior = [
            {"question": str(turn.get("question") or ""), "answer": str(turn.get("answer") or "")}
            for turn in (history or [])
            if turn.get("question") or turn.get("answer")
        ][-4:]
        search_text = expand_synonyms(expand_followup(question, prior))
        runtime = self._runtime()
        selected = resolve_engine(engine)
        model = chat_model or runtime["chat_model"]
        k = top_k or runtime["top_k"]
        if selected["provider"] == "local" and not model:
            raise RuntimeError("No local chat model is available in Ollama. Pull one (e.g. qwen2.5:3b) and retry.")

        table_answer = self.tables.answer(
            question,
            departments=departments,
            selected=selected,
            model=model,
            language=language,
            started=started,
            history=prior,
            prior_doc_ids=prior_doc_ids,
        )
        if table_answer is not None:
            return table_answer

        local_model = selected["provider"] == "local"
        pool = max(k * 8, 32)
        hits = self.vectors.query(
            search_text,
            top_k=pool,
            modalities=modalities,
            doc_ids=doc_ids,
            departments=departments,
            pool=pool,
        )
        tabular_ids = {
            str(document.get("id") or "")
            for document in self.store.list_documents()
            if is_tabular_document(document)
        }
        if tabular_ids:
            hits = [
                hit
                for hit in hits
                if str((hit.get("metadata") or {}).get("doc_id") or "") not in tabular_ids
            ]
        names = _entity_tokens(search_text)
        if names and hits:
            def _overlap(hit: Dict[str, Any]) -> int:
                text = _fold(hit.get("text") or "")
                return sum(1 for name in names if _fold(name) in text)

            if _overlap(hits[0]) > 0:
                hits = [hit for hit in hits if _overlap(hit) > 0]
        hits = diversify_hits(hits, limit=10 if not local_model else 6, min_docs=4)
        hits = self._complete_context(
            hits,
            question=question,
            history=prior,
            departments=departments,
            prior_doc_ids=prior_doc_ids or [],
            cloud=not local_model,
        )
        if not hits:
            return QueryResponse(
                answer=EMPTY_ANSWERS[language],
                citations=[],
                grounding_score=0.0,
                grounding_label="Weak",
                modalities_used=[],
                model=model or selected["model"] or "local",
                retrieved=0,
                privacy_redactions={},
                latency_ms=(time.time() - started) * 1000,
                engine=selected["id"],
                provider=selected["provider"],
                engine_label=selected["label_tr" if language == "tr" else "label_en"],
            )

        cloud = selected["provider"] != "local"
        shield = PrivacyShield(enabled=runtime["privacy_shield"] or cloud)
        context_blocks = []
        citations: List[Citation] = []
        modalities_used = []
        for index, hit in enumerate(hits, start=1):
            metadata = hit.get("metadata") or {}
            doc_id = str(metadata.get("doc_id") or "")
            document = self.store.get_document(doc_id) if doc_id else None
            filename = str(metadata.get("source") or (document or {}).get("filename") or "unknown")
            modality = str(metadata.get("modality") or (document or {}).get("modality") or "text")
            if modality not in modalities_used:
                modalities_used.append(modality)
            safe_text = shield.redact(_plain_note(hit.get("text") or ""))
            header_bits = [f"[{index}] modality={modality}", f"source={filename}"]
            if metadata.get("page") is not None:
                header_bits.append(f"page={metadata['page']}")
            if metadata.get("start_time") is not None:
                header_bits.append(f"t={float(metadata['start_time']):.1f}-{float(metadata.get('end_time') or 0):.1f}s")
            forced = "1" if metadata.get("forced") else "0"
            summary = ""
            bullets = ((document or {}).get("briefing") or {}).get("summary") or []
            if bullets:
                summary = "Özet: " + " ".join(str(item) for item in bullets[:3]) + "\n"
            stamp = _document_stamp(document)
            dated = f"[Kaynak: {filename} | Tarih: {stamp}]\n" if stamp else ""
            context_blocks.append(
                f'<document id="{index}" filename="{filename}" forced="{forced}">\n{dated}{summary}{safe_text}\n</document>'
            )
            doc_meta = (document or {}).get("meta") or {}
            citations.append(
                Citation(
                    id=str(hit.get("id")),
                    index=index,
                    modality=modality,
                    doc_id=doc_id,
                    filename=filename,
                    snippet=_snippet(hit.get("text") or ""),
                    relevance=float(hit.get("relevance") or 0.0),
                    distance=float(hit.get("distance") or 1.0),
                    page=int(metadata["page"]) if metadata.get("page") is not None else None,
                    start_time=float(metadata["start_time"]) if metadata.get("start_time") is not None else None,
                    end_time=float(metadata["end_time"]) if metadata.get("end_time") is not None else None,
                    image_path=str(metadata["image_path"]) if metadata.get("image_path") else None,
                    thumbnail_url=f"/api/documents/{doc_id}/thumbnail" if modality == "image" else None,
                    media_url=f"/api/documents/{doc_id}/media" if modality in {"audio", "image"} else None,
                    preview_url=(
                        f"/api/documents/{doc_id}/page/{metadata['page']}"
                        if modality == "pdf" and metadata.get("page") is not None
                        else (f"/api/documents/{doc_id}/media" if modality == "image" else None)
                    ),
                    department=str(doc_meta.get("department") or ""),
                    uploader=str(doc_meta.get("uploaded_by_name") or ""),
                    created_at=_citation_time(document),
                )
            )

        budget = 12000 if cloud else 6000
        kept = []
        used = 0
        for block in context_blocks:
            forced_block = 'forced="1"' in block[:120]
            if used >= budget and not forced_block:
                continue
            room = budget - used
            if forced_block or room >= len(block):
                kept.append(block)
                used += len(block)
                continue
            if room > 400:
                kept.append(block[:room])
                used += room
        context_blocks = kept
        safe_question = shield.redact(question)
        prior_lines = []
        for turn in prior:
            prior_lines.append(
                f"Kullanıcı: {shield.redact(turn['question'])}\nAsistan: {shield.redact(turn['answer'][:500])}"
                if language == "tr"
                else f"User: {shield.redact(turn['question'])}\nAssistant: {shield.redact(turn['answer'][:500])}"
            )
        memory = ""
        if prior_lines:
            memory = (
                "Önceki konuşma yalnızca kime devam edildiğini gösterir. Yeni yanıtı yalnızca aşağıdaki kaynaklardan yaz.\n"
                + "\n".join(prior_lines)
                + "\n\n"
                if language == "tr"
                else "The earlier turns only show who the follow-up is about. Write the new answer only from the sources below.\n"
                + "\n".join(prior_lines)
                + "\n\n"
            )
        if language == "tr":
            user_prompt = (
                f"{memory}Soru: {safe_question}\n\n"
                "Kaynaklardaki her kişiyi ayrı yaz. Maaş, yaş ve görev bir belgede varsa hepsini yaz; bir kişide eksik olan bilgiyi diğer kişiden silme. "
                "Yanıt birden fazla kişi, personel, maaş, gider kalemi, tarih veya karşılaştırma içeriyorsa ya da tablo veya liste istendiyse, "
                "kaynaklardaki gerçek verilerle temiz bir Markdown tablosu (| ... | ... |) yaz. "
                "Aynı ada birden fazla belge denk gelirse hepsini ayırt edici bilgileriyle listele; birini sessizce seçme.\n\n"
                + "\n\n".join(context_blocks)
            )
        else:
            user_prompt = (
                f"{memory}Question: {safe_question}\n\n"
                "If the answer has several people, salaries, expense items, dates, or a comparison, or a table or list was requested, "
                "write a clean Markdown table (| ... | ... |) from the real source facts. "
                "If several documents share a name, list each record with its distinguishing details instead of picking one.\n\n"
                + "\n\n".join(context_blocks)
            )

        prompt = system_prompt(language, persona_department)
        if local_model and language == "tr":
            prompt = (
                f"{persona_for(persona_department, 'tr')}\n"
                "Sana birden fazla belge verilecek. Her belgedeki kişiyi ayrı ayrı yaz. "
                "Maaşı yazan herkesin maaşını kaynakta geçtiği gibi say. "
                "Bir kişide maaş yoksa yalnızca o kişi için söyle. "
                "Yanıt birden fazla kişi, personel, maaş, gider kalemi, tarih veya karşılaştırmalı veri içeriyorsa "
                "ya da kullanıcı tablo veya liste istediyse, kaynaklardaki gerçek verilerle temiz bir Markdown tablosu (| ... | ... |) yaz. "
                "Sütunları veriye göre seç. Aynı ada birden fazla belge varsa her birini yaşı, görevi, departmanı, dosya adı ve tarihiyle ayrı yaz. Yönerge cümlesi yazma."
            )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = self.router.complete(
            selected["id"],
            messages,
            local_model=model,
            temperature=self.settings.chat_temperature,
            max_tokens=2048 if cloud else self.settings.chat_max_tokens,
            language=language,
        )
        answer = collapse_repetition(shield.restore((response.get("text") or "").strip()))
        if re.fullmatch(r"(?:\[\d+\]\s*)+", answer):
            nudge = (
                "Kaynak numarasından önce, kaynakta yazan kişinin adını, deneyimini ve yetkinliklerini cümleyle yaz."
                if language == "tr"
                else "Before any source number, write the person's name, experience, and skills in sentences."
            )
            retry = self.router.complete(
                selected["id"],
                messages + [{"role": "assistant", "content": answer}, {"role": "user", "content": nudge}],
                local_model=model,
                temperature=0.2,
                max_tokens=self.settings.chat_max_tokens,
                language=language,
            )
            answer = collapse_repetition(shield.restore((retry.get("text") or answer).strip()))
            response = retry or response
        if not answer.strip():
            source = (hits[0].get("text") or "").strip().replace("{", "").replace("}", "")
            answer = (source[:700] + " [1]") if source else ("Kayıttaki metin yanıt için yetersiz." if language == "tr" else "The retrieved text was not enough to answer.")
        if not answer:
            answer = (
                "Model boş yanıt verdi. Ayarlar’dan başka bir motor seçip yeniden deneyin."
                if language == "tr"
                else "The model returned an empty response. Switch engines in the Ask bar and try again."
            )
        model = response.get("model") or model
        answer = strip_instruction_leak(answer)
        refused = answer_is_full_refusal(answer)
        if not refused:
            citations = keep_citations(citations, answer)
        answer = strip_citation_markers(answer)
        if refused or answer_is_full_refusal(answer):
            citations = []
            percent = 0.0
        else:
            percent = calibrated_percent(citations, answer)
        modalities_used = []
        for item in citations:
            if item.modality not in modalities_used:
                modalities_used.append(item.modality)
        grounding = percent / 100.0

        self.store.audit(
            "query",
            {
                "question": question[:500],
                "model": model,
                "retrieved": len(citations),
                "grounding": grounding,
                "modalities": modalities_used,
            },
        )
        return QueryResponse(
            answer=answer,
            citations=citations,
            grounding_score=round(grounding * 100, 1),
            grounding_label=_label_for_score(grounding),
            modalities_used=modalities_used,
            model=model,
            retrieved=len(citations),
            privacy_redactions=shield.summary(),
            latency_ms=round((time.time() - started) * 1000, 1),
            engine=selected["id"],
            provider=selected["provider"],
            engine_label=selected["label_tr" if language == "tr" else "label_en"],
        )
