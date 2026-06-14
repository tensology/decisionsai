#!/usr/bin/env bash
set -euo pipefail

if ! command -v cursor-agent >/dev/null 2>&1; then
  echo "missing: cursor-agent command is not on PATH"
  exit 1
fi

cursor-agent --version || true
echo "ready: DecisionsAI can use backend id 'cursor'"
