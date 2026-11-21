# OmniVault

**Local-first and hybrid multimodal enterprise memory.** OmniVault ingests contracts, spreadsheets, notes, meeting audio, photos, and web pages into a department-scoped vault, then answers with grounded citations.

The default path is 100% offline: Ollama, faster-whisper, Apple Vision OCR, and ChromaDB stay on `localhost`. Optional Google Gemini, Google Gemma, and Groq engines can write the final answer. When a cloud engine is selected, the KVKK Privacy Shield redacts email, phone, IBAN, TCKN, card, and IP values before the prompt leaves the machine and restores them in the visible answer. Embeddings, OCR, and Whisper never leave the device.

## Why it exists

Enterprises drown in fragmented knowledge (contracts, recordings, whiteboard photos, payroll notes) while KVKK and GDPR forbid shipping that material to a public LLM by default. OmniVault is a local control plane that:

1. **Ingests every working modality** into one department-scoped memory.
2. **Answers with grounded citations** you can open (PDF page, audio timestamp, image lightbox).
3. **Keeps Apple Silicon RAM orderly** so vision, Whisper, and chat never sit in memory together.
4. **Isolates departments** so an İK question cannot read Finans vectors unless an administrator explicitly opens the whole vault.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    React + Tailwind SPA                          │
│  Sign-in · Ask · Vault · History · Team · Settings               │
│  Citations · Markdown tables · Update confirmation · Transfer    │
└───────────────────────────────┬──────────────────────────────────┘
                                │  Bearer session  /api/*
┌───────────────────────────────▼──────────────────────────────────┐
│                     FastAPI control plane                        │
│  RBAC · Ingestion · RAG · Table agent · Briefings · SSE          │
│  Privacy Shield on every cloud prompt                            │
└───┬────────────┬──────────────┬───────────────┬─────────┬────────┘
    │            │              │               │         │
 PDF/DOCX     CSV/XLSX/XLS    Audio/WebM      Images    Web URL
 PyMuPDF      pandas agent    faster-whisper  Vision    article text
 + TXT/MD     (not embedded)  int8            OCR       → Markdown
    │            │              │               │         │
    └────────────┴──────┬───────┴───────────────┴─────────┘
                        │
        SQLite (omnivault.db + users.db) · Chroma · staging · trash
                        │
                        ▼
              Answer router (selected per question)
     Local Ollama qwen2.5:3b  ·  Gemma 4 26B  ·  Gemini Flash  ·  Groq Llama 3 70B
     Embeddings stay on nomic-embed-text. Vision calls use keep_alive=0.
                        │
                        ▼
              stdio MCP  (backend/mcp_server.py)
     Cursor / Claude Desktop  ·  same vault, department-scoped tools
```

### Signature engineering touches

| Capability | Detail |
|---|---|
| **Department isolation and RBAC** | Built-in rooms: Yönetim, İK, Finans, Teknik, plus custom departments. **Admin** (`role=admin`, department `all`) sees every room when the workspace is “all” and manages users and departments. **Editor** (`permission=editor`, the default) can ingest, edit, and transfer inside their own department; `dept_admin` can also delete. **Reader** (`role=reader` or `permission=read_only`) can ask and cannot change the vault. A reader role is pinned to Yönetim. Vectors are filtered by department before retrieval. |
| **Full multimodal ingestion** | PDF, DOCX, TXT, MD, and images are chunked into Chroma. CSV, XLSX, and XLS stay as pandas frames (`embed_model=table-agent`, zero chunks) and are answered by a restricted pandas agent. Audio (MP3, WAV, M4A, OGG, FLAC, AAC, live WebM) is transcribed with faster-whisper. Images use Apple Vision OCR, with an optional local or Gemma caption. `POST /api/urls` pulls readable article text from an http(s) page. |
| **Versioning and conflict resolution** | The same filename, or the same `Adım` / `Soyadım` note, inside one department replaces the older file, drops its vectors, and bumps `meta.version` from `v1` to `v2`. The rule does not cross departments. When two sources disagree, the newest timestamp is the active fact; the answer states it first and then cites the older value and its date. |
| **Dynamic Markdown tables** | When an answer has several people, salaries, expenses, dates, or a comparison, the selected model is instructed to emit a Markdown table. The UI renders that table as HTML. Spreadsheet math is computed by the pandas agent, not by stuffing cells into the vector index. |
| **Confirmed chat updates** | A command such as “Hasan Doğan'ın maaşını 60.000 TL yap” is detected before the LLM. The chat shows the target record, the current text, and the proposed text, with confirm and cancel. Confirm calls `POST /api/documents/{id}/update-text`, rewrites the note, re-embeds it, and increments the version. Read-only users receive a refusal. Nothing is written on the first pass. |
| **Zero-reingest transfer** | Move updates the SQLite department and the stored Chroma metadata, including the original embeddings, with no OCR, Whisper, or new embedding pass. Copy clones the file and the vectors into the target department as `v1`. |
| **Interactive citations** | PDF opens the rendered page and snippet. Audio seeks an HTML5 player to `start_time`. Images open in a lightbox. |
| **Executive briefings** | After a successful index, OmniVault stores a short summary, tags, and entities. Notes under 400 characters use a factual briefing so a short HR card is not paraphrased away. |
| **Hallucination guard** | Strict grounded prompting, plus Chroma cosine distance mapped to a 0–100 grounding meter. Citation chips stay hidden only when the entire answer is a refusal. |
| **Apple Silicon RAM discipline** | Vision calls send `keep_alive: 0` and unload immediately. Chat models are unloaded before Whisper or vision loads. Whisper defaults to `base` / `int8` / CPU. Context window scales with RAM: 6144 on an 8 GB Air, 8192 at 16 GB, 12288 above that. Images are downscaled to a 768 px long side before a vision call. |
| **KVKK Privacy Shield** | Optional locally (`OMNIVAULT_PRIVACY_SHIELD`, default `false`). Forced on for every Google or Groq answer. Tokens such as `[[EMAIL_1]]` are reversed before the answer is shown. |
| **Hybrid answer router** | The engine menu offers local Qwen 2.5 3B, Groq Llama 3 70B, Google Gemma 4 26B, and Gemini Flash. Embeddings stay on Ollama even when the answer is written in the cloud. |
| **Live model picker** | `GET /api/tags` on startup. Chat, vision, and embed models are whatever you already pulled. |

## Prerequisites

- macOS on Apple Silicon is the intended host. Other Unix systems run with a weaker OCR path.
- Python 3.12+ (setup creates `.venv`)
- Node.js 20+ and npm, for the UI
- [Ollama](https://ollama.com) on the loopback interface
- Optional: `ffmpeg` on `PATH` for unusual containers. PyAV already covers WAV, MP3, and M4A.

Suggested local models (`setup.sh` pulls them when they are missing):

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:3b
ollama pull moondream
```

## Environment

Copy `.env.example` to `.env`. Empty cloud keys keep the app fully offline.

| Variable | Role |
|---|---|
| `OMNIVAULT_ADMIN_USER` / `OMNIVAULT_ADMIN_PASS` | First administrator. Defaults: `admin` / `admin123`. Change both before any shared machine. |
| `OMNIVAULT_HOST` / `OMNIVAULT_PORT` | Bind address. Default `127.0.0.1:8000`. |
| `OMNIVAULT_OLLAMA_URL` | Loopback Ollama. A public host is refused unless `OMNIVAULT_ALLOW_REMOTE_OLLAMA=true`. |
| `OMNIVAULT_CHAT_MODEL` / `OMNIVAULT_VISION_MODEL` / `OMNIVAULT_EMBED_MODEL` | Optional pins. Empty values are discovered from `GET /api/tags`. |
| `OMNIVAULT_PRIVACY_SHIELD` | Local redaction switch. Cloud engines enable the shield on their own. |
| `OMNIVAULT_STRICT_GUARD` | Grounded-answer guard. Default `true`. |
| `OMNIVAULT_TOP_K` | Local retrieval depth. Default `6`. Cloud answers may widen the pool. |
| `OMNIVAULT_CHAT_NUM_CTX` | Override the RAM-scaled context window. |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Either key unlocks Gemma and Gemini. Keys can also be saved later in Settings. |
| `GROQ_API_KEY` | Unlocks the Groq Llama route. |

## Quick start

```bash
./setup.sh
cp .env.example .env
./run.sh
```

Open **http://127.0.0.1:8000** and sign in. The screen offers four local demo personas, which call `POST /api/auth/demo` and open a real session:

| Button | Account | Scope |
|---|---|---|
| Admin | `admin` / `OMNIVAULT_ADMIN_PASS` | Every department |
| İK | `ik_uzmani` / `ik123` | İK |
| Finans | `finans_uzmani` / `finans123` | Finans |
| Teknik | `teknik_ekip` / `teknik123` | Teknik |

Upload a file, save a note, or paste a URL. Wait until the card reads `ready`, then ask in that department. There is no one-click sample corpus in the current UI. `backend/services/demo.py` still contains an unused HeliosPay generator, and process startup deletes any document whose origin is `demo`.

## Development mode

```bash
./run.sh --api-only          # API on :8000
cd frontend && npm run dev   # Vite on :5173, proxying /api
```

The production server serves `frontend/dist`. Rebuild with `npx vite build` inside `frontend/` after a UI change, then restart the API. The server does not use `--reload`.

## Model Context Protocol (MCP) integration

`backend/mcp_server.py` is two surfaces over one vault.

**HTTP catalog**, session-scoped and department-filtered like the rest of the API:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/mcp/tools` | Tool list plus a ready-to-copy stdio snippet |
| `GET` | `/api/mcp/resources` | `vault://{department}/documents` |
| `POST` | `/api/mcp/call` | Invoke a tool as the signed-in user |

HTTP tools: `search_department_vault`, `list_department_documents`, `search_vault`, `calculate_department_metrics`, `transfer_document`, `apply_document_update`.

**stdio server** for Cursor and Claude Desktop. Run the file directly; it speaks MCP on stdin/stdout and logs to stderr. The local process owner is treated as an administrator (`department: all`) because the desktop app launched it on this Mac. The stdio tool set is the three operations a desktop agent needs, plus one resource:

| Tool or resource | What it does |
|---|---|
| `search_department_vault` | Hybrid retrieval in one department. Returns snippets, filenames, timestamps, and version. |
| `list_department_documents` | Active documents, versions, and summaries in one department. |
| `transfer_document` | Move or copy without OCR, Whisper, or a new embedding pass. `mode` is `move` or `copy`. |
| `vault://{department}/documents` | JSON resource with the same department catalog. |

Example `.cursor/mcp.json`. Use the project virtualenv and the absolute path to the server. Do not put API keys in this file; the server reads `.env` itself. Keep `mcp.json` out of git.

```json
{
  "mcpServers": {
    "omnivault": {
      "command": "/absolute/path/to/omnivault-enterprise-ai/.venv/bin/python",
      "args": ["/absolute/path/to/omnivault-enterprise-ai/backend/mcp_server.py"]
    }
  }
}
```

Settings renders this snippet from `cursor_config()` and can copy it. The same shape works in Claude Desktop.

## API surface (selected)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Password session |
| `POST` | `/api/auth/demo` | Demo persona session (`admin`, `ik`, `finans`, `teknik`) |
| `GET` | `/api/auth/me` | Current user, role, permission, department |
| `GET/POST/PATCH/DELETE` | `/api/users` | Admin user management |
| `GET/POST/DELETE` | `/api/departments` | Admin department management |
| `GET` | `/api/health` | Ollama, Whisper, vectors, and RAM |
| `GET/PUT` | `/api/settings` | Models, guards, Privacy Shield, cloud key presence |
| `POST` | `/api/documents/upload` | Multimodal ingest into the caller’s department |
| `POST` | `/api/notes` | Save a Markdown note |
| `POST` | `/api/urls` | Ingest a web page as Markdown |
| `POST` | `/api/transcribe` | Transcribe a live WebM question; the audio is not kept |
| `POST` | `/api/query` | Department-scoped RAG or table-agent answer, citations, and an optional update proposal |
| `POST` | `/api/documents/{id}/update-text` | Apply a confirmed text edit and bump the version |
| `POST` | `/api/documents/{id}/transfer` | Move or copy without re-ingest |
| `GET` | `/api/documents/{id}/page/{n}` | PDF page render for a citation |
| `GET/DELETE` | `/api/history` | Per-department threads |
| `POST` | `/api/history/export` | Export a thread to `.xlsx` |
| `GET` | `/api/revisions` | Admin change log |
| `POST` | `/api/revisions/{id}/rollback` | Undo an ingest, or restore a delete from `trash/` |
| `GET` | `/api/events` | SSE ingest progress |
| `GET/POST` | `/api/mcp/tools`, `/api/mcp/call` | Session-scoped MCP |

Interactive docs: http://127.0.0.1:8000/docs

## Data layout

All runtime state stays under `./data/` and is gitignored.

```
data/
  omnivault.db      # documents, settings, departments, revisions, audit
  users.db          # accounts, sessions, query history (PBKDF2 passwords)
  chroma/           # persistent vectors, cosine, nomic-embed-text
  uploads/          # original files, one directory per document
  staging/          # short-lived upload, note, URL, and question-audio files
  trash/{doc_id}/   # deleted originals, kept so an admin can roll a delete back
  thumbnails/       # image and PDF page caches
  transcripts/      # Whisper JSON
  analysis/         # image OCR and description markdown
  models/whisper/   # faster-whisper weights
```

`staging/` is a handoff directory. The pipeline moves a file into `uploads/` or deletes a question recording after transcription. `trash/` is the rollback store for deletes.

## Privacy posture

- The Ollama URL must be loopback or private unless `OMNIVAULT_ALLOW_REMOTE_OLLAMA=true`.
- No telemetry (`ANONYMIZED_TELEMETRY=False`, Hugging Face telemetry disabled).
- Embeddings, OCR, and Whisper stay on the machine in both offline and hybrid mode.
- The Privacy Shield redacts PII before a cloud prompt and restores the tokens in the answer the user sees.
- Queries, ingests, transfers, and version bumps are written to the local SQLite audit trail.
- `.env`, `.cursor/mcp.json`, `.venv/`, and `data/` are gitignored.

## License

MIT. Built for local enterprise demonstration and investor technical diligence.
