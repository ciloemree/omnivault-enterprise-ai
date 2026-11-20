import type {
  DocumentItem,
  HealthResponse,
  PdfPageResponse,
  QueryResponse,
  RuntimeSettings,
} from "./types";

function roleHeader(): Record<string, string> {
  const headers: Record<string, string> = {
    "X-OmniVault-Role": localStorage.getItem("omnivault-role") || "member",
    "X-OmniVault-Department": localStorage.getItem("omnivault-department") || "public",
  };
  const token = localStorage.getItem("omnivault-token");
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const role = roleHeader();
  Object.entries(role).forEach(([key, value]) => headers.set(key, value));
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401 && !path.startsWith("/api/auth/login")) {
    localStorage.removeItem("omnivault-token");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export interface HistoryItem {
  id: number;
  question: string;
  department: string;
  username?: string;
  user_full_name?: string;
  user_title?: string;
  conversation_id?: string;
  thread_title?: string;
  created_at: number;
  answer: QueryResponse;
}

export interface SessionUser {
  id: number;
  username: string;
  full_name: string;
  role: "admin" | "member" | "reader";
  department: string;
  permission?: "read_only" | "editor" | "dept_admin";
  title?: string;
  token?: string;
}

export function downloadCsv(filename: string, columns: string[], rows: string[][]) {
  const escape = (value: string) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const lines = [columns, ...rows].map((row) => row.map((cell) => escape(cell)).join(","));
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${filename}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

export const api = {
  login: (username: string, password: string) =>
    request<SessionUser>("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  me: () => request<SessionUser>("/api/auth/me"),
  users: () => request<Array<{ id: number; username: string; full_name: string; role: string; department: string; permission?: string; title?: string }>>("/api/users"),
  createUser: (body: { username: string; password: string; full_name: string; role: string; department: string; permission?: string; title?: string }) =>
    request<SessionUser>("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  demoLogin: (persona: "admin" | "ik" | "finans" | "teknik") =>
    request<SessionUser>("/api/auth/demo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona }),
    }),
  updateUser: (id: number, body: { department?: string; password?: string; permission?: string; username?: string; full_name?: string; title?: string }) =>
    request<{ ok: boolean }>(`/api/users/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  exportHistory: async (body: {
    filename: string;
    kind: "csv" | "xlsx";
    department?: string;
    username?: string;
    search?: string;
    conversation_id?: string;
    query_ids?: number[];
  }) => {
    const headers = roleHeader();
    const response = await fetch("/api/history/export", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error("Export failed");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${body.filename}.${body.kind}`;
    link.click();
    URL.revokeObjectURL(url);
  },
  exportXlsx: async (filename: string, columns: string[], rows: string[][]) => {
    const headers = roleHeader();
    const response = await fetch("/api/export.xlsx", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ filename, columns, rows }),
    });
    if (!response.ok) throw new Error("Export failed");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${filename}.xlsx`;
    link.click();
    URL.revokeObjectURL(url);
  },
  updateProfile: (body: { username?: string; password?: string }) =>
    request<{ ok: boolean }>("/api/auth/profile", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  departments: () => request<Array<{ slug: string; name: string; users: number; files: number }>>("/api/departments"),
  addDepartment: (name: string) =>
    request<Array<{ slug: string; name: string; users: number; files: number }>>("/api/departments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  deleteDepartment: (slug: string) =>
    request<Array<{ slug: string; name: string; users: number; files: number }>>(`/api/departments/${slug}`, { method: "DELETE" }),
  deleteUser: (id: number) => request<{ ok: boolean }>(`/api/users/${id}`, { method: "DELETE" }),
  history: (filters?: { department?: string; username?: string }) => {
    const params = new URLSearchParams();
    if (filters?.department && filters.department !== "all") params.set("department", filters.department);
    if (filters?.username && filters.username !== "all") params.set("username", filters.username);
    const query = params.toString();
    return request<{ items: HistoryItem[] }>(`/api/history${query ? `?${query}` : ""}`);
  },
  clearHistory: () => request<{ ok: boolean }>("/api/history", { method: "DELETE" }),
  deleteHistory: (id: number) => request<{ ok: boolean }>(`/api/history/${id}`, { method: "DELETE" }),
  renameThread: (conversationId: string, title: string) =>
    request<{ title: string }>("/api/history/thread", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId, title }),
    }),
  health: () => {
    const department = localStorage.getItem("omnivault-department") || "";
    return request<HealthResponse>(`/api/health?department=${encodeURIComponent(department)}`);
  },
  settings: () => request<RuntimeSettings>("/api/settings"),
  updateSettings: (body: Partial<RuntimeSettings>) =>
    request<RuntimeSettings>("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  documents: () => request<DocumentItem[]>("/api/documents"),
  upload: async (files: FileList | File[], lang: "en" | "tr" = "en", department = "public") => {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("files", file));
    return request<DocumentItem[]>(`/api/documents/upload?lang=${lang}&department=${department}`, {
      method: "POST",
      body: form,
    });
  },
  note: (body: { title: string; body: string; department: string; lang: "en" | "tr" }) =>
    request<DocumentItem>("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  transcribe: async (blob: Blob, filename: string, lang: "en" | "tr" = "tr") => {
    const form = new FormData();
    form.append("file", blob, filename);
    return request<{ text: string; language: string }>(`/api/transcribe?lang=${lang}`, { method: "POST", body: form });
  },
  suggestions: (lang: "en" | "tr") => request<{ questions: string[]; persona: string }>(`/api/suggestions?lang=${lang}`),
  previewUrl: (url: string) =>
    request<{ title: string; text: string }>("/api/urls", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, preview: true }),
    }),
  ingestUrl: (url: string, lang: "en" | "tr", department = "public") =>
    request<DocumentItem>("/api/urls", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, lang, department }),
    }),
  register: (body: { username: string; password: string; full_name: string; department: string }) =>
    request<SessionUser>("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, role: "member" }),
    }),
  revisions: () => request<Array<Record<string, unknown>>>("/api/revisions"),
  rollback: (id: number) => request<{ ok: boolean }>(`/api/revisions/${id}/rollback`, { method: "POST" }),
  purgeVectors: (docId: string) => request<{ ok: boolean }>(`/api/vectors/${docId}`, { method: "DELETE" }),
  remove: (id: string) => request<{ ok: boolean }>(`/api/documents/${id}`, { method: "DELETE" }),
  reindex: (id: string) => request<DocumentItem>(`/api/documents/${id}/reindex`, { method: "POST" }),
  departmentOptions: () => request<Array<{ slug: string; name: string }>>("/api/departments/options"),
  transfer: (id: string, target_department: string, mode: "move" | "copy") =>
    request<{ document: DocumentItem; mode: string; department: string; department_name: string }>(
      `/api/documents/${id}/transfer`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_department, mode }),
      },
    ),
  mcpTools: () =>
    request<{
      tools: Array<{ name: string; description: string }>;
      count: number;
      cursor?: Record<string, unknown>;
    }>("/api/mcp/tools"),
  mcpResources: () => request<{ resources: Array<{ uri: string; name: string; description: string }>; count: number }>("/api/mcp/resources"),
  documentText: (id: string) => request<{ text: string }>(`/api/documents/${id}/text`),
  saveText: (id: string, text: string) =>
    request<DocumentItem>(`/api/documents/${id}/text`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  updateText: (id: string, text: string) =>
    request<DocumentItem>(`/api/documents/${id}/update-text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  reorderDepartment: (slug: string, direction: "up" | "down") =>
    request<Array<{ slug: string; name: string; users: number; files: number }>>("/api/departments/reorder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, direction }),
    }),
  query: (body: {
    question: string;
    top_k?: number;
    modalities?: string[];
    doc_ids?: string[];
    chat_model?: string;
    lang?: "en" | "tr";
    language?: "en" | "tr";
    engine?: string;
    conversation_id?: string;
    history?: Array<{ question: string; answer: string }>;
  }) =>
    request<QueryResponse>("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  seedDemo: (lang: "en" | "tr" = "en") =>
    request<{ created: DocumentItem[]; reused: DocumentItem[]; message: string }>(`/api/demo/seed?lang=${lang}`, {
      method: "POST",
    }),
  pdfPage: (docId: string, page: number) =>
    request<PdfPageResponse>(`/api/documents/${docId}/page/${page}`),
};

export function formatBytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatDuration(seconds?: number | null): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function formatClock(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}
