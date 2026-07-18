#!/usr/bin/env bash
# Start the ATRIUM NLP Enrichment API server and wait until it is healthy.
#
# Prefers Docker Compose (the `api` profile), falls back to a local uvicorn
# launch provisioned by setup_api_service.sh.
#
# Usage:
#   bash scripts/server.sh            # Docker Compose api profile, or local fallback
#   bash scripts/server.sh --local    # skip Docker, run uvicorn directly
#
# Environment:
#   ATRIUM_NE_PORT  - port to serve on (default: 8000)
#   ATRIUM_NE_URL   - health-check target (default: http://localhost:$ATRIUM_NE_PORT)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${ATRIUM_NE_PORT:-8000}"
BASE_URL="${ATRIUM_NE_URL:-http://localhost:${PORT}}"
HEALTH_URL="${BASE_URL}/info"
MODE="auto"

for arg in "$@"; do
    case "$arg" in
        --local) MODE="local" ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

# Already running? Nothing to do.
if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    echo "✅ API already healthy at ${BASE_URL}"
    exit 0
fi

cd "$REPO_ROOT"

start_docker() {
    echo "🐳 Starting via docker compose --profile api..."
    docker compose --profile api up -d
}

start_local() {
    echo "🐍 Starting local uvicorn server..."
    if [ ! -d "venv-nlp" ]; then
        echo "No venv found - provisioning via setup_api_service.sh (install only)..."
        bash setup_api_service.sh --no-serve
    fi
    # shellcheck disable=SC1091
    source venv-nlp/bin/activate
    nohup uvicorn service.api:app --host 0.0.0.0 --port "$PORT" > api_server.log 2>&1 &
    echo "Server PID: $! (logs: api_server.log)"
}

case "$MODE" in
    local) start_local ;;
    auto)
        if command -v docker > /dev/null 2>&1 && docker info > /dev/null 2>&1; then
            start_docker
        else
            start_local
        fi
        ;;
esac

# First launch downloads the KeyBERT sentence-transformer embedding model into
# the HF cache - allow a generous startup window.
echo "⏳ Waiting for ${HEALTH_URL} (model prefetch on first run may take several minutes)..."
DEADLINE=$((SECONDS + 900))
until curl -sf "$HEALTH_URL" > /dev/null 2>&1; do
    if [ "$SECONDS" -ge "$DEADLINE" ]; then
        echo "❌ Server did not become healthy within 15 minutes." >&2
        echo "   Check: api_server.log (local) or 'docker compose --profile api logs' (Docker)." >&2
        exit 1
    fi
    sleep 5
done

echo "✅ API healthy at ${BASE_URL}"
