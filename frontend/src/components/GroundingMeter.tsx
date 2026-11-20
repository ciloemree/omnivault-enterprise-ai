import clsx from "clsx";
import { useOmni } from "../lib/store";
import { confidenceLabel, t } from "../lib/i18n";

export function GroundingMeter({ score, label }: { score: number; label: string }) {
  const lang = useOmni((s) => s.lang);
  const tone =
    score >= 78 ? "good" : score >= 58 ? "accent" : score >= 38 ? "warn" : "bad";
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-[#6e6e73]">{t(lang, "grounding")}</div>
          <div className="mt-1 text-sm text-[#6e6e73]">{t(lang, "groundingHint")}</div>
        </div>
        <div
          className={clsx(
            "text-2xl font-semibold tabular-nums",
            tone === "good" && "text-emerald-700",
            tone === "accent" && "text-[#0071e3]",
            tone === "warn" && "text-amber-700",
            tone === "bad" && "text-rose-700",
          )}
        >
          {score.toFixed(1)}
          <span className="ml-1 text-sm font-medium text-[#6e6e73]">/ 100</span>
        </div>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[#f5f5f7]">
        <div
          className={clsx(
            "h-full rounded-full transition-all duration-700",
            tone === "good" && "bg-emerald-500",
            tone === "accent" && "bg-[#0071e3]",
            tone === "warn" && "bg-amber-500",
            tone === "bad" && "bg-rose-500",
          )}
          style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
        />
      </div>
      <div className="mt-2 text-xs font-medium text-[#6e6e73]">
        {confidenceLabel(lang, label)} {t(lang, "confidence")}
      </div>
    </div>
  );
}
