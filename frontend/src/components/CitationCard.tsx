import { FileAudio2, FileText, Image as ImageIcon } from "lucide-react";
import type { Citation } from "../lib/types";
import { formatDuration } from "../lib/api";
import { useOmni } from "../lib/store";
import { t } from "../lib/i18n";

const icons = {
  pdf: FileText,
  text: FileText,
  audio: FileAudio2,
  image: ImageIcon,
};

export function CitationCard({ citation }: { citation: Citation }) {
  const setCitationFocus = useOmni((s) => s.setCitationFocus);
  const lang = useOmni((s) => s.lang);
  const Icon = icons[(citation.modality as keyof typeof icons) || "text"] || FileText;

  return (
    <button
      type="button"
      onClick={() => {
        if (citation.modality === "audio") setCitationFocus({ kind: "audio", citation });
        else if (citation.modality === "image") setCitationFocus({ kind: "image", citation });
        else setCitationFocus({ kind: "pdf", citation });
      }}
      className="group card w-full p-4 text-left transition hover:border-[#0071e3]/30"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="citation-chip">[{citation.index}]</span>
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-xl bg-[#f5f5f7] text-[#0071e3]">
            <Icon size={16} />
          </span>
          <div>
            <div className="text-sm font-medium">{citation.filename}</div>
            <div className="text-xs uppercase tracking-wider text-[#6e6e73]">{citation.modality}</div>
          </div>
        </div>
        <div className="text-right text-xs text-[#6e6e73]">
          <div className="font-semibold text-[#0071e3]">{Math.round(citation.relevance * 100)}%</div>
          <div>{t(lang, "relevance")}</div>
        </div>
      </div>
      <p className="source-preview text-sm leading-relaxed text-[#424245]">{citation.snippet}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-[#6e6e73]">
        {citation.page != null && <span className="chip">{t(lang, "page")} {citation.page}</span>}
        {citation.start_time != null && (
          <span className="chip">
            {formatDuration(citation.start_time)} → {formatDuration(citation.end_time)}
          </span>
        )}
        <span className="rounded-full bg-[#0071e3] px-3 py-1 text-[12px] font-semibold text-white">{t(lang, "openSource")}</span>
      </div>
    </button>
  );
}
