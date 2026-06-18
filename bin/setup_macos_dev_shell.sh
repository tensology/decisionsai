#!/bin/bash
# Idempotent macOS dev-shell fixes for Apple Silicon:
# - Re-exec Rosetta terminals as native arm64 (psycopg2 / Django venv wheels)
# - Stop mixing Intel + ARM in DYLD_LIBRARY_PATH
# - Prefer native iTerm/Cursor launches

set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MARKER_START="# >>> decisions-macos-dev-shell >>>"
MARKER_END="# <<< decisions-macos-dev-shell <<<"
BLOCK_FILE="$(mktemp)"
ZSHRC="${HOME}/.zshrc"

if [[ "${OSTYPE:-}" != darwin* ]]; then
    exit 0
fi

if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" != "1" ]; then
    exit 0
fi

cat >"$BLOCK_FILE" <<'EOF'
# >>> decisions-macos-dev-shell >>>
# Apple Silicon: Rosetta terminals run Python as x86_64 while pip wheels are arm64.
# Skip when zsh is running a -c command (DecisionsAI startup terminals, scripts).
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ] && [ -z "${ZSH_EXECUTION_STRING:-}" ]; then
  exec arch -arm64 /bin/zsh -il
fi
# arm64 Homebrew only — /usr/local is Intel and breaks native Python extensions.
case "${DYLD_LIBRARY_PATH:-}" in
  *:/usr/local/lib*|/usr/local/lib:*|/usr/local/lib)
    export DYLD_LIBRARY_PATH="/opt/homebrew/lib"
    ;;
esac
# <<< decisions-macos-dev-shell <<<
EOF

_ensure_zshrc_block() {
    touch "$ZSHRC"
    if grep -q "$MARKER_START" "$ZSHRC" 2>/dev/null; then
        python3 - "$ZSHRC" "$BLOCK_FILE" <<'PY'
import sys
from pathlib import Path

zshrc = Path(sys.argv[1])
block = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
text = zshrc.read_text(encoding="utf-8")
start = "# >>> decisions-macos-dev-shell >>>"
end = "# <<< decisions-macos-dev-shell <<<"
if start not in text or end not in text:
    raise SystemExit(1)
pre, rest = text.split(start, 1)
_, post = rest.split(end, 1)
zshrc.write_text(pre.rstrip() + "\n\n" + block + "\n" + post.lstrip("\n"), encoding="utf-8")
PY
        echo -e "${GREEN}✓${NC} Refreshed macOS dev-shell guard in $ZSHRC"
        return 0
    fi

    if grep -q 'sysctl.proc_translated' "$ZSHRC" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} macOS dev-shell guard already present in $ZSHRC"
        return 0
    fi

    cp "$ZSHRC" "${ZSHRC}.backup.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
    { cat "$BLOCK_FILE"; echo ""; cat "$ZSHRC"; } >"${ZSHRC}.decisions_tmp"
    mv "${ZSHRC}.decisions_tmp" "$ZSHRC"
    echo -e "${GREEN}✓${NC} Added macOS dev-shell guard to $ZSHRC"
}

_fix_dyld_line() {
    if grep -q 'DYLD_LIBRARY_PATH="/usr/local/lib:/opt/homebrew/lib"' "$ZSHRC" 2>/dev/null; then
        sed -i '' 's|export DYLD_LIBRARY_PATH="/usr/local/lib:/opt/homebrew/lib"|export DYLD_LIBRARY_PATH="/opt/homebrew/lib"|' "$ZSHRC"
        echo -e "${GREEN}✓${NC} Fixed mixed Intel/ARM DYLD_LIBRARY_PATH in $ZSHRC"
    fi
}

_prefer_native_terminal_apps() {
    local updated=0
    for app in iTerm Cursor; do
        local plist="/Applications/${app}.app/Contents/Info.plist"
        [ -f "$plist" ] || continue
        if /usr/libexec/PlistBuddy -c "Print :LSRequiresNativeExecution" "$plist" &>/dev/null; then
            /usr/libexec/PlistBuddy -c "Set :LSRequiresNativeExecution true" "$plist" 2>/dev/null && updated=1
        else
            /usr/libexec/PlistBuddy -c "Add :LSRequiresNativeExecution bool true" "$plist" 2>/dev/null && updated=1
        fi
    done
    if [ "$updated" -eq 1 ]; then
        echo -e "${GREEN}✓${NC} Set iTerm/Cursor to prefer native ARM (quit and reopen those apps)"
    fi
}

_verify() {
    local issues=0
    if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
        echo -e "${YELLOW}⚠${NC}  This shell is running under Rosetta — open a new terminal after setup"
        issues=1
    fi
    if grep -q '/usr/local/lib:/opt/homebrew/lib' "$ZSHRC" 2>/dev/null; then
        echo -e "${YELLOW}⚠${NC}  $ZSHRC still mixes Intel and ARM library paths"
        issues=1
    fi
    if [ "$issues" -eq 0 ]; then
        echo -e "${GREEN}✓${NC} macOS dev shell looks healthy (arm64 Python wheels should load)"
    fi
}

echo -e "${YELLOW}Checking macOS dev shell (arm64 / psycopg2 compatibility)...${NC}"
_ensure_zshrc_block
_fix_dyld_line
_prefer_native_terminal_apps
_verify
rm -f "$BLOCK_FILE"
