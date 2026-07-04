#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<'USAGE'
Set up project coding CLIs independently of DecisionsAI startup.

Usage:
  scripts/setup_project_clis.sh [all|cursor|codex|claude|pi|cline|rtk|hermes-agent]

Tools:
  cursor        Install Cursor Agent CLI (cursor-agent)
  codex         Install OpenAI Codex CLI
  claude        Install Claude Code CLI
  pi            Install Pi coding agent
  cline         Install Cline CLI (npm global, ~/.cline)
  rtk           Install RTK token proxy and wire hooks for installed CLIs
  hermes-agent  Install Nous Hermes Agent (hermes CLI, ~/.hermes) — not DecisionsAI Orchestrator
  all           Install project CLIs + RTK (does not install hermes-agent unless added later)

Environment:
  NONINTERACTIVE=1  Run without prompts.
  CODEX_INSTALLER=chatgpt|npm|brew
                   Choose Codex install route. Default: chatgpt on macOS/Linux.
  PI_PACKAGE=@earendil-works/pi-coding-agent
                   Override Pi package. Legacy package: @mariozechner/pi-coding-agent.
  RTK_TELEMETRY_DISABLED=1
                   Skip RTK telemetry prompts during hook init.
USAGE
}

log() {
    printf '%s\n' "$*"
}

have() {
    command -v "$1" >/dev/null 2>&1
}

confirm() {
    if [ "${NONINTERACTIVE:-0}" = "1" ]; then
        return 0
    fi
    printf '%s [y/N] ' "$1"
    read -r answer
    case "${answer:-}" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

ensure_npm() {
    if have npm; then
        return 0
    fi
    log "npm is required for this install path but is not on PATH."
    log "Install Node.js first, then rerun this script."
    return 1
}

install_cursor() {
    if have cursor-agent; then
        log "Cursor Agent already installed: $(cursor-agent --version 2>/dev/null | head -1 || echo unknown)"
        return 0
    fi

    log "Cursor Agent CLI is not installed."
    log "Official Cursor install command: curl https://cursor.com/install -fsS | bash"
    if confirm "Install Cursor Agent CLI now?"; then
        curl https://cursor.com/install -fsS | bash
        hash -r 2>/dev/null || true
    fi

    if have cursor-agent; then
        log "Cursor Agent installed: $(cursor-agent --version 2>/dev/null | head -1 || echo unknown)"
    else
        log "cursor-agent is still not on PATH. Add ~/.local/bin to PATH or restart your shell."
    fi
}

install_codex() {
    if have codex; then
        log "Codex CLI already installed: $(codex --version 2>/dev/null | head -1 || echo unknown)"
        return 0
    fi

    local method="${CODEX_INSTALLER:-chatgpt}"
    if [ "$method" = "brew" ]; then
        if ! have brew; then
            log "Homebrew is not on PATH; cannot use CODEX_INSTALLER=brew."
            return 1
        fi
        log "Installing Codex CLI with Homebrew cask."
        brew install --cask codex
    elif [ "$method" = "npm" ]; then
        ensure_npm
        log "Installing Codex CLI with npm package @openai/codex."
        npm install -g @openai/codex
    else
        log "Installing Codex CLI with the official ChatGPT install script."
        curl -fsSL https://chatgpt.com/codex/install.sh | sh
    fi

    hash -r 2>/dev/null || true
    if have codex; then
        log "Codex CLI installed: $(codex --version 2>/dev/null | head -1 || echo unknown)"
    else
        log "codex is still not on PATH. Restart your shell or add the install directory to PATH."
    fi
}

install_claude() {
    if have claude; then
        log "Claude Code already installed: $(claude --version 2>/dev/null | head -1 || echo unknown)"
        return 0
    fi

    ensure_npm
    log "Installing Claude Code CLI with npm package @anthropic-ai/claude-code."
    npm install -g @anthropic-ai/claude-code
    hash -r 2>/dev/null || true

    if have claude; then
        log "Claude Code installed: $(claude --version 2>/dev/null | head -1 || echo unknown)"
    else
        log "claude is still not on PATH. Restart your shell or check npm's global bin directory."
    fi
}

install_pi() {
    if have pi; then
        log "Pi coding agent already installed: $(pi --version 2>/dev/null | head -1 || echo unknown)"
        return 0
    fi

    ensure_npm
    local package_name="${PI_PACKAGE:-@earendil-works/pi-coding-agent}"
    log "Installing Pi coding agent with npm package $package_name."
    npm install -g "$package_name"
    hash -r 2>/dev/null || true

    if have pi; then
        log "Pi coding agent installed: $(pi --version 2>/dev/null | head -1 || echo unknown)"
    else
        log "pi is still not on PATH. Restart your shell or check npm's global bin directory."
    fi
}

install_cline() {
    if have cline; then
        log "Cline CLI already installed: $(cline version 2>/dev/null | head -1 || echo unknown)"
        return 0
    fi

    log "Installing Cline CLI with npm package cline."
    npm install -g cline
    hash -r 2>/dev/null || true

    if have cline; then
        log "Cline CLI installed. Run: cline auth"
    else
        log "cline is still not on PATH. Restart your shell or check npm's global bin directory."
    fi
}

install_hermes_agent() {
    if have hermes; then
        log "Nous Hermes Agent already installed: $(hermes --version 2>/dev/null | head -1 || echo unknown)"
        return 0
    fi

    log "Installing Nous Hermes Agent (external operator CLI; separate from DecisionsAI Orchestrator)."
    log "Official install: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
    if confirm "Install Nous Hermes Agent now?"; then
        curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
        hash -r 2>/dev/null || true
    fi

    if have hermes; then
        log "Hermes Agent installed. Run: hermes setup"
        log "Docs: docs/nous-hermes-agent.md in the DecisionsAI repo."
    else
        log "hermes is still not on PATH. Restart your shell or check ~/.local/bin."
    fi
}

install_rtk() {
    if have rtk; then
        log "RTK already installed: $(rtk --version 2>/dev/null | head -1 || echo unknown)"
        return 0
    fi

    if have brew; then
        log "Installing RTK with Homebrew."
        if brew install rtk; then
            hash -r 2>/dev/null || true
        fi
    fi

    if ! have rtk; then
        log "Installing RTK with the official install script."
        curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh
        hash -r 2>/dev/null || true
    fi

    if have rtk; then
        log "RTK installed: $(rtk --version 2>/dev/null | head -1 || echo unknown)"
    else
        log "rtk is still not on PATH. Add ~/.local/bin to PATH or restart your shell."
    fi
}

init_rtk_agent_hooks() {
    if ! have rtk; then
        log "RTK not installed; skipping agent hook init."
        return 0
    fi

    export RTK_TELEMETRY_DISABLED="${RTK_TELEMETRY_DISABLED:-1}"
    local auto_flags=(--auto-patch)
    local claude_flags=(--auto-patch)
    if [ "${NONINTERACTIVE:-0}" = "1" ]; then
        claude_flags=(--hook-only)
    fi

    log "Configuring RTK hooks for installed coding agents."
    if have claude || [ -d "$HOME/.claude" ]; then
        rtk init -g "${claude_flags[@]}" || log "RTK Claude hook init skipped."
    fi
    if have codex || [ -d "$HOME/.codex" ] || [ -d "$HOME/.agents" ]; then
        rtk init -g --codex || log "RTK Codex hook init skipped."
    fi
    if have cursor-agent || have cursor || [ -d "$HOME/.cursor" ]; then
        rtk init -g --agent cursor "${auto_flags[@]}" || log "RTK Cursor hook init skipped."
    fi
    if have pi; then
        rtk init -g --agent pi "${auto_flags[@]}" || log "RTK Pi hook init skipped."
    fi
    if have hermes || [ -d "$HOME/.hermes" ]; then
        rtk init -g --agent hermes "${auto_flags[@]}" || log "RTK Nous Hermes Agent hook init skipped."
    fi
    if have cline || [ -d "$HOME/.cline" ]; then
        rtk init -g --agent cline "${auto_flags[@]}" || log "RTK Cline hook init skipped."
    fi
}

setup_plugins() {
    if [ -f "$ROOT_DIR/scripts/verify_agent_harness_setup.py" ]; then
        python3 "$ROOT_DIR/scripts/verify_agent_harness_setup.py" --root "$ROOT_DIR" --quiet || true
        return 0
    fi

    if [ -x "$ROOT_DIR/plugins/cursor-ide/scripts/install_local.py" ] || [ -f "$ROOT_DIR/plugins/cursor-ide/scripts/install_local.py" ]; then
        if have cursor || [ -d "$HOME/.cursor" ]; then
            python3 "$ROOT_DIR/plugins/cursor-ide/scripts/install_local.py" || true
        fi
    fi

    if [ -x "$ROOT_DIR/plugins/codex-ide/scripts/install_local.py" ] || [ -f "$ROOT_DIR/plugins/codex-ide/scripts/install_local.py" ]; then
        if have codex || [ -d "$HOME/.codex" ] || [ -d "$HOME/.agents" ]; then
            python3 "$ROOT_DIR/plugins/codex-ide/scripts/install_local.py" || true
        fi
    fi
}

target="${1:-all}"
case "$target" in
    -h|--help|help)
        usage
        exit 0
        ;;
    all)
        install_cursor
        install_codex
        install_claude
        install_pi
        install_rtk
        init_rtk_agent_hooks
        setup_plugins
        ;;
    cursor)
        install_cursor
        install_rtk
        init_rtk_agent_hooks
        setup_plugins
        ;;
    codex)
        install_codex
        install_rtk
        init_rtk_agent_hooks
        setup_plugins
        ;;
    claude)
        install_claude
        install_rtk
        init_rtk_agent_hooks
        setup_plugins
        ;;
    pi)
        install_pi
        install_rtk
        init_rtk_agent_hooks
        ;;
    cline)
        install_cline
        install_rtk
        init_rtk_agent_hooks
        ;;
    rtk)
        install_rtk
        init_rtk_agent_hooks
        ;;
    hermes-agent)
        install_hermes_agent
        install_rtk
        init_rtk_agent_hooks
        ;;
    *)
        usage
        exit 2
        ;;
esac
