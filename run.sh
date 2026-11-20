#!/usr/bin/env bash
# Launch OmniVault API (serves the built SPA when frontend/dist exists).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

API_ONLY=0
HOST="${OMNIVAULT_HOST:-127.0.0.1}"
PORT="${OMNIVAULT_PORT:-8000}"

for arg in "$@"; do
  case "$arg" in
    --api-only) API_ONLY=1 ;;
    --host=*) HOST="${arg#*=}" ;;
    --port=*) PORT="${arg#*=}" ;;
  esac
done

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Virtualenv missing. Run ./setup.sh first."
  exit 1
fi

if ! curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "WARN: Ollama is not reachable at http://127.0.0.1:11434"
  echo "      Start it with: ollama serve"
fi

if [[ "$API_ONLY" -eq 0 && ! -f "$ROOT/frontend/dist/index.html" ]]; then
  if command -v npm >/dev/null 2>&1; then
    echo "==> frontend/dist missing — building UI"
    (cd "$ROOT/frontend" && npm install && npm run build)
  else
    echo "WARN: frontend/dist missing and npm unavailable; API-only mode."
  fi
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export OMNIVAULT_HOST="$HOST"
export OMNIVAULT_PORT="$PORT"

echo "==> OmniVault listening on http://${HOST}:${PORT}"
exec "$ROOT/.venv/bin/python" -m uvicorn backend.main:app --host "$HOST" --port "$PORT" --log-level info
