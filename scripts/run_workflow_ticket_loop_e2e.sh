#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PROFILE="${WORKFLOW_E2E_PROFILE:-until-green}"
if [ -n "${DECISIONSAI_PYTHON:-}" ]; then
  PYTHON_BIN="$DECISIONSAI_PYTHON"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -x "${WORKON_HOME:-$HOME/.virtualenvs}/decisions/bin/python" ]; then
  PYTHON_BIN="${WORKON_HOME:-$HOME/.virtualenvs}/decisions/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="python3.12"
else
  PYTHON_BIN="python3"
fi

if [ "${1:-}" = "--profile" ]; then
  PROFILE="${2:-until-green}"
  shift 2
fi

rtk "$PYTHON_BIN" scripts/run_isolated_workflow_e2e.py --profile "$PROFILE" "$@"
