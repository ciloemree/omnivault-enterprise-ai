import { useState } from "react";
import {
  FileAudio2,
  FileText,
  Image as ImageIcon,
  RefreshCw,
  Trash2,
  Sparkles,
} from "lucide-react";
import clsx from "clsx";
import type { DocumentItem } from "../lib/types";
import { formatBytes, formatClock, formatDuration } from "../lib/api";
import { StatusPill } from "./StatusPill";
import { api } from "../lib/api";
import { useOmni } from "../lib/store";
import { statusLabel, t } from "../lib/i18n";

const modalityIcon = {
  pdf: FileText,
  text: FileText,
  audio: FileAudio2,
  image: ImageIcon,
};

function toneForStatus(status: string) {
  if (status === "ready") return "good" as const;
  if (status === "error") return "bad" as const;
  if (status === "duplicate") return "neutral" as const;
  return "accent" as const;
}

export function DocumentCard({ document }: { document: DocumentItem }) {
  const remove = useOmni((s) => s.remove);
  const upsertDocument = useOmni((s) => s.upsertDocument);
  const setToast = useOmni((s) => s.setToast);
  const lang = useOmni((s) => s.lang);
  const user = useOmni((s) => s.user);
  const transferDocument = useOmni((s) => s.transferDocument);
  const canDelete = user?.role === "admin" || user?.permission === "dept_admin";
  const canTransfer = user?.role === "admin" || (user?.permission != null && user.permission !== "read_only");
  const [transferOpen, setTransferOpen] = useState(false);
  const [departments, setDepartments] = useState<Array<{ slug: string; name: string }>>([]);
  const [target, setTarget] = useState("");
  const [transferring, setTransferring] = useState(false);
  const askConfirm = useOmni((s) => s.askConfirm);
  const pushToast = useOmni((s) => s.pushToast);
  const [editing, setEditing] = useState(false);
  const [preview, setPreview] = useState(false);
  const [draft, setDraft] = useState("");
  const fileUrl = `/api/files/${document.id}/raw?token=${encodeURIComponent(localStorage.getItem("omnivault-token") || "")}`;
  const Icon = modalityIcon[(document.modality as keyof typeof modalityIcon) || "text"] || FileText;
  const briefing = document.briefing;

  return (
    <article className="card overflow-hidden">
      <div className="flex items-start justify-between gap-3 border-b border-black/5 px-5 py-4">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[#f5f5f7] text-[#0071e3]">
            <Icon size={18} />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold">{document.filename}</h3>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[#6e6e73]">
              <span className="uppercase tracking-wider">{document.modality}</span>
              <span className="chip">{String(document.meta?.department || "yonetim")}</span>
              <span>Ekleyen: {String(document.meta?.uploaded_by_name || "Kurumsal Arşiv (Genel)")}</span>
              {Boolean(document.meta?.table_agent) || /\.(csv|xlsx|xls)$/i.test(document.filename) ? (
                <span className="chip">
                  {t(lang, "tableAgent")}
                  {document.meta?.row_count != null ? ` · ${String(document.meta.row_count)}` : ""}
                </span>
              ) : (
                <span className="chip">{document.chunk_count || 0} {t(lang, "historyVectors")}</span>
              )}
              {Number(document.meta?.version) > 1 && (
                <span className="chip">v{String(document.meta.version)}</span>
              )}
              <span>·</span>
              <span>{formatBytes(document.size_bytes)}</span>
              <span>·</span>
              <span>{formatClock(document.created_at)}</span>
              {document.origin === "demo" && (
                <span className="chip">{t(lang, "demo")}</span>
              )}
            </div>
          </div>
        </div>
        <StatusPill label={statusLabel(lang, document.status)} tone={toneForStatus(document.status)} />
      </div>

      <div className="space-y-4 px-5 py-4">
        {(document.status === "queued" ||
          document.status === "processing" ||
          document.status === "indexing" ||
          document.status === "briefing") && (
          <div>
            <div className="mb-2 flex items-center justify-between text-xs text-[#6e6e73]">
              <span>{document.message || document.stage || "Working…"}</span>
              <span>{Math.round((document.progress || 0) * 100)}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-[#f5f5f7]">
              <div
                className="h-full rounded-full bg-[#0071e3] transition-all"
                style={{ width: `${Math.max(4, Math.min(100, (document.progress || 0) * 100))}%` }}
              />
            </div>
          </div>
        )}

        {document.error && (
          <div className="rounded-2xl border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-200">
            {document.error}
          </div>
        )}

        {briefing && (
          <div className="rounded-2xl bg-[#f5f5f7] p-4">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[#6e6e73]">
              <Sparkles size={14} /> {t(lang, "briefing")}
            </div>
            <ul className="space-y-2 text-sm leading-relaxed text-[#1d1d1f]">
              {(briefing.summary || []).slice(0, 3).map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#0071e3]" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
            <div className="mt-3 flex flex-wrap gap-2">
              {(briefing.tags || []).map((tag) => (
                <span key={tag} className="chip">
                  {tag}
                </span>
              ))}
            </div>
            {(briefing.entities || []).length > 0 && (
              <div className="mt-3 text-xs text-[#6e6e73]">
                {t(lang, "entities")}: {(briefing.entities || []).slice(0, 8).join(" · ")}
              </div>
            )}
          </div>
        )}

        <div className="flex flex-wrap gap-2 text-[11px] text-[#6e6e73]">
          {Boolean(document.meta?.table_agent) || /\.(csv|xlsx|xls)$/i.test(document.filename) ? (
            <MetaChip label={`${String(document.meta?.row_count ?? 0)} ${t(lang, "tableRows")}`} />
          ) : (
            <MetaChip label={`${document.chunk_count} ${t(lang, "chunks")}`} />
          )}
          {document.modality === "pdf" && document.meta?.page_count != null && (
            <MetaChip label={`${document.meta.page_count} ${t(lang, "pages")}`} />
          )}
          {document.modality === "audio" && document.meta?.duration != null && (
            <MetaChip label={formatDuration(Number(document.meta.duration))} />
          )}
          {document.modality === "image" && document.meta?.ocr_engine != null && (
            <MetaChip label={`OCR: ${String(document.meta.ocr_engine)}`} />
          )}
          {document.embed_model && document.embed_model !== "table-agent" && <MetaChip label={document.embed_model} />}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-black/5 px-5 py-3">
        <div className="text-xs text-slate-500">
          {document.modality === "image" && (
            <img
              src={`/api/files/${document.id}/raw?token=${encodeURIComponent(localStorage.getItem("omnivault-token") || "")}`}
              alt=""
              className="h-12 w-20 rounded-lg border border-white/8 object-cover"
            />
          )}
        </div>
        <div className="doc-actions flex flex-wrap items-center justify-end gap-2">
          <button type="button" className="btn-secondary text-xs" onClick={() => setPreview(true)}>
            {t(lang, "openFile")}
          </button>
          {document.status !== "duplicate" && (
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-1.5 text-xs"
              onClick={async () => {
                const stored = await api.documentText(document.id);
                setDraft(stored.text || "");
                setEditing(true);
              }}
            >
              {t(lang, "editText")}
            </button>
          )}
          {document.status !== "duplicate" && (
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-1.5 text-xs"
              onClick={async () => {
                const updated = await api.reindex(document.id);
                upsertDocument(updated);
                setToast(lang === "tr" ? `${document.filename} yeniden okunacak` : `${document.filename} will be read again`);
              }}
            >
              <RefreshCw size={14} /> {t(lang, "reindex")}
            </button>
          )}
          {canTransfer && document.status !== "duplicate" && (
            <button
              type="button"
              className="btn-secondary text-xs"
              onClick={async () => {
                const next = !transferOpen;
                setTransferOpen(next);
                if (next && !departments.length) {
                  const options = await api.departmentOptions();
                  const choices = options.filter((item) => item.slug !== String(document.meta?.department || ""));
                  setDepartments(choices);
                  setTarget(choices[0]?.slug || "");
                }
              }}
            >
              {t(lang, "transfer")}
            </button>
          )}
          {canDelete && (
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-full bg-[#ff3b30] px-3 py-2 text-xs font-semibold text-white"
            onClick={async () => {
              if (!(await askConfirm())) return;
              await remove(document.id);
            }}
          >
            <Trash2 size={14} /> {t(lang, "delete")}
          </button>
          )}
        </div>
        {transferOpen && (
          <div className="mt-3 flex w-full basis-full flex-wrap items-end gap-2 rounded-2xl bg-[#f5f5f7] p-3">
            <label className="min-w-[180px] flex-1 text-xs font-medium text-[#6e6e73]">
              {t(lang, "transferTarget")}
              <select className="field mt-1" value={target} onChange={(event) => setTarget(event.target.value)}>
                {departments.map((item) => (
                  <option key={item.slug} value={item.slug}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn-primary text-xs"
              disabled={!target || transferring}
              onClick={async () => {
                setTransferring(true);
                try {
                  await transferDocument(document.id, target, "move");
                  setTransferOpen(false);
                } finally {
                  setTransferring(false);
                }
              }}
            >
              {t(lang, "transferMove")}
            </button>
            <button
              type="button"
              className="btn-secondary text-xs"
              disabled={!target || transferring}
              onClick={async () => {
                setTransferring(true);
                try {
                  await transferDocument(document.id, target, "copy");
                  setTransferOpen(false);
                } finally {
                  setTransferring(false);
                }
              }}
            >
              {t(lang, "transferCopy")}
            </button>
          </div>
        )}
      </div>
      {preview && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={() => setPreview(false)}>
          <div className="modal-card" style={{ width: "min(880px, 100%)" }} onClick={(event) => event.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="truncate font-semibold">{document.filename}</h3>
              <button type="button" className="btn-secondary" onClick={() => setPreview(false)}>{t(lang, "close")}</button>
            </div>
            {document.modality === "image" && <img src={fileUrl} alt={document.filename} className="max-h-[70vh] w-full object-contain" />}
            {document.modality === "pdf" && <iframe title={document.filename} src={fileUrl} className="h-[70vh] w-full rounded-xl border border-black/10" />}
            {document.modality === "audio" && <audio controls src={fileUrl} className="w-full" />}
            {document.modality !== "image" && document.modality !== "pdf" && document.modality !== "audio" && (
              <a className="btn-primary inline-flex" href={fileUrl} target="_blank" rel="noreferrer">{t(lang, "openFile")}</a>
            )}
          </div>
        </div>
      )}
      {editing && (
        <div className="space-y-3 border-t border-black/5 px-5 py-4">
          <textarea className="field min-h-36" value={draft} onChange={(event) => setDraft(event.target.value)} />
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-primary"
              onClick={async () => {
                const updated = await api.saveText(document.id, draft);
                upsertDocument(updated);
                setEditing(false);
                pushToast(lang === "tr" ? "Dosya güncellendi" : "File updated", "success");
              }}
            >
              {t(lang, "save")}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setEditing(false)}>{t(lang, "close")}</button>
          </div>
        </div>
      )}
    </article>
  );
}

function MetaChip({ label }: { label: string }) {
  return <span className={clsx("rounded-full bg-white/5 px-2.5 py-1")}>{label}</span>;
}
