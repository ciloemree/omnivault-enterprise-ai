import { useEffect } from "react";
import clsx from "clsx";
import { AskPanel } from "./components/AskPanel";
import { HistoryPanel } from "./components/HistoryPanel";
import { LoginScreen } from "./components/LoginScreen";
import { TeamPanel } from "./components/TeamPanel";
import { VaultPanel } from "./components/VaultPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { CitationViewer } from "./components/CitationViewer";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { useOmni } from "./lib/store";
import { navName, roomLabel, t } from "./lib/i18n";

export default function App() {
  const bootstrap = useOmni((s) => s.bootstrap);
  const loading = useOmni((s) => s.loading);
  const error = useOmni((s) => s.error);
  const toasts = useOmni((s) => s.toasts);
  const activeTab = useOmni((s) => s.activeTab);
  const setTab = useOmni((s) => s.setTab);
  const health = useOmni((s) => s.health);
  const documents = useOmni((s) => s.documents);
  const upsertDocument = useOmni((s) => s.upsertDocument);
  const removeDocumentLocal = useOmni((s) => s.removeDocumentLocal);
  const lang = useOmni((s) => s.lang);
  const setLang = useOmni((s) => s.setLang);
  const role = useOmni((s) => s.role);
  const department = useOmni((s) => s.department);
  const user = useOmni((s) => s.user);
  const logout = useOmni((s) => s.logout);
  const setDepartment = useOmni((s) => s.setDepartment);
  const rooms = useOmni((s) => s.rooms);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    const token = localStorage.getItem("omnivault-token");
    if (!token) return;
    const source = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
    source.addEventListener("document", (event) => {
      try {
        const document = JSON.parse((event as MessageEvent).data) as { meta?: { department?: string } };
        const owner = document.meta?.department || "";
        const current = useOmni.getState();
        const viewingAll = current.role === "admin" && current.department === "all";
        if (!viewingAll && owner && owner !== current.department) return;
        upsertDocument(document as never);
      } catch {
        /* ignore */
      }
    });
    source.addEventListener("document.deleted", (event) => {
      try {
        removeDocumentLocal(JSON.parse((event as MessageEvent).data).id);
      } catch {
        /* ignore */
      }
    });
    return () => source.close();
  }, [upsertDocument, removeDocumentLocal, user]);

  const ready = documents.filter((doc) => doc.status === "ready").length;
  const vectors = documents.reduce((sum, document) => sum + (document.chunk_count || 0), 0);
  const departmentName =
    department === "all"
      ? t(lang, "deptAll")
      : department === "hr"
        ? t(lang, "deptHr")
        : department === "finance"
          ? t(lang, "deptFinance")
          : department === "engineering"
            ? t(lang, "deptEngineering")
            : department === "legal"
              ? t(lang, "deptLegal")
              : department === "sales"
                ? t(lang, "deptSales")
                : department === "yonetim"
                  ? t(lang, "deptAdmin")
                  : department === "public"
                  ? t(lang, "deptPublic")
                  : department;

  if (!user) {
    if (loading) {
      return <div className="flex min-h-screen items-center justify-center text-sm text-[#6e6e73]">{t(lang, "boot")}</div>;
    }
    return <LoginScreen />;
  }

  return (
    <div className="min-h-screen text-[#1d1d1f]">
      <div className="app-shell mx-auto flex min-h-screen max-w-[1280px] flex-col px-5 py-6 md:px-8">
        <header className="app-header mb-6 space-y-2">
          <div className="topbar flex items-center justify-between gap-3 rounded-2xl bg-white px-3 py-2 shadow-sm">
          <nav className="topbar-nav flex min-w-0 flex-1 items-center justify-start gap-2 overflow-x-auto">
            {(
              [
                ["ask", "tabAsk"],
                ["vault", "tabVault"],
                ["history", "tabHistory"],
                ["team", "tabTeam"],
                ["settings", "tabSettings"],
              ] as const
            )
              .filter(([id]) => (id !== "team" || role === "admin") && (role !== "reader" || id === "ask" || id === "history"))
              .map(([id, key]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTab(id)}
                  className={clsx("shrink-0 rounded-full px-3 py-1.5 text-[13px] font-medium", activeTab === id ? "bg-[#1d1d1f] text-white" : "text-[#1d1d1f]")}
                >
                  {t(lang, key)}
                </button>
              ))}
          </nav>
          <div className="topbar-meta flex shrink-0 items-center gap-2">
          <span className="chip stat-chip whitespace-nowrap">{ready} {t(lang, "readyCount")} • {vectors} {t(lang, "vectors")}</span>
          <div className="lang-toggle" role="group" aria-label="Language">
            <button type="button" className={lang === "tr" ? "active" : ""} onClick={() => setLang("tr")}>TR</button>
            <button type="button" className={lang === "en" ? "active" : ""} onClick={() => setLang("en")}>EN</button>
          </div>
          <div className="profile-pill">
            <span>{navName(lang, user)}</span>
            <button type="button" onClick={() => void logout()}>{t(lang, "logout")}</button>
          </div>
          </div>
          </div>
          <div className="deptbar flex items-center gap-2 overflow-x-auto rounded-2xl bg-white px-3 py-2 shadow-sm">
            {role === "admin" ? (
              (rooms.length ? rooms : [
                { slug: "yonetim", name: t(lang, "roomAdminTab") },
                { slug: "hr", name: t(lang, "roomHrTab") },
                { slug: "finance", name: t(lang, "roomFinanceTab") },
                { slug: "engineering", name: t(lang, "roomEngineeringTab") },
              ]).map((room) => (
                <button
                  key={room.slug}
                  type="button"
                  onClick={() => setDepartment(room.slug)}
                  className={clsx("shrink-0 rounded-full px-3 py-1.5 text-[13px] font-medium", department === room.slug ? "bg-[#0071e3] text-white" : "bg-[#f5f5f7]")}
                >
                  {roomLabel(lang, room.slug, room.name)}
                </button>
              ))
            ) : (
              <span className="rounded-full bg-[#1d1d1f] px-3 py-1 text-[12px] font-semibold text-white">{departmentName}</span>
            )}
            <span className="deptbar-stats ml-auto shrink-0 text-[13px] text-[#6e6e73]">{departmentName} · {ready} {t(lang, "readyCount")} · {vectors} {t(lang, "vectors")}</span>
          </div>
        </header>

        <main className="app-main flex-1 pb-10">
          {loading ? (
            <div className="card p-8 text-sm text-[#6e6e73]">{t(lang, "boot")}</div>
          ) : error && !health ? (
            <div className="card p-8 text-sm text-[#1d1d1f]">{t(lang, "apiDown")}</div>
          ) : (
            <>
              {activeTab === "ask" && <AskPanel />}
              {role !== "reader" && activeTab === "vault" && <VaultPanel />}
              {activeTab === "history" && <HistoryPanel />}
              {role === "admin" && activeTab === "team" && <TeamPanel />}
              {role !== "reader" && activeTab === "settings" && <SettingsPanel />}
            </>
          )}
        </main>
      </div>
      <CitationViewer />
      <ConfirmDialog />
      <div className="toast-stack">
        {toasts.map((item) => (
          <div key={item.id} className={`toast ${item.tone}`} role="status">
            {item.message}
          </div>
        ))}
      </div>
    </div>
  );
}
