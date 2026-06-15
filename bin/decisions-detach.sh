#!/bin/bash
# Launch DecisionsAI in a session independent of the current terminal.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$HOME/.decisions/logs/decisions.log"
RUN_DIR="$HOME/.decisions/run"
RUN_SCRIPT="$SCRIPT_DIR/bin/decisions-run.sh"

mkdir -p "$HOME/.decisions/logs" "$RUN_DIR"

if pgrep -f "[b]in/start.py" > /dev/null 2>&1; then
    echo "DecisionsAI is already running."
    exit 0
fi

if [ ! -x "$RUN_SCRIPT" ]; then
    chmod +x "$RUN_SCRIPT"
fi

launch_detached() {
    if [[ "$OSTYPE" == darwin* ]]; then
        # launchd child — survives Terminal window close.
        /usr/bin/osascript -e "do shell script \"cd '$SCRIPT_DIR' && /bin/bash '$RUN_SCRIPT' >> '$LOG_FILE' 2>&1 &\" with dismiss" >/dev/null 2>&1
        return $?
    fi

    if command -v setsid >/dev/null 2>&1; then
        setsid -f "$RUN_SCRIPT" >> "$LOG_FILE" 2>&1 &
        disown
        return 0
    fi

    nohup "$RUN_SCRIPT" >> "$LOG_FILE" 2>&1 &
    disown
}

if ! launch_detached; then
    echo "Failed to start DecisionsAI in the background."
    exit 1
fi

waited=0
while [ "$waited" -lt 90 ]; do
    if pgrep -f "[b]in/start.py" > /dev/null 2>&1; then
        app_pid=$(pgrep -f "[b]in/start.py" | head -n 1)
        echo "$app_pid" > "$RUN_DIR/decisions.pid"
        echo "DecisionsAI is running (PID: $app_pid)"
        echo "Logs: $LOG_FILE"
        echo "You can close this terminal window."
        exit 0
    fi
    sleep 1
    waited=$((waited + 1))
done

echo "Startup is taking longer than expected."
echo "Check logs: tail -f $LOG_FILE"
exit 0
