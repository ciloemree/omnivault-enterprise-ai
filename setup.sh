#!/usr/bin/env bash
# OmniVault bootstrap — Apple Silicon / macOS first-class.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "==> OmniVault setup"
echo "    workspace: $ROOT"

# --------------------------------------------------------------------------- helpers
have() { command -v "$1" >/dev/null 2>&1; }

PY=""
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif have python3.12; then
  PY="$(command -v python3.12)"
elif have python3; then
  PY="$(command -v python3)"
else
  echo "ERROR: Python 3.12+ is required."
  exit 1
fi

PY_VER="$("$PY" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
echo "    python: $PY ($PY_VER)"

# Prefer creating / reusing a local venv on 3.12+
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "==> Creating virtualenv (.venv)"
  if have uv; then
    uv venv "$ROOT/.venv" --python 3.12 || uv venv "$ROOT/.venv" --python "$PY"
  else
    "$PY" -m venv "$ROOT/.venv"
  fi
fi
VENV_PY="$ROOT/.venv/bin/python"
VENV_PIP="$ROOT/.venv/bin/pip"

"$VENV_PY" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"OmniVault needs Python >= 3.10 in .venv (found {sys.version})")
print(f"    venv ok: {sys.version.split()[0]}")
PY

echo "==> Installing Python dependencies"
"$VENV_PY" -m pip install --upgrade pip wheel >/dev/null
if have uv; then
  uv pip install --python "$VENV_PY" -r "$ROOT/requirements.txt"
else
  "$VENV_PIP" install -r "$ROOT/requirements.txt"
fi

# --------------------------------------------------------------------------- Node / frontend
if ! have node || ! have npm; then
  echo "WARN: node/npm not found — frontend build skipped. Install Node 20+ to use the UI."
else
  echo "==> Installing frontend dependencies"
  (cd "$ROOT/frontend" && npm install)
  echo "==> Building frontend"
  (cd "$ROOT/frontend" && npm run build)
fi

# --------------------------------------------------------------------------- ffmpeg (optional but useful for exotic containers)
if have ffmpeg; then
  echo "    ffmpeg: $(ffmpeg -version | head -n1)"
else
  echo "WARN: ffmpeg not on PATH. WAV/MP3/M4A still work via PyAV; install ffmpeg for broader codecs."
  if have brew; then
    echo "      tip: brew install ffmpeg"
  fi
fi

# --------------------------------------------------------------------------- Ollama
if ! have ollama; then
  echo "WARN: ollama CLI not found. Install from https://ollama.com and re-run setup."
else
  echo "==> Checking Ollama"
  if ! curl -sf --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null; then
    echo "    starting Ollama serve in background…"
    (ollama serve >/tmp/omnivault-ollama.log 2>&1 &) || true
    sleep 2
  fi
  if curl -sf --max-time 5 http://127.0.0.1:11434/api/tags >/dev/null; then
    echo "    Ollama is reachable at http://127.0.0.1:11434"
    ensure_model() {
      local model="$1"
      if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$model" || \
         ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -q "^${model}:"; then
        echo "    model present: $model"
      else
        echo "    pulling $model …"
        ollama pull "$model"
      fi
    }
    ensure_model "nomic-embed-text"
    # Prefer a lightweight chat model already common on Air-class machines.
    if ! ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Eq 'qwen2.5:3b|llama3.2:3b|phi3:mini|gemma2:2b'; then
      ensure_model "qwen2.5:3b"
    else
      echo "    chat model already installed (auto-detected at runtime)"
    fi
    if ! ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Eq 'moondream|llava|minicpm-v|qwen2.5vl|llama3.2-vision'; then
      ensure_model "moondream"
    else
      echo "    vision model already installed (auto-detected at runtime)"
    fi
  else
    echo "WARN: could not reach Ollama. Start it with: ollama serve"
  fi
fi

mkdir -p "$ROOT/data"

cat <<EOF

================================================================
OmniVault is ready.

Two-step launch:
  1) ./run.sh
  2) Open http://127.0.0.1:8000

Dev UI (optional hot reload):
  Terminal A: ./run.sh --api-only
  Terminal B: cd frontend && npm run dev
================================================================
EOF
