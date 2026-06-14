#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$#" -eq 0 ]; then
  set -- --browser chromium --browser webkit -q
fi

rtk python3 -m pytest -m e2e_playwright tests/ui/test_workflow_ticket_loop_browser_playwright_e2e.py "$@"
