#!/bin/bash
# DecisionsAI.app — CFBundleExecutable template (copied into decisions.app/Contents/MacOS/decisions).

set -u

APP_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT_ROOT="$(cd "$APP_ROOT/.." && pwd)"
LOG_DIR="$HOME/.decisions/logs"
LOG_FILE="$LOG_DIR/launcher.log"
RUN_DIR="$HOME/.decisions/run"
VENV_PYTHON="${VENV_PYTHON:-$HOME/.virtualenvs/decisions/bin/python}"

mkdir -p "$LOG_DIR" "$RUN_DIR"

# The dock wrapper captures stderr from native libraries as well as Python.
# Keep that useful crash trail bounded; Pipecat/loguru debug output otherwise
# grows this file indefinitely and creates avoidable disk churn while idle.
MAX_LAUNCHER_LOG_BYTES="${DECISIONSAI_LAUNCHER_LOG_MAX_BYTES:-10485760}"
if [ -f "$LOG_FILE" ]; then
    current_log_bytes=$(wc -c < "$LOG_FILE" | tr -d ' ')
    if [ "${current_log_bytes:-0}" -ge "$MAX_LAUNCHER_LOG_BYTES" ]; then
        rm -f "$LOG_FILE.3"
        [ ! -f "$LOG_FILE.2" ] || mv "$LOG_FILE.2" "$LOG_FILE.3"
        [ ! -f "$LOG_FILE.1" ] || mv "$LOG_FILE.1" "$LOG_FILE.2"
        mv "$LOG_FILE" "$LOG_FILE.1"
    fi
fi

export DECISIONS_DOCK_APP=1
export DECISIONS_APP_BUNDLE="$APP_ROOT"
export DECISIONS_PROJECT_ROOT="$PROJECT_ROOT"

# shellcheck source=decisions-env.sh
source "$PROJECT_ROOT/bin/decisions-env.sh"

{
    echo "===== DecisionsAI dock launch $(date) ====="
    echo "Project root: $PROJECT_ROOT"

    if pgrep -f "[Pp]ython.*${PROJECT_ROOT}/bin/start.py" > /dev/null 2>&1; then
        echo "DecisionsAI already running — requesting activation."
        touch "$RUN_DIR/activate.request"
        exit 0
    fi

    if [ ! -x "$PROJECT_ROOT/bin/decisions-run.sh" ]; then
        echo "Missing bin/decisions-run.sh"
        exit 1
    fi

    if [ ! -x "$VENV_PYTHON" ]; then
        echo "First launch — running setup in foreground."
        exec /bin/bash "$PROJECT_ROOT/bin/decisions.sh" --foreground
    fi

    echo "Starting DecisionsAI (dock mode)."
    exec /bin/bash "$PROJECT_ROOT/bin/decisions-run.sh"
} >>"$LOG_FILE" 2>&1
