#!/usr/bin/env bash
# Multi-worker uvicorn launcher for production API nodes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend"

cd "${BACKEND_DIR}"

if [[ -f "${BACKEND_DIR}/venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${BACKEND_DIR}/venv/bin/activate"
elif [[ -f "${BACKEND_DIR}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${BACKEND_DIR}/.venv/bin/activate"
fi

export WORKER_MODE="${WORKER_MODE:-api}"

WORKERS="${UVICORN_WORKERS:-3}"
PORT="${UVICORN_PORT:-8082}"
LIMIT="${UVICORN_LIMIT_CONCURRENCY:-96}"
KEEP_ALIVE="${UVICORN_TIMEOUT_KEEP_ALIVE:-60}"

exec uvicorn main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WORKERS}" \
  --timeout-keep-alive "${KEEP_ALIVE}" \
  --limit-concurrency "${LIMIT}"
