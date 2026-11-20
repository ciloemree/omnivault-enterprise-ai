import { api } from "./api";

export const QUERY_COLUMNS = ["Tarih", "Kullanıcı", "Unvan", "Departman", "Sohbet Başlığı", "Soru", "Yanıt", "Güven Skoru", "Model"];

export type HistoryExportFilters = {
  department?: string;
  username?: string;
  search?: string;
  conversationId?: string;
  queryIds?: number[];
};

export async function exportQueries(filename: string, kind: "csv" | "xlsx", filters: HistoryExportFilters = {}) {
  await api.exportHistory({
    filename,
    kind,
    department: filters.department || "",
    username: filters.username || "",
    search: filters.search || "",
    conversation_id: filters.conversationId || "",
    query_ids: filters.queryIds || [],
  });
}
