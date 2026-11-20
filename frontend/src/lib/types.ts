export type Modality = "pdf" | "audio" | "image" | "text";

export interface Briefing {
  summary: string[];
  tags: string[];
  entities: string[];
  risk_flags?: string[];
  source?: string;
  model?: string;
}

export interface DocumentItem {
  id: string;
  filename: string;
  modality: Modality | string;
  file_type: string;
  mime?: string | null;
  size_bytes: number;
  sha256: string;
  origin: string;
  status: string;
  stage?: string | null;
  progress: number;
  message?: string | null;
  error?: string | null;
  chunk_count: number;
  embed_model?: string | null;
  meta: Record<string, unknown>;
  briefing?: Briefing | null;
  privacy?: Record<string, unknown> | null;
  created_at: number;
  updated_at: number;
  indexed_at?: number | null;
}

export interface Citation {
  id: string;
  index: number;
  modality: string;
  doc_id: string;
  filename: string;
  snippet: string;
  relevance: number;
  distance: number;
  page?: number | null;
  start_time?: number | null;
  end_time?: number | null;
  image_path?: string | null;
  thumbnail_url?: string | null;
  media_url?: string | null;
  preview_url?: string | null;
  department?: string | null;
  uploader?: string | null;
  created_at?: number | null;
}

export interface PendingUpdate {
  doc_id: string;
  filename: string;
  department?: string;
  old_snippet: string;
  new_snippet: string;
  new_text: string;
  kind?: string;
  subject?: string;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  grounding_score: number;
  grounding_label: string;
  modalities_used: string[];
  model: string;
  retrieved: number;
  privacy_redactions: Record<string, number>;
  latency_ms: number;
  engine?: string;
  provider?: string;
  engine_label?: string;
  conversation_id?: string;
  pending_update?: PendingUpdate | null;
}

export interface ModelOption {
  name: string;
  size: number;
  parameter_size?: string;
  family?: string;
  quantization?: string;
  embedding_length?: number;
}

export interface RuntimeSettings {
  chat_model: string;
  vision_model: string;
  embed_model: string;
  privacy_shield: boolean;
  top_k: number;
  strict_guard: boolean;
  chat_keep_alive: string;
  embed_keep_alive: string;
  chat_num_ctx: number;
  ollama_url: string;
  gemini_key_set?: boolean;
  groq_key_set?: boolean;
  engines?: { id: string; provider: string; model: string; label_en: string; label_tr: string }[];
  available: {
    chat: ModelOption[];
    vision: ModelOption[];
    embed: ModelOption[];
  };
}

export interface HealthResponse {
  status: string;
  version: string;
  ollama: {
    online: boolean;
    url: string;
    error?: string | null;
    models: Array<Record<string, unknown>>;
    loaded: Array<Record<string, unknown>>;
    selected: Record<string, string | null>;
  };
  whisper: Record<string, unknown>;
  vector_count: number;
  document_count: number;
  privacy_shield: boolean;
  ram_gb: number;
  platform: string;
}

export interface PdfPageResponse {
  doc_id: string;
  page: number;
  page_count: number;
  width: number;
  height: number;
  image_url: string;
  text: string;
}

export type CitationFocus =
  | { kind: "pdf"; citation: Citation }
  | { kind: "audio"; citation: Citation }
  | { kind: "image"; citation: Citation }
  | null;
