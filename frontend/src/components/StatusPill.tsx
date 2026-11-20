import clsx from "clsx";

export function StatusPill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "neutral" | "good" | "warn" | "bad" | "accent";
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold tracking-wide uppercase",
        tone === "good" && "border-emerald-200 bg-emerald-50 text-emerald-800",
        tone === "warn" && "border-amber-200 bg-amber-50 text-amber-800",
        tone === "bad" && "border-rose-200 bg-rose-50 text-rose-700",
        tone === "accent" && "border-blue-200 bg-blue-50 text-blue-800",
        tone === "neutral" && "border-black/10 bg-[#f5f5f7] text-[#424245]",
      )}
    >
      <span
        className={clsx(
          "h-1.5 w-1.5 rounded-full",
          tone === "good" && "bg-emerald-500",
          tone === "warn" && "bg-amber-500",
          tone === "bad" && "bg-rose-500",
          tone === "accent" && "bg-[#0071e3]",
          tone === "neutral" && "bg-[#86868b]",
        )}
      />
      {label}
    </span>
  );
}
