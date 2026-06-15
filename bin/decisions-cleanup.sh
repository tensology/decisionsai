#!/bin/bash
# External DecisionsAI cleanup — runs detached after fast quit, or via ./decisions --stop

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$HOME/.decisions/run"
LOG_DIR="$HOME/.decisions/logs"
WORKER_PID_FILE="$SCRIPT_DIR/db/worker_pids.txt"

MAIN_PID=""
AGENT_PID=""
DETACH=0

while [ $# -gt 0 ]; do
    case "$1" in
        --main-pid)
            MAIN_PID="${2:-}"
            shift 2
            ;;
        --agent-pid)
            AGENT_PID="${2:-}"
            shift 2
            ;;
        --project-root)
            SCRIPT_DIR="$(cd "${2:-$SCRIPT_DIR}" && pwd)"
            WORKER_PID_FILE="$SCRIPT_DIR/db/worker_pids.txt"
            shift 2
            ;;
        --detach)
            DETACH=1
            shift
            ;;
        *)
            shift
            ;;
    esac
done

mkdir -p "$LOG_DIR"

if [ "$DETACH" -eq 1 ]; then
    # Re-exec without --detach so the parent can return immediately.
    reexec_args=(--project-root "$SCRIPT_DIR")
    [ -n "$MAIN_PID" ] && reexec_args+=(--main-pid "$MAIN_PID")
    [ -n "$AGENT_PID" ] && reexec_args+=(--agent-pid "$AGENT_PID")
    nohup /bin/bash "$0" "${reexec_args[@]}" >>"$LOG_DIR/cleanup.log" 2>&1 &
    disown "$!" 2>/dev/null || true
    exit 0
fi

# shellcheck source=decisions-sidecar.sh
source "$SCRIPT_DIR/bin/decisions-sidecar.sh"

_log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') cleanup: $*"
}

_is_alive() {
    local pid="$1"
    [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null && kill -0 "$pid" 2>/dev/null
}

_kill_pid() {
    local pid="$1"
    local sig="${2:-TERM}"
    if _is_alive "$pid"; then
        kill -"$sig" "$pid" 2>/dev/null || true
    fi
}

_collect_children() {
    local pid="$1"
    if command -v pgrep &>/dev/null; then
        pgrep -P "$pid" 2>/dev/null || true
    fi
}

_kill_tree() {
    local pid="$1"
    local sig="${2:-TERM}"
    [ -n "$pid" ] || return 0
    local child
    for child in $(_collect_children "$pid"); do
        _kill_tree "$child" "$sig"
    done
    _kill_pid "$pid" "$sig"
}

_kill_pids_from_file() {
    local sig="$1"
    local file="$2"
    [ -f "$file" ] || return 0
    local pid
    while IFS= read -r pid || [ -n "$pid" ]; do
        pid="${pid//[[:space:]]/}"
        [ -z "$pid" ] && continue
        _kill_tree "$pid" "$sig"
    done <"$file"
}

_log "starting (main=${MAIN_PID:-none} agent=${AGENT_PID:-none})"

# Graceful pass
if [ -n "$AGENT_PID" ]; then
    _kill_tree "$AGENT_PID" TERM
fi
_kill_pids_from_file TERM "$WORKER_PID_FILE"

if command -v pgrep &>/dev/null; then
    while IFS= read -r pid; do
        [ -z "$pid" ] && continue
        [ -n "$MAIN_PID" ] && [ "$pid" = "$MAIN_PID" ] && continue
        _kill_tree "$pid" TERM
    done < <(pgrep -f "${SCRIPT_DIR}/bin/start.py" 2>/dev/null || true)
fi

sleep 0.75

# Force pass
if [ -n "$AGENT_PID" ]; then
    _kill_tree "$AGENT_PID" KILL
fi
_kill_pids_from_file KILL "$WORKER_PID_FILE"

if command -v pgrep &>/dev/null; then
    while IFS= read -r pid; do
        [ -z "$pid" ] && continue
        [ -n "$MAIN_PID" ] && [ "$pid" = "$MAIN_PID" ] && continue
        _kill_tree "$pid" KILL
    done < <(pgrep -f "${SCRIPT_DIR}/bin/start.py" 2>/dev/null || true)
fi

decisions_stop_sidecar
rm -f "$RUN_DIR/decisions.pid" "$RUN_DIR/decisions-run.pid" "$WORKER_PID_FILE"

_log "done"
