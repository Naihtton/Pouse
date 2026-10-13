#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -x "$project_root/.venv/bin/python" ] || [ ! -d "$project_root/frontend/node_modules" ]; then
  echo "Dependencies are missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

(
  cd "$project_root/backend"
  "$project_root/.venv/bin/python" -m pytest -q
)
(
  cd "$project_root/frontend"
  npm run typecheck
  npm run build
)

