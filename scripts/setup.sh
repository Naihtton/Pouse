#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_command="${PYTHON:-python3}"

if ! command -v "$python_command" >/dev/null 2>&1; then
  echo "Python 3.12 or newer is required." >&2
  exit 1
fi
if ! "$python_command" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
  echo "Python 3.12 or newer is required." >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js 22 and npm are required." >&2
  exit 1
fi
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) < 22 ? 1 : 0)'; then
  echo "Node.js 22 or newer is required." >&2
  exit 1
fi

if [ ! -x "$project_root/.venv/bin/python" ]; then
  "$python_command" -m venv "$project_root/.venv"
fi

"$project_root/.venv/bin/python" -m pip install -r "$project_root/backend/requirements.txt"
npm --prefix "$project_root/frontend" ci

echo "Pouse is ready. Run ./scripts/dev.sh to start API :8000 and web :3000."
