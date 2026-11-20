import { create } from "zustand";
import { api, type HistoryItem, type SessionUser } from "./api";
import type { Lang, Role } from "./i18n";
import type {
  CitationFocus,
  DocumentItem,
  HealthResponse,
  QueryResponse,
  RuntimeSettings,
} from "./types";

function versionMessage(documents: DocumentItem[], lang: Lang): string {
  const replaced = documents.find((item) => item.meta?.replaced && Number(item.meta.version) > 1);
  if (!replaced) return "";
  const version = `v${String(replaced.meta.version)}`;
  return lang === "tr"
    ? `✓ Mevcut kayıt en güncel tarihli veriyle güncellendi (${version})`
    : `✓ Existing record updated with the newest data (${version})`;
}

interface OmniState {
  health: HealthResponse | null;
  settings: RuntimeSettings | null;
  documents: DocumentItem[];
  loading: boolean;
  querying: boolean;
  role: Role;
  department: string;
  user: SessionUser | null;
  history: HistoryItem[];
  turns: Array<{ question: string; answer: QueryResponse }>;
  conversationId: string | null;
  threadsByDept: Record<string, { conversationId: string | null; turns: Array<{ question: string; answer: QueryResponse }>; lastQuestion: string; lastAnswer: QueryResponse | null }>;
  error: string | null;
  toasts: Array<{ id: number; message: string; tone: "success" | "warning" | "error" }>;
  confirm: null | { resolve: (ok: boolean) => void };
  lastAnswer: QueryResponse | null;
  lastQuestion: string;
  citationFocus: CitationFocus;
  lang: Lang;
  activeTab: "ask" | "vault" | "history" | "team" | "settings";
  modalityFilter: string[];
  engine: string;
  setEngine: (engine: string) => void;
  bootstrap: () => Promise<void>;
  refreshDocuments: () => Promise<void>;
  refreshSettings: () => Promise<void>;
  setLang: (lang: Lang) => void;
  setTab: (tab: "ask" | "vault" | "history" | "team" | "settings") => void;
  setModalityFilter: (values: string[]) => void;
  setCitationFocus: (focus: CitationFocus) => void;
  setToast: (message: string | null) => void;
  pushToast: (message: string, tone?: "success" | "warning" | "error") => void;
  askConfirm: () => Promise<boolean>;
  setRole: (role: Role) => void;
  setDepartment: (department: string) => void;
  login: (username: string, password: string) => Promise<void>;
  loginDemo: (persona: "admin" | "ik" | "finans" | "teknik") => Promise<void>;
  openSession: (user: SessionUser) => Promise<void>;
  register: (body: { username: string; password: string; full_name: string; department: string }) => Promise<void>;
  logout: () => Promise<void>;
  clearHistory: () => Promise<void>;
  upload: (files: FileList | File[], department?: string) => Promise<void>;
  saveNote: (note: { title: string; body: string; department: string }) => Promise<void>;
  transferDocument: (id: string, target: string, mode: "move" | "copy") => Promise<void>;
  confirmPendingUpdate: (index: number) => Promise<void>;
  cancelPendingUpdate: (index: number) => void;
  remove: (id: string) => Promise<void>;
  ask: (question: string) => Promise<boolean>;
  startTopic: () => void;
  resumeThread: (items: HistoryItem[]) => void;
  saveSettings: (patch: Partial<RuntimeSettings> & { gemini_api_key?: string; groq_api_key?: string }) => Promise<void>;
  rooms: Array<{ slug: string; name: string; users: number; files: number }>;
  refreshRooms: () => Promise<void>;
  upsertDocument: (document: DocumentItem) => void;
  removeDocumentLocal: (id: string) => void;
}

export const useOmni = create<OmniState>((set, get) => ({
  health: null,
  settings: null,
  documents: [],
  rooms: [],
  loading: true,
  querying: false,
  role: "member",
  department: "yonetim",
  user: null,
  history: [],
  turns: [],
  conversationId: null,
  threadsByDept: {},
  error: null,
  toasts: [],
  confirm: null,
  lastAnswer: null,
  lastQuestion: "",
  citationFocus: null,
  lang: (typeof localStorage !== "undefined" && localStorage.getItem("omnivault-lang") === "tr" ? "tr" : "en") as Lang,
  activeTab: "ask",
  modalityFilter: [],
  engine: localStorage.getItem("omnivault-engine") || "gemma-4-26b",
  bootstrap: async () => {
    set({ loading: true, error: null });
    const token = localStorage.getItem("omnivault-token");
    if (!token) {
      set({ loading: false, user: null });
      return;
    }
    try {
      const user = await api.me();
      const saved = localStorage.getItem("omnivault-department");
      const workspace = user.role === "admin" ? (saved && saved !== "all" ? saved : "yonetim") : user.department;
      localStorage.setItem("omnivault-role", user.role);
      localStorage.setItem("omnivault-department", workspace);
      const [health, settings, documents, history] = await Promise.all([
        api.health(),
        api.settings(),
        api.documents(),
        api.history(),
      ]);
      set({
        user,
        role: user.role,
        department: workspace,
        health,
        settings,
        documents,
        history: history.items,
        loading: false,
      });
      if (user.role === "admin") await get().refreshRooms();
    } catch (error) {
      localStorage.removeItem("omnivault-token");
      set({
        loading: false,
        user: null,
        error: error instanceof Error ? error.message : "Failed to reach OmniVault API",
      });
    }
  },
  register: async (body) => {
    const user = await api.register(body);
    if (user.token) localStorage.setItem("omnivault-token", user.token);
    localStorage.setItem("omnivault-role", user.role);
    localStorage.setItem("omnivault-department", user.department);
    set({ user, role: user.role, department: user.department });
    await get().bootstrap();
  },
  login: async (username, password) => {
    await get().openSession(await api.login(username, password));
  },
  loginDemo: async (persona) => {
    await get().openSession(await api.demoLogin(persona));
  },
  openSession: async (user) => {
    if (user.token) localStorage.setItem("omnivault-token", user.token);
    localStorage.setItem("omnivault-role", user.role);
    const workspace = user.role === "admin" ? "yonetim" : user.department;
    localStorage.setItem("omnivault-department", workspace);
    set({ user, role: user.role, department: workspace });
    await get().bootstrap();
  },
  logout: async () => {
    try {
      await api.logout();
    } catch {
      /* session may already be gone */
    }
    localStorage.removeItem("omnivault-token");
    set({ user: null, documents: [], history: [], turns: [], conversationId: null, threadsByDept: {}, lastAnswer: null, role: "member", department: "yonetim" });
  },
  clearHistory: async () => {
    await api.clearHistory();
    set({ history: [], turns: [], conversationId: null, threadsByDept: {}, lastAnswer: null });
  },
  refreshRooms: async () => {
    if (get().role !== "admin") return;
    const rooms = await api.departments();
    set({ rooms });
  },
  refreshDocuments: async () => {
    const documents = await api.documents();
    set({ documents });
  },
  refreshSettings: async () => {
    const [health, settings] = await Promise.all([api.health(), api.settings()]);
    set({ health, settings });
  },
  setRole: (role) => {
    localStorage.setItem("omnivault-role", role);
    set({ role, activeTab: role === "reader" ? "ask" : get().activeTab });
    void get().refreshDocuments();
  },
  setDepartment: (department) => {
    if (department === get().department) return;
    const saved = { ...get().threadsByDept };
    saved[get().department] = {
      conversationId: get().conversationId,
      turns: get().turns,
      lastQuestion: get().lastQuestion,
      lastAnswer: get().lastAnswer,
    };
    const next = saved[department] || { conversationId: null, turns: [], lastQuestion: "", lastAnswer: null };
    localStorage.setItem("omnivault-department", department);
    set({
      department,
      threadsByDept: saved,
      conversationId: next.conversationId,
      turns: next.turns,
      lastQuestion: next.lastQuestion,
      lastAnswer: next.lastAnswer,
    });
    void get().refreshDocuments();
    void api.health().then((health) => set({ health })).catch(() => undefined);
    if (get().role !== "admin") {
      void api.history().then((result) => set({ history: result.items })).catch(() => set({ history: [] }));
    }
  },
  setLang: (lang) => {
    localStorage.setItem("omnivault-lang", lang);
    set({ lang });
  },
  setTab: (activeTab) => set({ activeTab }),
  setModalityFilter: (modalityFilter) => set({ modalityFilter }),
  setCitationFocus: (citationFocus) => set({ citationFocus }),
  pushToast: (message, tone = "success") => {
    const id = Date.now() + Math.random();
    set((state) => ({ toasts: [...state.toasts, { id, message, tone }] }));
    window.setTimeout(() => set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) })), 4200);
  },
  setToast: (message) => {
    if (message) get().pushToast(message, "success");
  },
  askConfirm: () =>
    new Promise((resolve) => {
      set({ confirm: { resolve } });
    }),
  upload: async (files, department) => {
    const active = get().department;
    const stamp = department && department !== "all" ? department : active !== "all" ? active : "public";
    const saved = await api.upload(files, get().lang, stamp);
    await get().refreshDocuments();
    set({ activeTab: "vault" });
    get().pushToast(versionMessage(saved, get().lang) || (get().lang === "tr" ? "Dosya yüklendi" : "File uploaded"), "success");
  },
  saveNote: async (note) => {
    const saved = await api.note({ ...note, lang: get().lang });
    await get().refreshDocuments();
    get().pushToast(versionMessage([saved], get().lang) || (get().lang === "tr" ? "Dosya eklendi" : "File added"), "success");
  },
  transferDocument: async (id, target, mode) => {
    try {
      await api.transfer(id, target, mode);
    } catch (error) {
      const message = error instanceof Error ? error.message : get().lang === "tr" ? "Belge aktarılamadı" : "Transfer failed";
      get().pushToast(message, "error");
      throw error;
    }
    await get().refreshDocuments();
    if (get().role === "admin") await get().refreshRooms();
    await get().refreshSettings();
    get().pushToast(get().lang === "tr" ? "✓ Belge aktarıldı" : "✓ Document transferred", "success");
  },
  confirmPendingUpdate: async (index) => {
    const turn = get().turns[index];
    const pending = turn?.answer.pending_update;
    if (!pending) return;
    await api.updateText(pending.doc_id, pending.new_text);
    const nextAnswer = {
      ...turn.answer,
      answer: get().lang === "tr" ? "✓ Kayıt başarıyla güncellendi." : "✓ The record was updated.",
      pending_update: null,
      engine_label: get().lang === "tr" ? "Güncellendi" : "Updated",
      grounding_score: 0,
      citations: [],
    };
    const turns = get().turns.map((item, itemIndex) => (itemIndex === index ? { ...item, answer: nextAnswer } : item));
    const department = get().department;
    set({
      turns,
      lastAnswer: index === turns.length - 1 ? nextAnswer : get().lastAnswer,
      threadsByDept: {
        ...get().threadsByDept,
        [department]: {
          conversationId: get().conversationId,
          turns,
          lastQuestion: get().lastQuestion,
          lastAnswer: index === turns.length - 1 ? nextAnswer : get().lastAnswer,
        },
      },
    });
    await get().refreshDocuments();
    get().pushToast(get().lang === "tr" ? "✓ Kayıt başarıyla güncellendi" : "✓ Record updated", "success");
  },
  cancelPendingUpdate: (index) => {
    const turn = get().turns[index];
    if (!turn?.answer.pending_update) return;
    const nextAnswer = {
      ...turn.answer,
      answer: get().lang === "tr" ? "Güncelleme iptal edildi. Kayıt değişmedi." : "Update cancelled. The record was not changed.",
      pending_update: null,
      engine_label: get().lang === "tr" ? "İptal" : "Cancelled",
    };
    const turns = get().turns.map((item, itemIndex) => (itemIndex === index ? { ...item, answer: nextAnswer } : item));
    const department = get().department;
    set({
      turns,
      lastAnswer: index === turns.length - 1 ? nextAnswer : get().lastAnswer,
      threadsByDept: {
        ...get().threadsByDept,
        [department]: {
          conversationId: get().conversationId,
          turns,
          lastQuestion: get().lastQuestion,
          lastAnswer: index === turns.length - 1 ? nextAnswer : get().lastAnswer,
        },
      },
    });
  },
  remove: async (id) => {
    await api.remove(id);
    set((state) => ({ documents: state.documents.filter((doc) => doc.id !== id) }));
    get().pushToast(get().lang === "tr" ? "Dosya silindi" : "File deleted", "success");
  },
  startTopic: () => {
    const department = get().department;
    const saved = {
      ...get().threadsByDept,
      [department]: { conversationId: null, turns: [], lastQuestion: "", lastAnswer: null },
    };
    set({ conversationId: null, turns: [], lastAnswer: null, lastQuestion: "", threadsByDept: saved });
  },
  resumeThread: (items) => {
    const department = items[0]?.department || get().department;
    const previous = get().department;
    const ordered = [...items].sort((a, b) => a.created_at - b.created_at);
    const conversationId = ordered.find((item) => item.conversation_id)?.conversation_id || crypto.randomUUID();
    const turns = ordered.map((item) => ({ question: item.question, answer: item.answer }));
    const lastAnswer = ordered.at(-1)?.answer || null;
    const lastQuestion = ordered.at(-1)?.question || "";
    const saved = { ...get().threadsByDept };
    saved[previous] = {
      conversationId: get().conversationId,
      turns: get().turns,
      lastQuestion: get().lastQuestion,
      lastAnswer: get().lastAnswer,
    };
    saved[department] = { conversationId, turns, lastQuestion, lastAnswer };
    if (department !== previous) localStorage.setItem("omnivault-department", department);
    set({
      department,
      threadsByDept: saved,
      conversationId,
      turns,
      lastAnswer,
      lastQuestion,
      activeTab: "ask",
    });
    if (department !== previous) void get().refreshDocuments();
  },
  ask: async (question) => {
    const conversationId = get().conversationId || crypto.randomUUID();
    const prior = get().turns.slice(-4).map((turn) => ({ question: turn.question, answer: turn.answer.answer }));
    set({ querying: true, error: null, lastQuestion: question, conversationId });
    try {
      const modalities = get().modalityFilter;
      const answer = await api.query({
        question,
        modalities: modalities.length ? modalities : undefined,
        top_k: get().settings?.top_k,
        chat_model: get().engine === "local" ? get().settings?.chat_model || undefined : undefined,
        lang: get().lang,
        language: get().lang,
        engine: get().engine,
        conversation_id: conversationId,
        history: prior,
      });
      const turn = { question, answer };
      const turns = [...get().turns, turn];
      const department = get().department;
      const listed = await api.history().catch(() => null);
      set({
        lastAnswer: answer,
        querying: false,
        activeTab: "ask",
        conversationId,
        turns,
        threadsByDept: {
          ...get().threadsByDept,
          [department]: { conversationId, turns, lastQuestion: question, lastAnswer: answer },
        },
        history: listed?.items || [
          {
            id: Date.now(),
            question,
            department,
            username: get().user?.username || "",
            user_full_name: get().user?.full_name || "",
            user_title: get().user?.title || "",
            conversation_id: conversationId,
            created_at: Date.now() / 1000,
            answer,
          },
          ...get().history,
        ],
      });
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Query failed";
      set({ querying: false, error: message });
      get().pushToast(message, "error");
      return false;
    }
  },
  setEngine: (engine) => {
    localStorage.setItem("omnivault-engine", engine);
    set({ engine });
  },
  saveSettings: async (patch) => {
    const settings = await api.updateSettings(patch);
    set({ settings });
    get().pushToast(get().lang === "tr" ? "Ayarlar kaydedildi" : "Settings saved", "success");
  },
  upsertDocument: (document) => {
    set((state) => {
      const index = state.documents.findIndex((item) => item.id === document.id);
      if (index === -1) {
        return { documents: [document, ...state.documents] };
      }
      const documents = state.documents.slice();
      documents[index] = document;
      return { documents };
    });
  },
  removeDocumentLocal: (id) => {
    set((state) => ({ documents: state.documents.filter((doc) => doc.id !== id) }));
  },
}));
