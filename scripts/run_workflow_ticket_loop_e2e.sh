#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Avoid macOS Screen Recording permission prompts during harness runs (headless Playwright only).
export DECISIONS_SKIP_UI_SCREEN_CAPTURE=1

PROFILE="${WORKFLOW_E2E_PROFILE:-until-green}"
PYTEST_TARGET="tests/ui/test_workflow_ticket_loop_browser_playwright_e2e.py"
PYTEST_EXTRA=()

if [ "${1:-}" = "--profile" ]; then
  PROFILE="${2:-until-green}"
  shift 2
fi

# Wipe stamped legacy harness clutter before each run; seeds recreate one canonical fixture.
rtk python3 scripts/workflow_ticket_loop_e2e.py cleanup-e2e-harness

if [ "$PROFILE" = "spotify" ] || [ "$PROFILE" = "dogfood" ]; then
  PROFILE="spotify"
  PYTEST_TARGET="tests/core/test_spotify_program_live_e2e.py"
  PYTEST_MARKER="e2e"
else
  PYTEST_MARKER="e2e_playwright"
fi

if [ "$#" -eq 0 ]; then
  set -- "${PYTEST_EXTRA[@]}" -q
fi

rtk python3 -m pytest -m "$PYTEST_MARKER" "$PYTEST_TARGET" "$@"
