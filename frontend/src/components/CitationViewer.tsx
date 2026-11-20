import { useEffect, useRef, useState } from "react";
import { X, FileText, Headphones, ImageIcon } from "lucide-react";
import { api, formatDuration } from "../lib/api";
import { useOmni } from "../lib/store";
import { roomLabel, t } from "../lib/i18n";
import type { Citation } from "../lib/types";

const STOPS = new Set([
  "nedir", "nasıl", "nasil", "hangi", "neden", "için", "icin", "what", "how", "which", "who", "where", "when", "does", "the", "and", "with",
]);

function mediaSrc(docId: string) {
  const token = localStorage.getItem("omnivault-token") || "";
  return `/api/documents/${docId}/media?token=${encodeURIComponent(token)}`;
}

function Highlighted({ text, query }: { text: string; query: string }) {
  const tokens = query
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((word) => word.length > 3 && !STOPS.has(word));
  const parts = text.split(/(?<=[.!?»])\s+/);
  return (
    <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-[#1d1d1f]">
      {parts.map((part, index) => {
        const hit = tokens.some((token) => part.toLowerCase().includes(token));
        return (
          <span key={`${index}-${part.slice(0, 12)}`}>
            {index > 0 ? " " : ""}
            {hit ? <mark className="source-hit">{part}</mark> : part}
          </span>
        );
      })}
    </p>
  );
}

export function CitationViewer() {
  const focus = useOmni((s) => s.citationFocus);
  const setCitationFocus = useOmni((s) => s.setCitationFocus);
  const lang = useOmni((s) => s.lang);
  const question = useOmni((s) => s.lastQuestion);
  const [fullText, setFullText] = useState("");

  useEffect(() => {
    if (!focus) return;
    let cancelled = false;
    setFullText("");
    api
      .documentText(focus.citation.doc_id)
      .then((result) => {
        if (!cancelled) setFullText(result.text || focus.citation.snippet);
      })
      .catch(() => {
        if (!cancelled) setFullText(focus.citation.snippet);
      });
    return () => {
      cancelled = true;
    };
  }, [focus]);

  if (!focus) return null;
  const citation = focus.citation;
  const when = citation.created_at
    ? new Date(citation.created_at * 1000).toLocaleString(lang === "tr" ? "tr-TR" : "en-GB")
    : "";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setCitationFocus(null)}>
      <div
        className="relative max-h-[92vh] w-full max-w-5xl overflow-hidden rounded-3xl bg-white shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-black/5 px-6 py-5">
          <div className="flex items-center gap-3">
            <span className="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-[#f5f5f7] text-[#0071e3]">
              {focus.kind === "audio" ? <Headphones size={18} /> : focus.kind === "image" ? <ImageIcon size={18} /> : <FileText size={18} />}
            </span>
            <div>
              <div className="break-words text-[17px] font-semibold">{citation.filename}</div>
              <div className="text-xs uppercase tracking-wider text-[#6e6e73]">[{citation.index}] {citation.modality}</div>
            </div>
          </div>
          <button type="button" onClick={() => setCitationFocus(null)} className="rounded-full bg-[#f5f5f7] p-2 text-[#1d1d1f]">
            <X size={18} />
          </button>
        </div>
        <div className="flex flex-wrap gap-2 border-b border-black/5 px-6 py-3">
          {citation.department && <span className="chip">{t(lang, "inspectorDept")}: {roomLabel(lang, citation.department, citation.department)}</span>}
          {citation.uploader && <span className="chip">{t(lang, "inspectorUploader")}: {citation.uploader}</span>}
          {when && <span className="chip">{t(lang, "inspectorDate")}: {when}</span>}
          <span className="chip">{t(lang, "inspectorScore")}: {Math.round(citation.relevance * 100)}%</span>
        </div>
        <div className="max-h-[calc(92vh-148px)] overflow-auto p-6">
          {focus.kind === "image" && <ImagePane citation={citation} text={fullText} query={question} lang={lang} />}
          {focus.kind === "audio" && <AudioPane citation={citation} text={fullText} query={question} lang={lang} />}
          {focus.kind === "pdf" && <TextPane citation={citation} text={fullText} query={question} lang={lang} />}
        </div>
      </div>
    </div>
  );
}

function TextPane({ citation, text, query, lang }: { citation: Citation; text: string; query: string; lang: "en" | "tr" }) {
  const [page, setPage] = useState<{ image_url: string } | null>(null);
  useEffect(() => {
    if (!citation.page) return;
    let cancelled = false;
    api.pdfPage(citation.doc_id, citation.page).then((result) => {
      if (!cancelled) setPage(result);
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [citation.doc_id, citation.page]);

  return (
    <div className={page ? "grid gap-5 lg:grid-cols-[1.1fr_0.9fr]" : ""}>
      {page && (
        <img src={`${page.image_url}?token=${encodeURIComponent(localStorage.getItem("omnivault-token") || "")}`} alt="" className="w-full rounded-2xl border border-black/5" />
      )}
      <div className="rounded-2xl bg-[#f5f5f7] p-5">
        <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-[#6e6e73]">{t(lang, "inspectorText")}</div>
        <Highlighted text={text || citation.snippet} query={query} />
      </div>
    </div>
  );
}

function AudioPane({ citation, text, query, lang }: { citation: Citation; text: string; query: string; lang: "en" | "tr" }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    const node = audioRef.current;
    if (!node || citation.start_time == null) return;
    const seek = () => {
      try {
        node.currentTime = Math.max(0, Number(citation.start_time) - 0.05);
      } catch {
        /* playback starts when the user presses play */
      }
    };
    if (node.readyState >= 1) seek();
    else node.addEventListener("loadedmetadata", seek, { once: true });
  }, [citation.doc_id, citation.start_time]);

  return (
    <div className="space-y-4">
      <div className="rounded-2xl bg-[#f5f5f7] p-5">
        {citation.start_time != null && (
          <div className="mb-3 text-sm text-[#6e6e73]">
            {formatDuration(citation.start_time)}
            {citation.end_time != null ? ` → ${formatDuration(citation.end_time)}` : ""}
          </div>
        )}
        <audio ref={audioRef} controls preload="auto" className="w-full" src={mediaSrc(citation.doc_id)} />
      </div>
      <div className="rounded-2xl bg-[#f5f5f7] p-5">
        <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-[#6e6e73]">{t(lang, "inspectorTranscript")}</div>
        <Highlighted text={text || citation.snippet} query={query} />
      </div>
    </div>
  );
}

function ImagePane({ citation, text, query, lang }: { citation: Citation; text: string; query: string; lang: "en" | "tr" }) {
  return (
    <div className="grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
      <img src={mediaSrc(citation.doc_id)} alt={citation.filename} className="max-h-[70vh] w-full rounded-2xl border border-black/5 object-contain" />
      <div className="rounded-2xl bg-[#f5f5f7] p-5">
        <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-[#6e6e73]">{t(lang, "inspectorOcr")}</div>
        <Highlighted text={text || citation.snippet} query={query} />
      </div>
    </div>
  );
}
