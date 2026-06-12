#!/usr/bin/env bash
# setup_api_service.sh — provision and launch the nlp-enrich FastAPI service.
#
# Mirrors the page-classification reference: create a venv, install the repo +
# service requirements, prefetch the KeyBERT sentence-transformer into HF_HOME
# so the first request does not pay model-load latency, then start uvicorn.
#
# Usage:
#   ./setup_api_service.sh            # install + launch on :8000
#   ./setup_api_service.sh --no-serve # install only
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV:-$HERE/venv-nlp}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
KEYBERT_MODEL="${KEYBERT_MODEL:-paraphrase-multilingual-MiniLM-L12-v2}"
export HF_HOME="${HF_HOME:-$HERE/.cache/huggingface}"

echo "[setup] venv: $VENV"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

pip install --upgrade pip
pip install -r "$HERE/requirements.txt"
pip install -r "$HERE/service/requirements.txt"

echo "[setup] prefetching KeyBERT model '$KEYBERT_MODEL' into $HF_HOME"
python3 - <<PY
from sentence_transformers import SentenceTransformer
SentenceTransformer("${KEYBERT_MODEL}")
print("[setup] model cached.")
PY

if [ "${1:-}" = "--no-serve" ]; then
    echo "[setup] install complete (--no-serve)."
    exit 0
fi

echo "[setup] launching: uvicorn service.api:app --host $HOST --port $PORT"
exec uvicorn service.api:app --host "$HOST" --port "$PORT"
