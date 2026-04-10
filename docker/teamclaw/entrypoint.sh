#!/usr/bin/env bash
set -euo pipefail

export TEAMCLAW_CONFIG_PATH="${TEAMCLAW_CONFIG_PATH:-/app/config-docker.yaml}"

mkdir -p /app/workspaces /app/avatars /app/logs /app/skills/user

cleanup() {
  local code=$?
  if [[ -n "${BACK_PID:-}" ]]; then
    kill -TERM "${BACK_PID}" 2>/dev/null || true
  fi
  if [[ -n "${FRONT_PID:-}" ]]; then
    kill -TERM "${FRONT_PID}" 2>/dev/null || true
  fi
  wait "${BACK_PID:-}" 2>/dev/null || true
  wait "${FRONT_PID:-}" 2>/dev/null || true
  exit "$code"
}

trap cleanup INT TERM EXIT

# Backend API
uvicorn app.main:app \
  --app-dir /app/backend \
  --host 0.0.0.0 \
  --port 8000 \
  --log-level info &
BACK_PID=$!

# Frontend static server (SPA fallback to index.html)
python3 /app/docker/teamclaw/serve_frontend.py &
FRONT_PID=$!

wait -n "$BACK_PID" "$FRONT_PID"
