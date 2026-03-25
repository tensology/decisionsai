#!/bin/bash
# Stop DecisionsAI running in the background

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$PROJECT_ROOT/decisionsai.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Stopping DecisionsAI (PID: $PID)..."
        kill "$PID"
        sleep 2

        # Force kill if still running
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "Force stopping DecisionsAI..."
            kill -9 "$PID"
        fi

        rm "$PID_FILE"
        echo "DecisionsAI stopped successfully"
    else
        echo "DecisionsAI is not running (stale PID file)"
        rm "$PID_FILE"
    fi
else
    echo "No PID file found. DecisionsAI may not be running."

    # Try to find and kill the process anyway
    PIDS=$(pgrep -f "python.*bin/start.py")
    if [ -n "$PIDS" ]; then
        echo "Found DecisionsAI process(es): $PIDS"
        echo "Killing process(es)..."
        kill $PIDS
        echo "Done"
    else
        echo "No DecisionsAI process found"
    fi
fi
