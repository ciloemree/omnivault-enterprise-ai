import { useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { useOmni } from "../lib/store";
import { StatusPill } from "./StatusPill";
import type { RuntimeSettings } from "../lib/types";
import { t } from "../lib/i18n";

export function SettingsPanel() {
  const settings = useOmni((s) => s.settings);
  const health = useOmni((s) => s.health);
  const saveSettings = useOmni((s) => s.saveSettings);
  const refreshSettings = useOmni((s) => s.refreshSettings);
  const lang = useOmni((s) => s.lang);
  const [draft, setDraft] = useState<Partial<RuntimeSettings> & { gemini_api_key?: string; groq_api_key?: string }>({});
  const [mcp, setMcp] = useState<{ tools: number; resources: number; names: string[]; uri: string; cursor: string } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    Promise.all([api.mcpTools(), api.mcpResources()])
      .then(([tools, resources]) =>
        setMcp({
          tools: tools.count,
          resources: resources.count,
          names: tools.tools.map((item) => item.name),
          uri: resources.resources[0]?.uri || "vault://{department}/documents",
          cursor: JSON.stringify(tools.cursor || {}, null, 2),
        }),
      )
      .catch(() => setMcp(null));
  }, []);

  useEffect(() => {
    if (settings) {
      setDraft({
        chat_model: settings.chat_model,
        vision_model: settings.vision_model,
        embed_model: settings.embed_model,
        privacy_shield: settings.privacy_shield,
        top_k: settings.top_k,
        strict_guard: settings.strict_guard,
      });
    }
  }, [settings]);

  if (!settings) {
    return <div className="card p-6 text-sm text-[#6e6e73]">{t(lang, "boot")}</div>;
  }

  return (
    <div className="space-y-6">
      <section className="card p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold">{t(lang, "settingsTitle")}</h2>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#6e6e73]">{t(lang, "settingsSubtitle")}</p>
          </div>
          <button type="button" onClick={() => refreshSettings()} className="btn-secondary">
            {t(lang, "refresh")}
          </button>
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-3">
          <Metric
            label="Ollama"
            value={health?.ollama.online ? "Online" : "Offline"}
            tone={health?.ollama.online ? "good" : "bad"}
          />
          <Metric label="Vectors" value={String(health?.vector_count ?? 0)} tone="accent" />
          <Metric label="Unified RAM" value={`${health?.ram_gb ?? "—"} GB`} tone="neutral" />
        </div>
      </section>

      <section className="card p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold">{t(lang, "mcpTitle")}</h2>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#6e6e73]">
              {lang === "tr"
                ? "Cursor ve Claude Desktop bu yerel stdio sunucusuyla kasayı doğrudan okur. HTTP uçları aynı araçları oturum anahtarıyla açar."
                : "Cursor and Claude Desktop open the vault through this local stdio server. The HTTP endpoints expose the same tools with a session token."}
            </p>
          </div>
          <span className="chip">
            {mcp ? `${mcp.tools} Tool • ${mcp.resources} Resource ${t(lang, "mcpActive")}` : "—"}
          </span>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {["GET /api/mcp/tools", "GET /api/mcp/resources", "POST /api/mcp/call"].map((endpoint) => (
            <div key={endpoint} className="rounded-2xl bg-[#f5f5f7] px-4 py-3 text-sm font-medium">
              {endpoint}
            </div>
          ))}
        </div>
        {mcp && (
          <div className="mt-4 space-y-3">
            <p className="text-xs leading-relaxed text-[#6e6e73]">
              {mcp.names.join(" · ")}
              <br />
              {mcp.uri}
            </p>
            <div className="rounded-2xl bg-[#f5f5f7] p-4">
              <div className="mb-2 flex items-center justify-between gap-3">
                <span className="text-xs font-semibold uppercase tracking-wide text-[#6e6e73]">mcp.json</span>
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  onClick={async () => {
                    const text = mcp.cursor;
                    let copiedText = false;
                    try {
                      await navigator.clipboard.writeText(text);
                      copiedText = true;
                    } catch {
                      const area = document.createElement("textarea");
                      area.value = text;
                      area.setAttribute("readonly", "");
                      area.style.position = "fixed";
                      area.style.left = "-9999px";
                      document.body.appendChild(area);
                      area.select();
                      copiedText = document.execCommand("copy");
                      area.remove();
                    }
                    if (!copiedText) return;
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1600);
                  }}
                >
                  {copied ? t(lang, "mcpCopied") : t(lang, "mcpCopy")}
                </button>
              </div>
              <pre className="overflow-x-auto whitespace-pre text-xs leading-relaxed text-[#1d1d1f]">{mcp.cursor}</pre>
            </div>
          </div>
        )}
      </section>

      <section className="card p-6">
        <div className="settings-grid">
          <Field label={t(lang, "chatModel")}>
            <select
              className="field settings-control"
              value={draft.chat_model || ""}
              onChange={(event) => setDraft((prev) => ({ ...prev, chat_model: event.target.value }))}
            >
              <option value="">{t(lang, "auto")}</option>
              {settings.available.chat.map((model) => (
                <option key={model.name} value={model.name}>
                  {model.name} {model.parameter_size ? `(${model.parameter_size})` : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t(lang, "visionModel")}>
            <select
              className="field settings-control"
              value={draft.vision_model || ""}
              onChange={(event) => setDraft((prev) => ({ ...prev, vision_model: event.target.value }))}
            >
              <option value="">{t(lang, "auto")}</option>
              {settings.available.vision.map((model) => (
                <option key={model.name} value={model.name}>
                  {model.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t(lang, "embedModel")}>
            <select
              className="field settings-control"
              value={draft.embed_model || ""}
              onChange={(event) => setDraft((prev) => ({ ...prev, embed_model: event.target.value }))}
            >
              <option value="">{t(lang, "auto")}</option>
              {settings.available.embed.map((model) => (
                <option key={model.name} value={model.name}>
                  {model.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t(lang, "topk")}>
            <input
              type="number"
              min={1}
              max={20}
              className="field settings-control"
              value={draft.top_k ?? 6}
              onChange={(event) => setDraft((prev) => ({ ...prev, top_k: Number(event.target.value) }))}
            />
          </Field>
          <Field label={t(lang, "geminiKey")}>
            <input
              type="password"
              autoComplete="off"
              className="field settings-control"
              placeholder={settings.gemini_key_set ? t(lang, "keySaved") : "GEMINI_API_KEY"}
              value={draft.gemini_api_key || ""}
              onChange={(event) => setDraft((prev) => ({ ...prev, gemini_api_key: event.target.value }))}
            />
          </Field>
          <Field label={t(lang, "groqKey")}>
            <input
              type="password"
              autoComplete="off"
              className="field settings-control"
              placeholder={settings.groq_key_set ? t(lang, "keySaved") : "GROQ_API_KEY"}
              value={draft.groq_api_key || ""}
              onChange={(event) => setDraft((prev) => ({ ...prev, groq_api_key: event.target.value }))}
            />
          </Field>
        </div>
        <p className="mt-3 text-xs leading-relaxed text-[#6e6e73]">{t(lang, "keyHint")}</p>

        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <Toggle
            title={t(lang, "guard")}
            description={t(lang, "guardHint")}
            checked={Boolean(draft.strict_guard)}
            onChange={(strict_guard) => setDraft((prev) => ({ ...prev, strict_guard }))}
          />
          <Toggle
            title={t(lang, "shield")}
            description={t(lang, "shieldHint")}
            checked={Boolean(draft.privacy_shield)}
            onChange={(privacy_shield) => setDraft((prev) => ({ ...prev, privacy_shield }))}
          />
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-[#6e6e73]">
            Endpoint {settings.ollama_url} · ctx {settings.chat_num_ctx} · keep_alive chat {settings.chat_keep_alive}
          </div>
          <button
            type="button"
            onClick={() => saveSettings(draft)}
            className="btn-primary"
          >
            {t(lang, "save")}
          </button>
        </div>
      </section>

      <section className="card p-6">
        <h3 className="text-lg font-semibold">{t(lang, "loaded")}</h3>
        <div className="mt-4 space-y-2">
          {(health?.ollama.loaded || []).length === 0 && (
            <div className="text-sm text-[#6e6e73]">{t(lang, "noneLoaded")}</div>
          )}
          {(health?.ollama.loaded || []).map((model: any) => (
            <div
              key={model.name || model.model}
              className="flex items-center justify-between rounded-2xl bg-[#f5f5f7] px-4 py-3 text-sm"
            >
              <span>{model.name || model.model}</span>
              <StatusPill label={t(lang, "resident")} tone="accent" />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="settings-field text-sm">
      <span className="text-xs font-semibold uppercase tracking-wide text-[#6e6e73]">{label}</span>
      {children}
    </label>
  );
}

function Toggle({
  title,
  description,
  checked,
  onChange,
}: {
  title: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex items-start justify-between gap-4 rounded-2xl bg-[#f5f5f7] p-4 text-left"
    >
      <div>
        <div className="font-medium">{title}</div>
        <div className="mt-1 text-sm text-[#6e6e73]">{description}</div>
      </div>
      <span
        className={
          checked
            ? "mt-1 inline-flex h-6 w-11 items-center rounded-full bg-[#34c759] p-1"
            : "mt-1 inline-flex h-6 w-11 items-center rounded-full bg-white/10 p-1"
        }
      >
        <span
          className={
            checked
              ? "ml-auto h-4 w-4 rounded-full bg-slate-950"
              : "h-4 w-4 rounded-full bg-slate-300"
          }
        />
      </span>
    </button>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "good" | "bad" | "accent" | "neutral";
}) {
  return (
    <div className="rounded-2xl bg-[#f5f5f7] p-4">
      <div className="text-xs uppercase tracking-wide text-[#6e6e73]">{label}</div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="text-xl font-semibold">{value}</div>
        <StatusPill label={tone === "good" ? "ok" : tone === "bad" ? "check" : "live"} tone={tone} />
      </div>
    </div>
  );
}
