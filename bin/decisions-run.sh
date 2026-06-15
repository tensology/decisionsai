#!/bin/bash
# Foreground app runner — started detached from the setup terminal.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

# shellcheck source=decisions-env.sh
source "$SCRIPT_DIR/bin/decisions-env.sh"

VENV_DIR="${VENV_DIR:-$HOME/.virtualenvs/decisions}"
RUN_DIR="$HOME/.decisions/run"
LOG_DIR="$HOME/.decisions/logs"

mkdir -p "$RUN_DIR" "$LOG_DIR"

# shellcheck source=decisions-sidecar.sh
source "$SCRIPT_DIR/bin/decisions-sidecar.sh"

_on_exit() {
    decisions_stop_sidecar
    rm -f "$RUN_DIR/decisions.pid" "$RUN_DIR/decisions-run.pid"
}
trap _on_exit EXIT

decisions_start_sidecar "$SCRIPT_DIR"
echo $$ > "$RUN_DIR/decisions-run.pid"

export DECISIONS_PYTHON="$VENV_DIR/bin/python"

# Do not exec: keep this shell alive so the EXIT trap stops the sidecar when Python exits.
"$VENV_DIR/bin/python" bin/start.py
exit_code=$?
_on_exit
exit "$exit_code"
