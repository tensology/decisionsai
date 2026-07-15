#!/usr/bin/env bash
# Package an already-built DecisionsAI.app. Never packages a source checkout.

set -euo pipefail

if [ "$#" -ne 2 ]; then
    printf 'Usage: %s /path/to/DecisionsAI.app /path/to/DecisionsAI-version.dmg\n' "$0" >&2
    exit 2
fi

APP_PATH="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
DMG_PATH="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
[ "$(uname -s)" = "Darwin" ] || { printf 'macOS is required to create a DMG.\n' >&2; exit 1; }
[ -d "$APP_PATH" ] || { printf 'App bundle not found: %s\n' "$APP_PATH" >&2; exit 1; }
[ -f "$APP_PATH/Contents/Info.plist" ] || { printf 'Invalid app bundle: missing Info.plist.\n' >&2; exit 1; }
[ -x "$APP_PATH/Contents/MacOS/DecisionsAI" ] || { printf 'Invalid app bundle: missing executable.\n' >&2; exit 1; }
command -v hdiutil >/dev/null 2>&1 || { printf 'hdiutil is required.\n' >&2; exit 1; }

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/decisionsai-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP_PATH" "$STAGE/DecisionsAI.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG_PATH"
hdiutil create -quiet -volname DecisionsAI -srcfolder "$STAGE" -ov -format UDZO "$DMG_PATH"
printf 'Created %s\n' "$DMG_PATH"
