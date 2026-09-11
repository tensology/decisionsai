#!/bin/bash
# Shared sidecar helpers for DecisionsAI launch scripts.

_decisions_sidecar_listening() {
    if command -v lsof &>/dev/null; then
        lsof -iTCP:11435 -sTCP:LISTEN -t &>/dev/null
        return $?
    fi
    return 1
}

_decisions_sign_sidecar() {
    local sidecar_bin="$1"
    local identity="${DECISIONSAI_CODESIGN_IDENTITY:--}"
    local identifier="net.decisionsai.sidecar"

    if ! command -v codesign &>/dev/null; then
        return 0
    fi

    # A real Apple Development or Developer ID identity gives TCC a stable
    # designated requirement across builds. Ad-hoc signing is a development
    # fallback only; rebuilding it changes the cdhash and requires re-approval.
    codesign --force --identifier "$identifier" --sign "$identity" "$sidecar_bin" &>/dev/null || true
}

decisions_start_sidecar() {
    local script_dir="$1"
    local sidecar_bin="$script_dir/sidecar/dist/decisionsai-sidecar"
    local sidecar_pid=""

    if [[ "$OSTYPE" == darwin* ]] && ! command -v cliclick &>/dev/null && command -v brew &>/dev/null; then
        echo "Installing cliclick (mouse control)..."
        brew install cliclick 2>/dev/null || echo "cliclick install failed — mouse clicks may use python3 fallback"
    fi

    if [ ! -f "$sidecar_bin" ]; then
        if command -v go &>/dev/null && [ -d "$script_dir/sidecar" ]; then
            echo "Building sidecar..."
            mkdir -p "$script_dir/sidecar/dist"
            (cd "$script_dir/sidecar" && go mod tidy && go build -ldflags="-s -w" -o dist/decisionsai-sidecar . 2>/dev/null) || \
                echo "Sidecar build failed — accessibility tree tools unavailable"
            if [ -f "$sidecar_bin" ] && command -v codesign &>/dev/null; then
                _decisions_sign_sidecar "$sidecar_bin"
            fi
        fi
    fi

    if [ -f "$sidecar_bin" ] && command -v codesign &>/dev/null; then
        if ! codesign -dv "$sidecar_bin" &>/dev/null; then
            _decisions_sign_sidecar "$sidecar_bin"
        fi
    fi

    if [ ! -f "$sidecar_bin" ]; then
        return 0
    fi

    mkdir -p "$HOME/.decisions/logs"
    if _decisions_sidecar_listening; then
        echo "Sidecar already running (HTTP port: 11435)"
        return 0
    fi

    nohup "$sidecar_bin" --local >> "$HOME/.decisions/logs/sidecar.log" 2>&1 &
    sidecar_pid=$!
    disown "$sidecar_pid" 2>/dev/null || disown
    echo "Sidecar started (PID: $sidecar_pid, HTTP port: 11435)"
}

decisions_stop_sidecar() {
    if _decisions_sidecar_listening && command -v lsof &>/dev/null; then
        local sidecar_pid=""
        sidecar_pid=$(lsof -iTCP:11435 -sTCP:LISTEN -t 2>/dev/null | head -n 1)
        if [ -n "$sidecar_pid" ]; then
            kill "$sidecar_pid" 2>/dev/null
            wait "$sidecar_pid" 2>/dev/null
        fi
    fi
}

# When executed directly (not sourced), start the sidecar for the given project root.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
    decisions_start_sidecar "$ROOT"
fi
