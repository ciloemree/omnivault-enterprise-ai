# OmniVault

**On-premise multimodal enterprise memory & RAG** for PDF contracts, meeting audio, and architecture diagrams — optimized for Apple Silicon, with zero cloud LLM dependency.

OmniVault keeps institutional knowledge inside the building. Every byte of inference stays on `localhost` (Ollama + faster-whisper + Apple Vision OCR + ChromaDB).

## Why it exists

Enterprises drown in fragmented knowledge (contracts, recordings, whiteboard photos) while KVKK/GDPR forbid shipping that material to public LLM APIs. OmniVault is a local control plane that:

1. **Ingests three modalities** into one vector memory  
2. **Answers with grounded citations** you can click into (page / timestamp / lightbox)  
3. **Manages unified memory carefully** so vision and chat models never fight for RAM on a MacBook Air

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     React + Tailwind SPA                    │
│   Ask · Vault · Settings · Interactive multimodal citations │
└────────────────────────────┬────────────────────────────────┘
                             │ /api/*
┌────────────────────────────▼────────────────────────────────┐
│                     FastAPI control plane                   │
│  Ingestion pipeline · RAG · Briefings · Privacy Shield · SSE│
└───┬─────────────┬──────────────┬──────────────┬─────────────┘
    │             │              │              │
 PDF/TXT     Audio (.wav/mp3)  Images        SQLite + Chroma
 PyMuPDF     faster-whisper    Vision OCR    persistent local
 tables+OCR  int8 / MPS tip    + moondream   memory store
                                   │
                                   ▼
                         Ollama (loopback only)
              nomic-embed-text · chat LLM · vision LLM
              keep_alive=0 unload after vision calls
```

### Signature engineering touches

| Capability | Detail |
|---|---|
| **Interactive citations** | PDF → rendered page + snippet · Audio → HTML5 player seeked to `start_time` · Image → lightbox |
| **Executive briefings** | Auto 3-bullet summary + tags + entities after every successful index |
| **Hallucination guard** | Strict grounded prompting + Chroma cosine distance → 0–100 grounding meter |
| **Apple Silicon RAM discipline** | Vision calls send `keep_alive: 0`; chat models are unloaded before Whisper/vision loads |
| **Privacy Shield** | Optional reversible redaction of email / phone / IBAN / TCKN / cards before prompts |
| **One-click demo** | HeliosPay policy PDF + architecture diagram + DR meeting WAV |
| **Dynamic model picker** | `GET /api/tags` on startup — use whatever you already pulled |

## Prerequisites

- macOS Apple Silicon recommended (works on other Unix with fewer OCR niceties)
- Python **3.12+** (setup creates `.venv`)
- Node.js **20+** / npm (for the UI)
- [Ollama](https://ollama.com) running locally
- Optional: `ffmpeg` on PATH for exotic media containers (PyAV covers WAV/MP3/M4A)

Suggested local models (auto-pulled by `setup.sh` when missing):

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:3b
ollama pull moondream
```

## Two-step launch

```bash
./setup.sh
./run.sh
```

Then open **http://127.0.0.1:8000**

In the UI, click **Load Sample Enterprise Data**, wait until the three assets show `ready`, then ask:

> What is the Tier 1 RTO and RPO for the payment gateway?

## Development mode

```bash
./run.sh --api-only          # API on :8000
cd frontend && npm run dev   # Vite on :5173 (proxies /api)
```

## API surface (selected)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Ollama / Whisper / vector / RAM snapshot |
| `GET/PUT` | `/api/settings` | Dynamic model selection + guards |
| `POST` | `/api/documents/upload` | Multimodal ingest |
| `POST` | `/api/query` | Cross-modal RAG + citations + grounding |
| `POST` | `/api/demo/seed` | One-click HeliosPay corpus |
| `GET` | `/api/events` | SSE live document progress |
| `GET` | `/api/documents/{id}/page/{n}` | PDF page render for citations |

Interactive docs: http://127.0.0.1:8000/docs

## Data layout

All state stays under `./data/` (gitignored):

```
data/
  omnivault.db      # document registry + audit trail
  chroma/           # persistent vectors
  uploads/          # original files
  thumbnails/       # image + PDF page caches
  transcripts/      # Whisper JSON
  analysis/         # image OCR+description markdown
  models/whisper/   # faster-whisper weights
  demo/             # generated sample corpus
```

## Privacy posture

- Ollama URL must be loopback/private unless `OMNIVAULT_ALLOW_REMOTE_OLLAMA=true`
- No telemetry (`ANONYMIZED_TELEMETRY=False`, HF telemetry disabled)
- Optional Privacy Shield redacts PII before any model call and restores tokens in the visible answer
- Audit trail records queries and ingest events locally in SQLite

## License

MIT — built for local enterprise demonstration and investor technical diligence.
