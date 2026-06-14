#!/usr/bin/env bash
set -euo pipefail

if ! command -v codex >/dev/null 2>&1; then
  echo "missing: codex command is not on PATH"
  exit 1
fi

codex --version || true
echo "ready: DecisionsAI can use backend id 'codex'"
