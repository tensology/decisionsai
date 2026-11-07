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

mkdir -p "$(dirname "$APP_LAUNCHER")" "$SCRIPT_DIR/decisions.app/Contents/Resources"
cp "$SCRIPT_DIR/installer/decisions-app-template/Contents/Info.plist" "$SCRIPT_DIR/decisions.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION")" "$SCRIPT_DIR/decisions.app/Contents/Info.plist"
ICONSET="$(mktemp -d)/icon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$SCRIPT_DIR/assets/icons/favicon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$SCRIPT_DIR/assets/icons/favicon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$SCRIPT_DIR/decisions.app/Contents/Resources/icon.icns"
rm -rf "$(dirname "$ICONSET")"
cp "$TEMPLATE" "$APP_LAUNCHER"
chmod +x "$APP_LAUNCHER"
echo "Installed dock launcher: $APP_LAUNCHER"
