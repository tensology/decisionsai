#!/usr/bin/env bash
# Atomic single-user install, update, verification, and rollback for DecisionsAI.app.

set -euo pipefail

APP_NAME="DecisionsAI.app"
INSTALL_DIR="${DECISIONSAI_INSTALL_DIR:-$HOME/Applications}"
STATE_DIR="${DECISIONSAI_STATE_DIR:-$HOME/Library/Application Support/DecisionsAI}"
TARGET="$INSTALL_DIR/$APP_NAME"
PREVIOUS="$STATE_DIR/releases/previous/$APP_NAME"
LOCK_DIR="$STATE_DIR/install.lock"

usage() {
    printf '%s\n' \
        "Usage: installer/install.sh /path/to/DecisionsAI.app" \
        "       installer/install.sh --verify" \
        "       installer/install.sh --rollback"
}

validate_app() {
    local app="$1"
    [ -d "$app" ] || { printf 'App bundle not found: %s\n' "$app" >&2; return 1; }
    [ -f "$app/Contents/Info.plist" ] || { printf 'Invalid app bundle: missing Info.plist.\n' >&2; return 1; }
    [ -x "$app/Contents/MacOS/DecisionsAI" ] || { printf 'Invalid app bundle: missing executable.\n' >&2; return 1; }
    local identifier
    identifier="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist" 2>/dev/null || true)"
    [ "$identifier" = "com.tensology.decisionsai" ] || {
        printf 'Invalid bundle identifier: %s\n' "${identifier:-missing}" >&2
        return 1
    }
}

acquire_lock() {
    mkdir -p "$STATE_DIR"
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        local owner=""
        [ -f "$LOCK_DIR/pid" ] && owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
        if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
            rm -rf "$LOCK_DIR"
            mkdir "$LOCK_DIR"
        else
            printf 'Another DecisionsAI install or rollback is active.\n' >&2
            exit 1
        fi
    fi
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM
}

verify_target() {
    validate_app "$TARGET"
    /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$TARGET/Contents/Info.plist"
}

case "${1:-}" in
    --verify)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        verify_target
        exit 0
        ;;
    --rollback)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        acquire_lock
        validate_app "$TARGET"
        validate_app "$PREVIOUS"
        SWAP="$INSTALL_DIR/.DecisionsAI.rollback.$$"
        mv "$TARGET" "$SWAP"
        if mv "$PREVIOUS" "$TARGET"; then
            mkdir -p "$(dirname "$PREVIOUS")"
            mv "$SWAP" "$PREVIOUS"
        else
            mv "$SWAP" "$TARGET"
            printf 'Rollback failed; current installation was restored.\n' >&2
            exit 1
        fi
        printf 'Rolled back DecisionsAI to version %s.\n' "$(verify_target)"
        exit 0
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    "")
        usage >&2
        exit 2
        ;;
esac

[ "$#" -eq 1 ] || { usage >&2; exit 2; }
SOURCE="$1"
validate_app "$SOURCE"
acquire_lock
mkdir -p "$INSTALL_DIR" "$(dirname "$PREVIOUS")"
STAGE="$INSTALL_DIR/.DecisionsAI.stage.$$"
BACKUP_STAGE="$STATE_DIR/releases/.previous.$$"
rm -rf "$STAGE" "$BACKUP_STAGE"
trap 'rm -rf "$LOCK_DIR" "$STAGE" "$BACKUP_STAGE"' EXIT INT TERM
ditto "$SOURCE" "$STAGE"
validate_app "$STAGE"

if [ -e "$TARGET" ]; then
    validate_app "$TARGET"
    mv "$TARGET" "$BACKUP_STAGE"
fi
if ! mv "$STAGE" "$TARGET"; then
    [ -e "$BACKUP_STAGE" ] && mv "$BACKUP_STAGE" "$TARGET"
    printf 'Installation failed; previous installation was restored.\n' >&2
    exit 1
fi
if [ -e "$BACKUP_STAGE" ]; then
    rm -rf "$PREVIOUS"
    mv "$BACKUP_STAGE" "$PREVIOUS"
fi

printf 'Installed DecisionsAI version %s at %s.\n' "$(verify_target)" "$TARGET"
