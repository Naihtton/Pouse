#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api_port="${API_PORT:-8000}"
web_port="${WEB_PORT:-3000}"

if [ ! -x "$project_root/.venv/bin/uvicorn" ] || [ ! -d "$project_root/frontend/node_modules" ]; then
  echo "Dependencies are missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

cleanup() {
  trap - INT TERM EXIT
  kill "$api_pid" "$web_pid" 2>/dev/null || true
  wait "$api_pid" "$web_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

(
  cd "$project_root/backend"
  exec "$project_root/.venv/bin/uvicorn" app.main:app --reload --host 0.0.0.0 --port "$api_port"
) &
api_pid=$!

(
  cd "$project_root/frontend"
  export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-http://localhost:$api_port}"
  exec npm run dev -- --port "$web_port"
) &
web_pid=$!

echo "Pouse API: http://localhost:$api_port"
echo "Pouse web: http://localhost:$web_port"
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
  sleep 1
done

echo "A development service stopped unexpectedly." >&2
exit 1
