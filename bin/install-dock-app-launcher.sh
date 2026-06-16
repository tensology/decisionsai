#!/bin/bash
# Install the Dock .app launcher from the repo template.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$SCRIPT_DIR/bin/dock-app-launcher.sh"
APP_LAUNCHER="$SCRIPT_DIR/decisions.app/Contents/MacOS/decisions"

if [ ! -f "$TEMPLATE" ]; then
    echo "Missing dock launcher template: $TEMPLATE"
    exit 1
fi

mkdir -p "$(dirname "$APP_LAUNCHER")"
cp "$TEMPLATE" "$APP_LAUNCHER"
chmod +x "$APP_LAUNCHER"
echo "Installed dock launcher: $APP_LAUNCHER"
