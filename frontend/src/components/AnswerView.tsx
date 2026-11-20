import { useState } from "react";
import type { Citation, QueryResponse } from "../lib/types";
import { RichText } from "./RichText";
import { useOmni } from "../lib/store";
import { confidenceLabel, t } from "../lib/i18n";

function shortName(filename: string) {
  const stem = filename.replace(/\.[^.]+$/, "");
  return stem.length > 18 ? `${stem.slice(0, 16)}…` : stem;
}

function toneFor(score: number) {
  if (score >= 78) return "good";
  if (score >= 58) return "accent";
  if (score >= 38) return "warn";
  return "bad";
}

export function AnswerView({ answer, turnIndex }: { answer: QueryResponse; turnIndex?: number }) {
  const lang = useOmni((s) => s.lang);
  const pushToast = useOmni((s) => s.pushToast);
  const setCitationFocus = useOmni((s) => s.setCitationFocus);
  const confirmPendingUpdate = useOmni((s) => s.confirmPendingUpdate);
  const cancelPendingUpdate = useOmni((s) => s.cancelPendingUpdate);
  const [busy, setBusy] = useState(false);
  const score = Math.max(0, Math.min(100, answer.grounding_score));
  const tone = toneFor(score);
  const mark = tone === "good" ? "🟢" : tone === "accent" ? "🔵" : tone === "warn" ? "🟠" : "🔴";
  const trust = t(lang, "confidence");
  const trustWord = trust.slice(0, 1).toUpperCase() + trust.slice(1);
  const seconds = (answer.latency_ms / 1000).toFixed(1);
  const pending = answer.pending_update;

  function openSource(citation: Citation) {
    if (citation.modality === "audio") setCitationFocus({ kind: "audio", citation });
    else if (citation.modality === "image") setCitationFocus({ kind: "image", citation });
    else setCitationFocus({ kind: "pdf", citation });
  }

  return (
    <article className="assistant-bubble">
      <div className="bubble-head">
        <span className="chip">{answer.engine_label || answer.model}</span>
        <span className="chip">
          {seconds} {t(lang, "secondsShort")}
        </span>
        {score > 0 && (
        <span className={`confidence-badge ${tone}`}>
          {mark} %{score.toFixed(1)} {confidenceLabel(lang, answer.grounding_label)} {trustWord}
          <span className="confidence-track" aria-hidden="true">
            <span style={{ width: `${score}%` }} />
          </span>
        </span>
        )}
      </div>
      <div className="prose-answer text-[15px] leading-relaxed">
        <RichText text={answer.answer} />
      </div>
      {pending && typeof turnIndex === "number" && (
        <div className="update-card">
          <div className="update-row">
            <span>{t(lang, "updateTarget")}</span>
            <strong>{pending.filename}</strong>
          </div>
          <div className="update-row">
            <span>{t(lang, "updateCurrent")}</span>
            <strong>{pending.old_snippet}</strong>
          </div>
          <div className="update-row">
            <span>{t(lang, "updateNext")}</span>
            <strong>{pending.new_snippet}</strong>
          </div>
          <div className="update-actions">
            <button
              type="button"
              className="btn-primary text-xs"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await confirmPendingUpdate(turnIndex);
                } catch (error) {
                  pushToast(error instanceof Error ? error.message : String(error), "error");
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t(lang, "updateConfirm")}
            </button>
            <button
              type="button"
              className="btn-secondary text-xs"
              disabled={busy}
              onClick={() => cancelPendingUpdate(turnIndex)}
            >
              {t(lang, "updateCancel")}
            </button>
          </div>
        </div>
      )}
      <div className="bubble-actions">
        {score > 0 && answer.citations.map((citation) => (
          <button key={citation.id} type="button" className="source-pill" onClick={() => openSource(citation)}>
            📄 {t(lang, "sourceN")} {citation.index}: {shortName(citation.filename)} (%{Math.round(citation.relevance * 100)}) • {t(lang, "inspectSource")}
          </button>
        ))}
        <button
          type="button"
          className="copy-pill"
          onClick={() => {
            void navigator.clipboard.writeText(answer.answer).then(() => {
              pushToast(t(lang, "copiedAnswer"), "success");
            });
          }}
        >
          📋 {t(lang, "copyAnswer")}
        </button>
      </div>
    </article>
  );
}
