import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { exportQueries } from "../lib/exportTable";
import { useOmni } from "../lib/store";
import { roomLabel, t } from "../lib/i18n";
import { RichText } from "./RichText";

const PAGE_SIZE = 10;

function pageWindow(current: number, total: number): Array<number | "..."> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index);
  const keep = [0, total - 1, current - 1, current, current + 1].filter((page, index, all) => page >= 0 && page < total && all.indexOf(page) === index);
  keep.sort((a, b) => a - b);
  const windowed: Array<number | "..."> = [];
  keep.forEach((page, index) => {
    if (index > 0 && page - keep[index - 1] > 1) windowed.push("...");
    windowed.push(page);
  });
  return windowed;
}

function stamp(value: number, lang: string) {
  return new Date(value * 1000).toLocaleString(lang === "tr" ? "tr-TR" : "en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function HistoryPanel() {
  const lang = useOmni((s) => s.lang);
  const role = useOmni((s) => s.role);
  const history = useOmni((s) => s.history);
  const rooms = useOmni((s) => s.rooms);
  const clearHistory = useOmni((s) => s.clearHistory);
  const askConfirm = useOmni((s) => s.askConfirm);
  const pushToast = useOmni((s) => s.pushToast);
  const workspace = useOmni((s) => s.department);
  const [query, setQuery] = useState("");
  const [department, setDepartment] = useState(workspace && workspace !== "all" ? workspace : "all");
  const [username, setUsername] = useState("all");
  const [people, setPeople] = useState<Array<{ username: string; full_name: string }>>([]);
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const resumeThread = useOmni((s) => s.resumeThread);

  useEffect(() => {
    if (role !== "admin") return;
    api.users().then((rows) => setPeople(rows.map((row) => ({ username: row.username, full_name: row.full_name })))).catch(() => setPeople([]));
  }, [role]);

  useEffect(() => {
    if (workspace && workspace !== "all") setDepartment(workspace);
  }, [workspace]);

  useEffect(() => {
    if (role !== "admin") return;
    api.history({ department, username }).then((result) => useOmni.setState({ history: result.items })).catch(() => undefined);
  }, [role, department, username]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return history.filter((item) => {
      if (department !== "all" && item.department !== department) return false;
      if (username !== "all" && (item.username || "") !== username) return false;
      if (!needle) return true;
      return `${item.question} ${item.answer.answer} ${item.user_full_name || ""} ${item.username || ""} ${item.thread_title || ""}`.toLowerCase().includes(needle);
    });
  }, [history, query, department, username]);

  async function saveTitle(conversationId: string, title: string) {
    const cleaned = title.trim();
    if (!cleaned || !conversationId) return;
    const saved = await api.renameThread(conversationId, cleaned);
    useOmni.setState({
      history: history.map((row) => (row.conversation_id === conversationId ? { ...row, thread_title: saved.title } : row)),
    });
    setEditing(null);
    pushToast(t(lang, "titleUpdated"), "success");
  }

  const threads = useMemo(() => {
    const groups = new Map<string, typeof filtered>();
    filtered.forEach((item) => {
      const key = item.conversation_id || `solo-${item.id}`;
      const rows = groups.get(key) || [];
      rows.push(item);
      groups.set(key, rows);
    });
    return [...groups.entries()]
      .map(([key, items]) => {
        const ordered = [...items].sort((a, b) => a.created_at - b.created_at);
        return {
          key,
          items: ordered,
          latest: ordered[ordered.length - 1],
          title: ordered.find((item) => item.thread_title)?.thread_title || ordered[0].question,
        };
      })
      .sort((a, b) => b.latest.created_at - a.latest.created_at);
  }, [filtered]);
  const pages = Math.max(1, Math.ceil(threads.length / PAGE_SIZE));
  const safePage = Math.min(page, pages - 1);
  const visible = threads.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-2xl font-semibold">{t(lang, "queryHistory")}</h2>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-secondary" onClick={() => void exportQueries("sorgu-gecmisi", "xlsx", { department, username, search: query })}>{t(lang, "exportExcel")}</button>
          <button type="button" className="btn-secondary" onClick={() => void exportQueries("sorgu-gecmisi", "csv", { department, username, search: query })}>{t(lang, "exportCsv")}</button>
          <button type="button" className="btn-secondary" onClick={async () => { if (await askConfirm()) { await clearHistory(); pushToast(lang === "tr" ? "Sorgu geçmişi temizlendi" : "Query history cleared", "success"); } }}>
            {t(lang, "clearAllHistory")}
          </button>
        </div>
      </div>
      <div className="history-toolbar">
        <input className="field" placeholder={t(lang, "historySearch")} value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} />
        {role === "admin" && (
          <>
            <select className="field" value={department} onChange={(event) => { setDepartment(event.target.value); setPage(0); }}>
              <option value="all">{t(lang, "allDepartments")}</option>
              {rooms.map((room) => (
                <option key={room.slug} value={room.slug}>{roomLabel(lang, room.slug, room.name)}</option>
              ))}
            </select>
            <select className="field" value={username} onChange={(event) => { setUsername(event.target.value); setPage(0); }}>
              <option value="all">{t(lang, "allUsers")}</option>
              {people.map((person) => (
                <option key={person.username} value={person.username}>
                  {person.full_name} ({person.username})
                </option>
              ))}
            </select>
          </>
        )}
      </div>
      <div className="space-y-2">
        {visible.map((thread) => {
          const open = openId === thread.key;
          const first = thread.items[0];
          const dept = first.department === "all" ? t(lang, "allDepartments") : roomLabel(lang, first.department, first.department);
          return (
            <article key={thread.key} className="card p-3">
              <div className="thread-layout flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1 text-left">
                  <button type="button" className="w-full text-left" onClick={() => setOpenId(open ? null : thread.key)}>
                    <div className="flex flex-wrap gap-2 text-[12px] text-[#6e6e73]">
                      <span className="chip">{thread.items.length} {t(lang, "messageCount")}</span>
                      <span>[{t(lang, "askedBy")}: {thread.latest.user_full_name || "—"} (@{thread.latest.username || "—"})]</span>
                      <span>[{t(lang, "deptBadge")}: {dept}]</span>
                      <span>{stamp(thread.latest.created_at, lang)}</span>
                    </div>
                  </button>
                  <div className="mt-1 flex items-center gap-2">
                    {editing === thread.key ? (
                      <div className="thread-rename">
                        <input
                          className="field thread-title-input"
                          value={draft}
                          autoFocus
                          onChange={(event) => setDraft(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Escape") setEditing(null);
                            if (event.key === "Enter") void saveTitle(first.conversation_id || "", draft);
                          }}
                        />
                        <button type="button" className="btn-primary" onClick={() => void saveTitle(first.conversation_id || "", draft)}>{t(lang, "save")}</button>
                        <button type="button" className="btn-secondary" onClick={() => setEditing(null)}>{t(lang, "cancel")}</button>
                      </div>
                    ) : (
                      <button type="button" className="text-left font-medium" onClick={() => setOpenId(open ? null : thread.key)}>
                        {thread.title}
                      </button>
                    )}
                    {first.conversation_id && editing !== thread.key && (
                      <button
                        type="button"
                        className="shrink-0 text-sm"
                        aria-label={t(lang, "renameThread")}
                        onClick={() => {
                          setEditing(thread.key);
                          setDraft(thread.title);
                        }}
                      >
                        ✏️
                      </button>
                    )}
                  </div>
                </div>
                <div className="thread-actions">
                <button
                  type="button"
                  className="btn-secondary shrink-0"
                  onClick={() => void exportQueries(thread.title || "sohbet", "xlsx", {
                    conversationId: first.conversation_id || "",
                    queryIds: first.conversation_id ? [] : thread.items.map((item) => item.id),
                  })}
                >
                  {t(lang, "exportThread")}
                </button>
                <button
                  type="button"
                  className="shrink-0 rounded-full bg-[#ff3b30] px-3 py-1.5 text-xs font-semibold text-white"
                  onClick={async () => {
                    if (!(await askConfirm())) return;
                    await Promise.all(thread.items.map((item) => api.deleteHistory(item.id)));
                    const ids = new Set(thread.items.map((item) => item.id));
                    useOmni.setState({ history: history.filter((row) => !ids.has(row.id)) });
                    pushToast(lang === "tr" ? "Konuşma silindi" : "Conversation deleted", "success");
                  }}
                >
                  {t(lang, "delete")}
                </button>
                </div>
              </div>
              {open && (
                <div className="mt-3 space-y-3">
                  {thread.items.map((item) => (
                    <div key={item.id} className="rounded-2xl bg-[#f5f5f7] p-3">
                      <div className="text-[12px] text-[#6e6e73]">{stamp(item.created_at, lang)}</div>
                      <div className="mt-1 font-medium">{item.question}</div>
                      <div className="mt-2 text-[15px] leading-relaxed">
                        <RichText text={item.answer.answer} />
                      </div>
                    </div>
                  ))}
                  <button type="button" className="btn-primary" onClick={() => resumeThread(thread.items)}>
                    {t(lang, "continueThread")}
                  </button>
                </div>
              )}
            </article>
          );
        })}
      </div>
      {threads.length > 0 && (
        <div className="pager">
          <button type="button" disabled={safePage === 0} onClick={() => setPage(0)}>« {t(lang, "firstPage")}</button>
          <button type="button" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>‹ {t(lang, "prevPage")}</button>
          {pageWindow(safePage, pages).map((item, index) => (
            item === "..." ? (
              <span key={`gap-${index}`} className="ellipsis">...</span>
            ) : (
              <button key={item} type="button" className={item === safePage ? "active" : ""} onClick={() => setPage(item)}>
                {item + 1}
              </button>
            )
          ))}
          <button type="button" disabled={safePage >= pages - 1} onClick={() => setPage(safePage + 1)}>{t(lang, "nextPage")} ›</button>
          <button type="button" disabled={safePage >= pages - 1} onClick={() => setPage(pages - 1)}>{t(lang, "lastPage")} »</button>
        </div>
      )}
    </section>
  );
}
