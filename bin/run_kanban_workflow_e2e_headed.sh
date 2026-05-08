#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://127.0.0.1:8765}"
BROWSER_NAME="${BROWSER_NAME:-webkit}"
SLOWMO_MS="${SLOWMO_MS:-500}"

echo "==> Checking web UI availability at ${BASE_URL}"
if ! python3 - <<'PY'
import os
import urllib.request

base = os.environ.get("BASE_URL", "http://127.0.0.1:8765")
urllib.request.urlopen(f"{base}/kanban/", timeout=3)
print("Web UI reachable.")
PY
then
  echo "Web UI is not reachable."
  echo "Start the app/server first, then run this script again."
  exit 1
fi

echo "==> Running headed Playwright flow (${BROWSER_NAME})"
echo "    This opens a visible browser and walks the Kanban workflow flow."
pytest -q \
  tests/ui/test_kanban_workflow_e2e_webkit.py \
  tests/ui/test_workflows_active_run_webkit.py \
  -m e2e_playwright \
  --browser "${BROWSER_NAME}" \
  --headed \
  --slowmo "${SLOWMO_MS}" \
  -s

echo "==> Done"
