#!/bin/bash

# Cross-platform setup and run script for DecisionsAI
# Works on Unix, Linux, and macOS

# Don't use set -e here as we want to handle errors gracefully

# Get the project root directory (parent of bin/)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ── Integration endpoint safety defaults ──────────────────────────────────────
# Prevent accidental localhost:8090 collisions with unrelated local dev servers.
# You can still override these explicitly in your shell before launching.
if [ -z "${DEBUG:-}" ]; then
    export DEBUG="FALSE"
fi
if [ -z "${DECISIONSAI_WA_API_BASE:-}" ]; then
    export DECISIONSAI_WA_API_BASE="https://www.decisionsai.net/api/whatsapp"
fi
if [ -z "${DECISIONSAI_WA_WS_URL:-}" ]; then
    export DECISIONSAI_WA_WS_URL="wss://www.decisionsai.net/ws/whatsapp"
fi
if [ -z "${DECISIONSAI_WS_URL:-}" ]; then
    export DECISIONSAI_WS_URL="wss://www.decisionsai.net/ws/telegram"
fi

echo -e "${GREEN}DecisionsAI Setup & Run${NC}"
echo "================================"

# Consolidate legacy home dir (~/.decisionsai) into canonical (~/.decisions)
migrate_legacy_decisions_home() {
    local legacy_dir="$HOME/.decisionsai"
    local canonical_dir="$HOME/.decisions"
    if [ ! -d "$legacy_dir" ]; then
        return 0
    fi

    mkdir -p "$canonical_dir"
    "$PYTHON_CMD" - <<'PY'
from pathlib import Path
import shutil

legacy = Path.home() / ".decisionsai"
canonical = Path.home() / ".decisions"
if not legacy.exists():
    raise SystemExit(0)

for src in sorted(legacy.rglob("*"), key=lambda p: len(p.parts)):
    rel = src.relative_to(legacy)
    dst = canonical / rel
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        # Canonical tree wins; keep legacy copy for now.
        continue
    shutil.move(str(src), str(dst))

for d in sorted([p for p in legacy.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
    try:
        d.rmdir()
    except OSError:
        pass
try:
    legacy.rmdir()
except OSError:
    pass
PY
}

# Check for repository updates
echo -e "${YELLOW}Checking for updates...${NC}"
if [ -d ".git" ]; then
    # Fetch latest changes from remote
    if git fetch origin 2>/dev/null; then
        # Get current branch name
        CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

        # Compare local and remote
        LOCAL=$(git rev-parse @ 2>/dev/null)
        REMOTE=$(git rev-parse @{u} 2>/dev/null)

        if [ "$LOCAL" != "$REMOTE" ]; then
            # Check if there are uncommitted changes
            if git diff-index --quiet HEAD -- 2>/dev/null; then
                echo -e "${YELLOW}Updates available. Pulling latest changes...${NC}"
                if git pull origin "$CURRENT_BRANCH" 2>/dev/null; then
                    echo -e "${GREEN}✓${NC} Repository updated successfully"
                else
                    echo -e "${YELLOW}Warning: Could not pull updates. Continuing with current version...${NC}"
                fi
            else
                echo -e "${YELLOW}Warning: Local changes detected. Skipping auto-update.${NC}"
                echo -e "${YELLOW}To update manually: commit or stash your changes, then run 'git pull'${NC}"
            fi
        else
            echo -e "${GREEN}✓${NC} Repository is up to date"
        fi
    else
        echo -e "${YELLOW}Warning: Could not check for updates (no remote or network issue)${NC}"
    fi
else
    echo -e "${YELLOW}Not a git repository. Skipping update check.${NC}"
fi

# Show version
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null)
GIT_DATE=$(git log -1 --format=%ci 2>/dev/null)
if [ -n "$GIT_HASH" ]; then
    echo -e "\033[36mVersion: ${GIT_HASH} (${GIT_DATE})\033[0m"
fi
echo ""

# Require python3.12 - PyTorch and PyQt6 need 3.12 (3.13+ not supported)
# IMPORTANT: On macOS, prefer the python.org framework build over Homebrew.
# PyTorch wheels on PyPI are compiled against the python.org framework which
# exports _PyDict_GetItemRef. Homebrew's build does NOT export this symbol,
# causing every torch version to fail with "symbol not found in flat namespace".
PYTHON_CMD=""

if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS: prefer python.org framework build (required for PyTorch)
    FRAMEWORK_PYTHON="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
    if [ -x "$FRAMEWORK_PYTHON" ]; then
        # Verify it's 3.12.8+ (has _PyDict_GetItemRef backport)
        FRAMEWORK_VER=$("$FRAMEWORK_PYTHON" -c "import sys; print(f'{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null)
        FRAMEWORK_MICRO=$(echo "$FRAMEWORK_VER" | cut -d. -f2)
        if [ "$FRAMEWORK_MICRO" -ge 8 ] 2>/dev/null; then
            PYTHON_CMD="$FRAMEWORK_PYTHON"
            echo -e "${GREEN}✓${NC} Python found (framework): $($PYTHON_CMD --version)"
        else
            echo -e "${YELLOW}python.org Python 3.12 found but version too old ($FRAMEWORK_VER). Need 3.12.8+${NC}"
            echo -e "${YELLOW}Download from: https://www.python.org/downloads/release/python-31210/${NC}"
        fi
    fi

    # Fallback: try Homebrew python3.12 (may not work with torch)
    if [ -z "$PYTHON_CMD" ] && command -v python3.12 &> /dev/null; then
        PYTHON_CMD="python3.12"
        echo -e "${YELLOW}⚠${NC}  Using Homebrew Python ($($PYTHON_CMD --version))"
        echo -e "${YELLOW}   PyTorch/F5-TTS may not work. For full support, install python.org Python 3.12.10+:${NC}"
        echo -e "${YELLOW}   https://www.python.org/downloads/release/python-31210/${NC}"
    fi

    # Not found at all — install via Homebrew as last resort
    if [ -z "$PYTHON_CMD" ]; then
        echo -e "${YELLOW}python3.12 not found. Installing via Homebrew...${NC}"
        if command -v brew &> /dev/null; then
            brew install python@3.12
            if [ -d "/opt/homebrew/bin" ]; then
                export PATH="/opt/homebrew/bin:/opt/homebrew/opt/python@3.12/bin:$PATH"
            fi
            hash -r 2>/dev/null
            if command -v python3.12 &> /dev/null; then
                PYTHON_CMD="python3.12"
                echo -e "${GREEN}✓${NC} Python installed: $($PYTHON_CMD --version)"
                echo -e "${YELLOW}⚠${NC}  Homebrew Python installed. PyTorch may not work."
                echo -e "${YELLOW}   For full support, install python.org Python 3.12.10+:${NC}"
                echo -e "${YELLOW}   https://www.python.org/downloads/release/python-31210/${NC}"
            fi
        else
            echo -e "${RED}Error: Homebrew not found. Install Python 3.12.10+ from:${NC}"
            echo "  https://www.python.org/downloads/release/python-31210/"
            exit 1
        fi
    fi
else
    # Linux/other: use python3.12 from PATH
    if command -v python3.12 &> /dev/null; then
        PYTHON_CMD="python3.12"
        echo -e "${GREEN}✓${NC} Python found: $(python3.12 --version)"
    else
        echo -e "${RED}Error: python3.12 not found. Please install Python 3.12:${NC}"
        echo "  # Use pyenv: curl https://pyenv.run | bash && pyenv install 3.12.10"
        echo "  # Or download from: https://www.python.org/downloads/"
        exit 1
    fi
fi

if [ -z "$PYTHON_CMD" ]; then
    echo -e "${RED}Error: Could not find or install Python 3.12. Exiting.${NC}"
    exit 1
fi

migrate_legacy_decisions_home

# Idempotently repair local Codex/Cursor/Claude harness surfaces when present.
# This must never block Decisions startup or complain when those tools are absent.
if [ -f "$SCRIPT_DIR/scripts/verify_agent_harness_setup.py" ]; then
    "$PYTHON_CMD" "$SCRIPT_DIR/scripts/verify_agent_harness_setup.py" --root "$SCRIPT_DIR" --quiet >/dev/null 2>&1 || true
fi

# Check and install system dependencies
echo -e "${YELLOW}Checking system dependencies...${NC}"

check_command() {
    command -v "$1" &> /dev/null
}

install_system_deps() {
    local OS_TYPE=""
    
    # Detect OS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS_TYPE="macos"
    elif [[ -f /etc/debian_version ]]; then
        OS_TYPE="debian"
    elif [[ -f /etc/redhat-release ]]; then
        OS_TYPE="redhat"
    elif [[ -f /etc/arch-release ]]; then
        OS_TYPE="arch"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS_TYPE="linux"
    fi
    
    local NEED_INSTALL=false
    
    # Check for portaudio (via pkg-config or library)
    if ! pkg-config --exists portaudio-2.0 2>/dev/null; then
        # Try alternative check methods
        if [[ "$OS_TYPE" == "macos" ]]; then
            if ! brew list portaudio &>/dev/null 2>&1; then
                NEED_INSTALL=true
            fi
        elif [[ "$OS_TYPE" == "linux"* ]] || [[ "$OS_TYPE" == "debian" ]] || [[ "$OS_TYPE" == "redhat" ]]; then
            if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
                NEED_INSTALL=true
            fi
        else
            # Assume it might be missing, but don't fail
            NEED_INSTALL=true
        fi
    fi
    
    # Check for ffmpeg
    if ! check_command ffmpeg; then
        NEED_INSTALL=true
    fi
    
    # Check for zlib (required for librosa/llvmlite)
    if [[ "$OS_TYPE" == "macos" ]]; then
        if ! brew list zlib &>/dev/null 2>&1; then
            NEED_INSTALL=true
        fi
    elif [[ "$OS_TYPE" == "linux"* ]] || [[ "$OS_TYPE" == "debian" ]] || [[ "$OS_TYPE" == "redhat" ]]; then
        if ! ldconfig -p 2>/dev/null | grep -q libz; then
            NEED_INSTALL=true
        fi
    fi
    
    # Check for audio device management tools
    if [[ "$OS_TYPE" == "macos" ]]; then
        # macOS: SwitchAudioSource
        if ! check_command SwitchAudioSource; then
            NEED_INSTALL=true
        fi
    elif [[ "$OS_TYPE" == "linux"* ]] || [[ "$OS_TYPE" == "debian" ]] || [[ "$OS_TYPE" == "redhat" ]]; then
        # Linux/Unix: PulseAudio (pactl)
        if ! check_command pactl; then
            NEED_INSTALL=true
        fi
    fi
    
    if [ "$NEED_INSTALL" = true ]; then
        echo -e "${YELLOW}System dependencies missing. Installing...${NC}"
        
        case "$OS_TYPE" in
            macos)
                if check_command brew; then
                    echo "Installing via Homebrew..."
                    # Check what needs to be installed
                    INSTALL_LIST="portaudio ffmpeg zlib"
                    if ! check_command SwitchAudioSource; then
                        INSTALL_LIST="$INSTALL_LIST switchaudio-osx"
                    fi
                    if ! brew install $INSTALL_LIST; then
                        echo -e "${YELLOW}Warning: Failed to install some dependencies. Continuing anyway...${NC}"
                    else
                        if [[ "$INSTALL_LIST" == *"switchaudio-osx"* ]]; then
                            echo -e "${GREEN}✓${NC} SwitchAudioSource installed (required for audio device management)"
                        fi
                    fi
                else
                    echo -e "${YELLOW}Homebrew not found. Please install Homebrew first:${NC}"
                    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
                    echo "Then run: brew install portaudio ffmpeg zlib switchaudio-osx"
                    echo -e "${YELLOW}Continuing without system dependencies (may cause issues)...${NC}"
                fi
                ;;
            debian)
                echo "Installing via apt-get (requires sudo)..."
                INSTALL_LIST="portaudio19-dev ffmpeg"
                if ! check_command pactl; then
                    INSTALL_LIST="$INSTALL_LIST pulseaudio-utils"
                fi
                if ! sudo apt-get update || ! sudo apt-get install -y $INSTALL_LIST; then
                    echo -e "${YELLOW}Warning: Failed to install dependencies. You may need to run:${NC}"
                    echo "  sudo apt-get update && sudo apt-get install -y portaudio19-dev ffmpeg pulseaudio-utils"
                    echo -e "${YELLOW}Continuing anyway...${NC}"
                else
                    if [[ "$INSTALL_LIST" == *"pulseaudio-utils"* ]]; then
                        echo -e "${GREEN}✓${NC} PulseAudio utils installed (required for audio device management)"
                    fi
                fi
                ;;
            redhat)
                if check_command dnf; then
                    echo "Installing via dnf (requires sudo)..."
                    INSTALL_LIST="portaudio-devel ffmpeg"
                    if ! check_command pactl; then
                        INSTALL_LIST="$INSTALL_LIST pulseaudio-utils"
                    fi
                    if ! sudo dnf install -y $INSTALL_LIST; then
                        echo -e "${YELLOW}Warning: Failed to install dependencies. Continuing anyway...${NC}"
                    else
                        if [[ "$INSTALL_LIST" == *"pulseaudio-utils"* ]]; then
                            echo -e "${GREEN}✓${NC} PulseAudio utils installed (required for audio device management)"
                        fi
                    fi
                elif check_command yum; then
                    echo "Installing via yum (requires sudo)..."
                    INSTALL_LIST="portaudio-devel ffmpeg"
                    if ! check_command pactl; then
                        INSTALL_LIST="$INSTALL_LIST pulseaudio-utils"
                    fi
                    if ! sudo yum install -y $INSTALL_LIST; then
                        echo -e "${YELLOW}Warning: Failed to install dependencies. Continuing anyway...${NC}"
                    else
                        if [[ "$INSTALL_LIST" == *"pulseaudio-utils"* ]]; then
                            echo -e "${GREEN}✓${NC} PulseAudio utils installed (required for audio device management)"
                        fi
                    fi
                else
                    echo -e "${YELLOW}No package manager found. Please install portaudio, ffmpeg, and pulseaudio-utils manually.${NC}"
                    echo -e "${YELLOW}Continuing without system dependencies (may cause issues)...${NC}"
                fi
                ;;
            arch)
                echo "Installing via pacman (requires sudo)..."
                INSTALL_LIST="portaudio ffmpeg"
                if ! check_command pactl; then
                    INSTALL_LIST="$INSTALL_LIST pulseaudio"
                fi
                if ! sudo pacman -S --noconfirm $INSTALL_LIST; then
                    echo -e "${YELLOW}Warning: Failed to install dependencies. Continuing anyway...${NC}"
                else
                    if [[ "$INSTALL_LIST" == *"pulseaudio"* ]]; then
                        echo -e "${GREEN}✓${NC} PulseAudio installed (required for audio device management)"
                    fi
                fi
                ;;
            *)
                echo -e "${YELLOW}Unknown OS. Please install portaudio and ffmpeg manually.${NC}"
                echo "Visit: https://ffmpeg.org/download.html and http://www.portaudio.com/download.html"
                echo -e "${YELLOW}Continuing without system dependencies (may cause issues)...${NC}"
                ;;
        esac
    else
        echo -e "${GREEN}✓${NC} System dependencies already installed"
        # Double-check audio device management tools
        if [[ "$OS_TYPE" == "macos" ]] && ! check_command SwitchAudioSource; then
            echo -e "${YELLOW}Note: SwitchAudioSource not found. Audio device management features will be limited.${NC}"
            echo -e "${YELLOW}Install with: brew install switchaudio-osx${NC}"
        elif [[ "$OS_TYPE" == "linux"* ]] || [[ "$OS_TYPE" == "debian" ]] || [[ "$OS_TYPE" == "redhat" ]]; then
            if ! check_command pactl; then
                echo -e "${YELLOW}Note: pactl (PulseAudio) not found. Audio device management features will be limited.${NC}"
                echo -e "${YELLOW}Install with: sudo apt-get install pulseaudio-utils (Debian/Ubuntu) or equivalent${NC}"
            fi
        fi
    fi
}

install_system_deps

# Virtual environment - always use ~/.virtualenvs/decisions with python3.12
VENV_DIR="$HOME/.virtualenvs/decisions"

if [ -d "$VENV_DIR" ]; then
    # Verify the existing venv uses python3.12
    VENV_PYTHON_VERSION=$("$VENV_DIR/bin/python" --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    if [ "$VENV_PYTHON_VERSION" != "3.12" ]; then
        echo -e "${YELLOW}Existing venv uses Python $VENV_PYTHON_VERSION, recreating with 3.12...${NC}"
        rm -rf "$VENV_DIR"
        mkdir -p "$HOME/.virtualenvs"
        $PYTHON_CMD -m venv "$VENV_DIR"
        echo -e "${GREEN}✓${NC} Virtual environment recreated at $VENV_DIR"
    else
        echo -e "${GREEN}✓${NC} Using existing virtual environment at $VENV_DIR (Python $VENV_PYTHON_VERSION)"
    fi
else
    echo -e "${YELLOW}Creating virtual environment at $VENV_DIR...${NC}"
    mkdir -p "$HOME/.virtualenvs"
    $PYTHON_CMD -m venv "$VENV_DIR"
    echo -e "${GREEN}✓${NC} Virtual environment created at $VENV_DIR"
fi

# Activate virtual environment
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
    echo -e "${GREEN}✓${NC} Virtual environment activated"
else
    echo -e "${RED}Error: Could not activate virtual environment at $VENV_DIR${NC}"
    exit 1
fi

# Check pip version (skip upgrade to avoid hanging)
echo -e "${YELLOW}Checking pip...${NC}"
PIP_VERSION=$("$VENV_DIR/bin/pip" --version 2>/dev/null | awk '{print $2}' || echo "unknown")
if [ "$PIP_VERSION" != "unknown" ]; then
    echo -e "${GREEN}✓${NC} pip version: $PIP_VERSION"
else
    echo -e "${YELLOW}Warning: Could not determine pip version${NC}"
fi

# Install requirements if not already installed
# Use a marker file in the installer folder to track installation
REQUIREMENTS_MARKER="installer/.requirements_installed_external"

# Verify that all critical dependencies are actually installed.
# Uses a single Python process (bin/check_deps.py) instead of 37+ subprocesses
# to avoid macOS jetsam (SIGKILL) under memory pressure causing false "missing"
# package reports.
check_dependencies_installed() {
    local dep_check_output
    # Only stdout lists missing critical packages (one per line). stderr is suppressed so
    # import-time warnings (e.g. kanade_tokenizer FlashAttention) do not look like missing deps.
    dep_check_output=$("$VENV_DIR/bin/python" "$SCRIPT_DIR/bin/check_deps.py" 2>/dev/null)
    local exit_code=$?
    # Exit 137 = 128+9 = SIGKILL (macOS jetsam/OOM). Don't treat as missing deps.
    if [ "$exit_code" -eq 137 ]; then
        echo -e "${YELLOW}Dependency check was killed (SIGKILL). Assuming OK and continuing.${NC}" >&2
        return 0
    fi
    if [ "$exit_code" -ne 0 ]; then
        echo -e "${YELLOW}Missing dependencies detected:${NC}" >&2
        echo "$dep_check_output" | while read -r pkg; do
            [ -n "$pkg" ] && echo -e "${YELLOW}  - $pkg${NC}" >&2
        done
        local missing_count
        missing_count=$(echo "$dep_check_output" | grep -c . || echo 0)
        echo -e "${YELLOW}Found $missing_count missing package(s)${NC}" >&2
        return 1
    fi
    return 0
}

if [ ! -f "$REQUIREMENTS_MARKER" ] || ! check_dependencies_installed; then
    if [ ! -f "$REQUIREMENTS_MARKER" ]; then
        echo -e "${YELLOW}Installing dependencies...${NC}"
    else
        echo -e "${YELLOW}Dependencies appear incomplete. Reinstalling...${NC}"
        rm -f "$REQUIREMENTS_MARKER"
    fi
    
    "$VENV_DIR/bin/pip" install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: pip install failed. Please check the output above.${NC}"
        exit 1
    fi
    
    # Install PyObjC-Cocoa on macOS if not already installed (needed for AppKit)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        "$VENV_DIR/bin/python" -c "import AppKit" 2>/dev/null || {
            echo -e "${YELLOW}Installing PyObjC-Cocoa for macOS...${NC}"
            "$VENV_DIR/bin/pip" install pyobjc-framework-Cocoa
        }
    fi
    
    # Ensure installer directory exists
    mkdir -p installer
    touch "$REQUIREMENTS_MARKER"
    echo -e "${GREEN}✓${NC} Dependencies installed"

    # Fix llvmlite and numba library paths on macOS (required for Kokoro TTS)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo -e "${YELLOW}Fixing library paths for macOS (llvmlite, numba)...${NC}"
        FIXED_COUNT=0

        # Fix llvmlite — @rpath/libc++.1.dylib and @rpath/libz.1.dylib
        LLVMLITE_LIB=$("$VENV_DIR/bin/python" -c "import llvmlite, os; print(os.path.join(os.path.dirname(llvmlite.__file__), 'binding', 'libllvmlite.dylib'))" 2>/dev/null)
        if [ -f "$LLVMLITE_LIB" ]; then
            LLVM_CHANGED=false
            if otool -L "$LLVMLITE_LIB" 2>/dev/null | grep -q "@rpath/libc++.1.dylib"; then
                install_name_tool -change @rpath/libc++.1.dylib /usr/lib/libc++.1.dylib "$LLVMLITE_LIB" 2>/dev/null && LLVM_CHANGED=true
            fi
            if otool -L "$LLVMLITE_LIB" 2>/dev/null | grep -q "@rpath/libz.1.dylib"; then
                # Resolve libz from Homebrew or system
                LIBZ_PATH=""
                if [ -f "/opt/homebrew/lib/libz.1.dylib" ]; then
                    LIBZ_PATH="/opt/homebrew/lib/libz.1.dylib"
                elif [ -f "/usr/local/lib/libz.1.dylib" ]; then
                    LIBZ_PATH="/usr/local/lib/libz.1.dylib"
                elif [ -f "/usr/lib/libz.1.dylib" ]; then
                    LIBZ_PATH="/usr/lib/libz.1.dylib"
                fi
                if [ -n "$LIBZ_PATH" ]; then
                    install_name_tool -change @rpath/libz.1.dylib "$LIBZ_PATH" "$LLVMLITE_LIB" 2>/dev/null && LLVM_CHANGED=true
                fi
            fi
            if [ "$LLVM_CHANGED" = true ]; then
                codesign --force --sign - "$LLVMLITE_LIB" 2>/dev/null
                FIXED_COUNT=$((FIXED_COUNT + 1))
            fi
        fi

        # Fix numba .so files
        NUMBA_DIR=$("$VENV_DIR/bin/python" -c "import numba, os; print(os.path.dirname(numba.__file__))" 2>/dev/null)
        if [ -d "$NUMBA_DIR" ]; then
            while IFS= read -r so_file; do
                if [ -f "$so_file" ] && otool -L "$so_file" 2>/dev/null | grep -q "@rpath/libc++.1.dylib"; then
                    if install_name_tool -change @rpath/libc++.1.dylib /usr/lib/libc++.1.dylib "$so_file" 2>/dev/null; then
                        codesign --force --sign - "$so_file" 2>/dev/null
                        FIXED_COUNT=$((FIXED_COUNT + 1))
                    fi
                fi
            done < <(find "$NUMBA_DIR" -name "*.so" -type f 2>/dev/null)
        fi

        if [ "$FIXED_COUNT" -gt 0 ]; then
            echo -e "${GREEN}✓${NC} Fixed $FIXED_COUNT library path(s) for Kokoro TTS"
        else
            echo -e "${GREEN}✓${NC} Library paths already correct"
        fi
    fi
else
    # Check if PyObjC-Cocoa is installed on macOS (might have been missed)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        "$VENV_DIR/bin/python" -c "import AppKit" 2>/dev/null || {
            echo -e "${YELLOW}Installing PyObjC-Cocoa for macOS...${NC}"
            "$VENV_DIR/bin/pip" install pyobjc-framework-Cocoa
        }

        # Also check/fix llvmlite and numba library paths (required for Kokoro TTS)
        FIXED_COUNT=0

        # Fix llvmlite — @rpath/libc++.1.dylib and @rpath/libz.1.dylib
        LLVMLITE_LIB=$("$VENV_DIR/bin/python" -c "import llvmlite, os; print(os.path.join(os.path.dirname(llvmlite.__file__), 'binding', 'libllvmlite.dylib'))" 2>/dev/null)
        if [ -f "$LLVMLITE_LIB" ]; then
            LLVM_CHANGED=false
            if otool -L "$LLVMLITE_LIB" 2>/dev/null | grep -q "@rpath/libc++.1.dylib"; then
                install_name_tool -change @rpath/libc++.1.dylib /usr/lib/libc++.1.dylib "$LLVMLITE_LIB" 2>/dev/null && LLVM_CHANGED=true
            fi
            if otool -L "$LLVMLITE_LIB" 2>/dev/null | grep -q "@rpath/libz.1.dylib"; then
                LIBZ_PATH=""
                if [ -f "/opt/homebrew/lib/libz.1.dylib" ]; then
                    LIBZ_PATH="/opt/homebrew/lib/libz.1.dylib"
                elif [ -f "/usr/local/lib/libz.1.dylib" ]; then
                    LIBZ_PATH="/usr/local/lib/libz.1.dylib"
                elif [ -f "/usr/lib/libz.1.dylib" ]; then
                    LIBZ_PATH="/usr/lib/libz.1.dylib"
                fi
                if [ -n "$LIBZ_PATH" ]; then
                    install_name_tool -change @rpath/libz.1.dylib "$LIBZ_PATH" "$LLVMLITE_LIB" 2>/dev/null && LLVM_CHANGED=true
                fi
            fi
            if [ "$LLVM_CHANGED" = true ]; then
                codesign --force --sign - "$LLVMLITE_LIB" 2>/dev/null
                FIXED_COUNT=$((FIXED_COUNT + 1))
            fi
        fi

        # Fix numba .so files
        NUMBA_DIR=$("$VENV_DIR/bin/python" -c "import numba, os; print(os.path.dirname(numba.__file__))" 2>/dev/null)
        if [ -d "$NUMBA_DIR" ]; then
            while IFS= read -r so_file; do
                if [ -f "$so_file" ] && otool -L "$so_file" 2>/dev/null | grep -q "@rpath/libc++.1.dylib"; then
                    if install_name_tool -change @rpath/libc++.1.dylib /usr/lib/libc++.1.dylib "$so_file" 2>/dev/null; then
                        codesign --force --sign - "$so_file" 2>/dev/null
                        FIXED_COUNT=$((FIXED_COUNT + 1))
                    fi
                fi
            done < <(find "$NUMBA_DIR" -name "*.so" -type f 2>/dev/null)
        fi

        if [ "$FIXED_COUNT" -gt 0 ]; then
            echo -e "${YELLOW}Fixing library paths for macOS...${NC}"
            echo -e "${GREEN}✓${NC} Fixed $FIXED_COUNT library path(s) for Kokoro TTS"
        fi
    fi
    echo -e "${GREEN}✓${NC} Dependencies already installed"
fi

# Local STT/TTS caches (Vosk dir, Whisper gguf warm) — same idea as Whisper’s lazy download, but up front.
# Opt out: DECISIONS_AI_SKIP_MODEL_PREFETCH=1 ./bin/decisions.sh
prefetch_local_models_bootstrap() {
    if [ "${DECISIONS_AI_SKIP_MODEL_PREFETCH:-}" = "1" ]; then
        echo -e "${YELLOW}Skipping local model prefetch (DECISIONS_AI_SKIP_MODEL_PREFETCH=1).${NC}"
        return 0
    fi
    echo -e "${YELLOW}Prefetching local STT/TTS model caches (Vosk, Whisper)...${NC}"
    if ! "$VENV_DIR/bin/python" "$SCRIPT_DIR/scripts/prefetch_local_models.py" --only all \
        2> >(grep -vE '^objc\[[0-9]+\]: Class AVF(Frame|Audio)Receiver is implemented in both ' >&2); then
        echo -e "${YELLOW}⚠ Local model prefetch had errors — app will still start; first STT/TTS use may download.${NC}"
    else
        echo -e "${GREEN}✓${NC} Local model prefetch finished"
    fi
}
prefetch_local_models_bootstrap

# On macOS ARM (Sequoia+), the kernel's code signing monitor (codeSigningMonitor:2)
# rejects pages from universal (fat) binaries even when codesign --verify passes.
# Fix: thin fat binaries to arm64-only, then ad-hoc re-sign everything.
RESIGN_MARKER="installer/.extensions_signed_v2"
if [[ "$OSTYPE" == "darwin"* ]] && [ ! -f "$RESIGN_MARKER" ]; then
    ARCH=$(uname -m)
    echo -e "${YELLOW}Fixing native extensions for macOS code signing (one-time)...${NC}"
    THIN_COUNT=0
    RESIGN_COUNT=0

    # Step 1: Thin universal/fat binaries to native arch only
    if [ "$ARCH" = "arm64" ]; then
        while IFS= read -r fat_file; do
            if file "$fat_file" 2>/dev/null | grep -q "universal binary"; then
                if lipo -thin arm64 -output "${fat_file}.arm64" "$fat_file" 2>/dev/null; then
                    mv "${fat_file}.arm64" "$fat_file"
                    THIN_COUNT=$((THIN_COUNT + 1))
                else
                    rm -f "${fat_file}.arm64" 2>/dev/null
                fi
            fi
        done < <(find "$VENV_DIR/lib" \( -name "*.so" -o -name "*.dylib" \) -type f 2>/dev/null)
    fi

    # Step 2: Ad-hoc re-sign all native extensions
    while IFS= read -r lib_file; do
        if codesign --force --sign - "$lib_file" 2>/dev/null; then
            RESIGN_COUNT=$((RESIGN_COUNT + 1))
        fi
    done < <(find "$VENV_DIR/lib" \( -name "*.so" -o -name "*.dylib" \) -type f 2>/dev/null)

    mkdir -p installer
    touch "$RESIGN_MARKER"
    if [ "$THIN_COUNT" -gt 0 ]; then
        echo -e "${GREEN}✓${NC} Thinned $THIN_COUNT universal binary(ies) to $ARCH"
    fi
    echo -e "${GREEN}✓${NC} Re-signed $RESIGN_COUNT native extension(s)"
fi

# Check if models are installed
MODELS_DIR="./distr/core/agent/models"
KOKORO_MODEL="$MODELS_DIR/kokoro-v1.0.onnx"
KOKORO_VOICES="$MODELS_DIR/voices-v1.0.bin"

MODELS_EXIST=true

if [ ! -f "$KOKORO_MODEL" ] || [ ! -f "$KOKORO_VOICES" ]; then
    MODELS_EXIST=false
fi

# Run setup.py if models don't exist
if [ "$MODELS_EXIST" = false ]; then
    echo -e "${YELLOW}Models not found. Running setup...${NC}"
    "$VENV_DIR/bin/python" bin/setup.py
    echo -e "${GREEN}✓${NC} Setup complete"
else
    echo -e "${GREEN}✓${NC} Models already installed"
fi

# Install Playwright browser (chromium only) if not already present
PLAYWRIGHT_CHROMIUM=$("$VENV_DIR/bin/python" -c "from playwright._impl._driver import compute_driver_executable; import os; print(os.path.exists(os.path.join(os.path.dirname(compute_driver_executable()), '.local-browsers')))" 2>/dev/null || echo "False")
if [ "$PLAYWRIGHT_CHROMIUM" != "True" ]; then
    echo -e "${YELLOW}Installing Playwright Chromium browser...${NC}"
    "$VENV_DIR/bin/python" -m playwright install chromium 2>/dev/null && \
        echo -e "${GREEN}✓${NC} Playwright Chromium installed" || \
        echo -e "${YELLOW}⚠ Playwright install failed. Run manually: python -m playwright install chromium${NC}"
else
    echo -e "${GREEN}✓${NC} Playwright Chromium already installed"
fi

# Check for Ollama availability (no automatic model pulls)
check_ollama() {
    if command -v ollama &> /dev/null; then
        echo -e "${GREEN}✓${NC} Ollama found"
        echo -e "Default conversational model: deepseek-v4-pro:cloud"
        echo -e "${GREEN}✓${NC} Skipping automatic local Ollama model download"
        echo -e "${YELLOW}Optional local models can be installed manually if needed${NC}"
    else
        echo -e "${YELLOW}Note: Ollama not found. For local LLM support, install Ollama:${NC}"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo "  brew install ollama"
        else
            echo "  curl -fsSL https://ollama.com/install.sh | sh"
        fi
        echo -e "${YELLOW}No local model is required for the default cloud setup.${NC}"
    fi
}

check_ollama

# Check for NumPy/PyTorch compatibility issues
check_numpy_torch_compatibility() {
    NUMPY_VERSION=$("$VENV_DIR/bin/python" -c "import numpy; print(numpy.__version__)" 2>/dev/null)
    TORCH_VERSION=$("$VENV_DIR/bin/python" -c "import torch; print(torch.__version__.split('+')[0])" 2>/dev/null)
    
    # Check if numpy 2.x with old torch (pre-2.5)
    if [[ "$NUMPY_VERSION" == 2.* ]]; then
        # Extract major.minor from torch version
        TORCH_MAJOR=$(echo "$TORCH_VERSION" | cut -d. -f1)
        TORCH_MINOR=$(echo "$TORCH_VERSION" | cut -d. -f2)
        
        # Torch < 2.5 was compiled for NumPy 1.x
        if [[ "$TORCH_MAJOR" -lt 2 ]] || [[ "$TORCH_MAJOR" -eq 2 && "$TORCH_MINOR" -lt 5 ]]; then
            echo -e "${YELLOW}⚠️  NumPy/PyTorch version mismatch detected${NC}"
            echo -e "${YELLOW}   NumPy $NUMPY_VERSION requires PyTorch 2.5+, but found $TORCH_VERSION${NC}"
            echo -e "${YELLOW}   Upgrading PyTorch...${NC}"
            
            "$VENV_DIR/bin/pip" install "torch>=2.5.0" "torchaudio>=2.5.0" --quiet
            
            NEW_TORCH=$("$VENV_DIR/bin/python" -c "import torch; print(torch.__version__)" 2>/dev/null)
            echo -e "${GREEN}✓${NC} PyTorch upgraded: $TORCH_VERSION -> $NEW_TORCH"
        fi
    fi
}

check_numpy_torch_compatibility

# Verify PyTorch actually loads (catches _PyDict_GetItemRef symbol issue)
echo -e "${YELLOW}Verifying PyTorch...${NC}"
TORCH_OK=$("$VENV_DIR/bin/python" -c "
try:
    import torch
    print(f'ok {torch.__version__}')
except ImportError as e:
    if '_PyDict_GetItemRef' in str(e):
        print('symbol_error')
    else:
        print('missing')
except Exception:
    print('error')
" 2>/dev/null || echo "error")

case "$TORCH_OK" in
    ok*)
        TORCH_VER=$(echo "$TORCH_OK" | cut -d' ' -f2)
        echo -e "${GREEN}✓${NC} PyTorch $TORCH_VER loaded successfully"
        ;;
    symbol_error)
        echo -e "${YELLOW}⚠${NC}  PyTorch cannot load (_PyDict_GetItemRef symbol missing)"
        echo -e "${YELLOW}   F5-TTS and sentence-transformers will be unavailable.${NC}"
        echo -e "${YELLOW}   Tool retrieval will use TF-IDF fallback (still works, less accurate).${NC}"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo -e "${YELLOW}   FIX: Install python.org Python 3.12.10+:${NC}"
            echo -e "${YELLOW}   https://www.python.org/downloads/release/python-31210/${NC}"
            echo -e "${YELLOW}   Then: rm -rf ~/.virtualenvs/decisions && ./bin/decisions.sh${NC}"
        fi
        ;;
    missing)
        echo -e "${YELLOW}⚠${NC}  PyTorch not installed"
        ;;
    *)
        echo -e "${YELLOW}⚠${NC}  PyTorch verification failed"
        ;;
esac

# Clean up Python cache files to avoid stale bytecode issues
echo -e "${YELLOW}Cleaning Python cache...${NC}"
find "$SCRIPT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find "$SCRIPT_DIR" -type f -name "*.pyc" -delete 2>/dev/null
echo -e "${GREEN}✓${NC} Cache cleaned"

# Setup PATH and create symlink for 'decisions' command (silently if already set up)
# Check if decisions command already exists and works
DECISIONS_EXISTS=false
if command -v decisions >/dev/null 2>&1; then
    # Check if it's our symlink
    DECISIONS_CMD=$(command -v decisions)
    if [ -L "$DECISIONS_CMD" ] && [ "$(readlink "$DECISIONS_CMD")" = "$SCRIPT_DIR/decisions" ]; then
        DECISIONS_EXISTS=true
    elif [ "$DECISIONS_CMD" = "$SCRIPT_DIR/decisions" ]; then
        DECISIONS_EXISTS=true
    fi
fi

# Only show output if setup is needed
if [ "$DECISIONS_EXISTS" = false ]; then
    echo -e "${YELLOW}Setting up PATH and 'decisions' command...${NC}"
fi

# Determine where to create the symlink
# Prefer /usr/local/bin (standard location, requires sudo)
# Fallback to ~/.local/bin (user directory, no sudo needed)
SYMLINK_DIR=""
if [ -w "/usr/local/bin" ]; then
    SYMLINK_DIR="/usr/local/bin"
elif [ -d "$HOME/.local/bin" ] || mkdir -p "$HOME/.local/bin" 2>/dev/null; then
    SYMLINK_DIR="$HOME/.local/bin"
    # Add ~/.local/bin to PATH if not already there
    if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
        export PATH="$PATH:$HOME/.local/bin"
    fi
else
    # Last resort: use project directory in PATH
    SYMLINK_DIR=""
fi

# Create symlink if we have a location
if [ -n "$SYMLINK_DIR" ]; then
    SYMLINK_PATH="$SYMLINK_DIR/decisions"
    DECISIONS_SCRIPT="$SCRIPT_DIR/decisions"
    
    # Remove existing symlink if it points to wrong location
    if [ -L "$SYMLINK_PATH" ] && [ "$(readlink "$SYMLINK_PATH")" != "$DECISIONS_SCRIPT" ]; then
        rm "$SYMLINK_PATH" 2>/dev/null || true
    fi
    
    # Create symlink if it doesn't exist
    if [ ! -e "$SYMLINK_PATH" ]; then
        if ln -s "$DECISIONS_SCRIPT" "$SYMLINK_PATH" 2>/dev/null; then
            [ "$DECISIONS_EXISTS" = false ] && echo -e "${GREEN}✓${NC} Created 'decisions' command in $SYMLINK_DIR"
        else
            # If symlink creation failed (permissions), try with sudo
            if sudo ln -sf "$DECISIONS_SCRIPT" "$SYMLINK_PATH" 2>/dev/null; then
                [ "$DECISIONS_EXISTS" = false ] && echo -e "${GREEN}✓${NC} Created 'decisions' command in $SYMLINK_DIR (with sudo)"
            else
                [ "$DECISIONS_EXISTS" = false ] && echo -e "${YELLOW}⚠${NC}  Could not create symlink. Will use PATH method instead."
                SYMLINK_DIR=""
            fi
        fi
    fi
fi

# Also set up PATH in shell RC files (for project directory access) - only if needed
if [ "$DECISIONS_EXISTS" = false ] && ([ -z "$SYMLINK_DIR" ] || [ "$SYMLINK_DIR" = "$HOME/.local/bin" ]); then
    # Only need PATH setup if we're using ~/.local/bin or project directory
    bash "$SCRIPT_DIR/bin/setup_path.sh" 2>/dev/null || {
        # If setup_path.sh fails, try direct setup
        SHELL_RC=""
        if [ -n "$ZSH_VERSION" ]; then
            SHELL_RC="$HOME/.zshrc"
        elif [ -n "$BASH_VERSION" ]; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                SHELL_RC="$HOME/.bash_profile"
                [ ! -f "$SHELL_RC" ] && [ -f "$HOME/.bashrc" ] && SHELL_RC="$HOME/.bashrc"
            else
                SHELL_RC="$HOME/.bashrc"
            fi
        else
            SHELL_RC="$HOME/.profile"
        fi
        
        # Add ~/.local/bin to PATH if we're using it
        if [ "$SYMLINK_DIR" = "$HOME/.local/bin" ] && [ -n "$SHELL_RC" ] && ! grep -q "$HOME/.local/bin" "$SHELL_RC" 2>/dev/null; then
            echo "" >> "$SHELL_RC"
            echo "# DecisionsAI - Add ~/.local/bin to PATH" >> "$SHELL_RC"
            echo "export PATH=\"\$PATH:$HOME/.local/bin\"" >> "$SHELL_RC"
            [ "$DECISIONS_EXISTS" = false ] && echo -e "${GREEN}✓${NC} Added ~/.local/bin to PATH in $SHELL_RC"
        fi
        
        # Also add project directory to PATH as fallback
        if [ -n "$SHELL_RC" ] && ! grep -q "$SCRIPT_DIR" "$SHELL_RC" 2>/dev/null; then
            echo "" >> "$SHELL_RC"
            echo "# DecisionsAI PATH" >> "$SHELL_RC"
            echo "export PATH=\"\$PATH:$SCRIPT_DIR\"" >> "$SHELL_RC"
            [ "$DECISIONS_EXISTS" = false ] && echo -e "${GREEN}✓${NC} Added PATH entry to $SHELL_RC"
        fi
    }
    [ "$DECISIONS_EXISTS" = false ] && echo ""
fi

# Check and install the local DecisionsAI Cursor plugin when Cursor is present.
check_cursor_plugin_setup() {
    local cursor_plugin_source="$SCRIPT_DIR/cursor_plugin/decisions-cursor"
    local cursor_plugin_target="$HOME/.cursor/plugins/local/decisions-cursor"

    if [ ! -d "$cursor_plugin_source" ]; then
        return 0
    fi

    if ! command -v cursor >/dev/null 2>&1 && [ ! -d "$HOME/.cursor" ]; then
        echo -e "${YELLOW}Note: Cursor not detected. Skipping DecisionsAI Cursor plugin setup.${NC}"
        return 0
    fi

    if [ -f "$cursor_plugin_target/.cursor-plugin/plugin.json" ]; then
        echo -e "${GREEN}✓${NC} DecisionsAI Cursor plugin already installed"
        return 0
    fi

    echo -e "${YELLOW}Installing DecisionsAI Cursor plugin...${NC}"
    if "$VENV_DIR/bin/python" "$cursor_plugin_source/scripts/install_local.py" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} DecisionsAI Cursor plugin installed"
        echo -e "${YELLOW}Reload Cursor once so it picks up the local plugin.${NC}"
    else
        echo -e "${YELLOW}⚠${NC}  Could not install DecisionsAI Cursor plugin automatically."
        echo -e "${YELLOW}   Run: python3 cursor_plugin/decisions-cursor/scripts/install_local.py${NC}"
    fi
}

# Check and register the local DecisionsAI Codex plugin when Codex is present.
check_codex_plugin_setup() {
    local codex_plugin_source="$SCRIPT_DIR/codex_plugin/decisions-codex"
    local codex_plugin_target="$HOME/plugins/decisions-codex"

    if [ ! -d "$codex_plugin_source" ]; then
        return 0
    fi

    if ! command -v codex >/dev/null 2>&1 && [ ! -d "$HOME/.codex" ] && [ ! -d "$HOME/.agents" ]; then
        echo -e "${YELLOW}Note: Codex not detected. Skipping DecisionsAI Codex plugin setup.${NC}"
        return 0
    fi

    if [ -f "$codex_plugin_target/.codex-plugin/plugin.json" ]; then
        echo -e "${GREEN}✓${NC} DecisionsAI Codex plugin already installed"
        return 0
    fi

    echo -e "${YELLOW}Installing DecisionsAI Codex plugin...${NC}"
    if "$VENV_DIR/bin/python" "$codex_plugin_source/scripts/install_local.py" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} DecisionsAI Codex plugin installed"
        echo -e "${YELLOW}Reload Codex once so it picks up the local plugin.${NC}"
    else
        echo -e "${YELLOW}⚠${NC}  Could not install DecisionsAI Codex plugin automatically."
        echo -e "${YELLOW}   Run: python3 codex_plugin/decisions-codex/scripts/install_local.py${NC}"
    fi
}

check_project_cli_presence() {
    echo -e "${YELLOW}Checking project coding tools...${NC}"

    if command -v cursor >/dev/null 2>&1 || [ -d "$HOME/.cursor" ]; then
        echo -e "${GREEN}✓${NC} Cursor environment detected"
        check_cursor_plugin_setup
    else
        echo -e "${YELLOW}Note: Cursor IDE is not detected. Cursor plugin setup skipped.${NC}"
    fi

    if command -v codex >/dev/null 2>&1 || [ -d "$HOME/.codex" ] || [ -d "$HOME/.agents" ]; then
        echo -e "${GREEN}✓${NC} Codex environment detected"
        check_codex_plugin_setup
    else
        echo -e "${YELLOW}Note: Codex is not detected. Codex plugin setup skipped.${NC}"
    fi

    if command -v cursor-agent >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Cursor CLI found ($(cursor-agent --version 2>/dev/null | head -1 || echo "version unknown"))"
    else
        echo -e "${YELLOW}Note: cursor-agent is not on PATH; Cursor dispatch will use the IDE/plugin surface first.${NC}"
    fi

    if command -v codex >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Codex CLI found ($(codex --version 2>/dev/null | head -1 || echo "version unknown"))"
    else
        echo -e "${YELLOW}Note: codex is not on PATH; install it separately if CLI fallback is needed.${NC}"
    fi

    if command -v claude >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Claude Code found ($(claude --version 2>/dev/null | head -1 || echo "version unknown"))"
    else
        echo -e "${YELLOW}Note: claude is not on PATH; Claude Code project routing is unavailable.${NC}"
    fi

    if command -v pi >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Pi coding agent found ($(pi --version 2>/dev/null | head -1 || echo "version unknown"))"
    else
        echo -e "${YELLOW}Note: pi is not on PATH; Pi project routing is unavailable.${NC}"
    fi

    echo -e "${YELLOW}To install or update project CLIs, run: scripts/setup_project_clis.sh${NC}"
}

check_project_cli_presence

# Check EULA acceptance before boot
check_eula_acceptance() {
    local eula_state=""
    eula_state=$("$VENV_DIR/bin/python" - <<'PY'
from distr.core.utils import load_settings_from_db
settings = load_settings_from_db() or {}
print("1" if bool(settings.get("accepted_eula")) else "0")
PY
)

    if [ "$eula_state" = "1" ]; then
        echo -e "${GREEN}✓${NC} EULA already accepted"
        return 0
    fi

    echo ""
    echo -e "${YELLOW}EULA acceptance is required before DecisionsAI can start.${NC}"
    echo -e "${YELLOW}Please review LICENSE.md in the project root if you have not read it yet.${NC}"
    echo ""
    printf "Do you agree to the EULA? (yes/no): "
    read -r eula_answer
    eula_answer=$(printf "%s" "$eula_answer" | tr '[:upper:]' '[:lower:]')

    if [ "$eula_answer" = "yes" ] || [ "$eula_answer" = "y" ]; then
        if "$VENV_DIR/bin/python" - <<'PY'
from distr.core.utils import load_settings_from_db, save_settings_to_db
settings = load_settings_from_db() or {}
settings["accepted_eula"] = True
save_settings_to_db(settings)
print("EULA accepted and saved.")
PY
        then
            echo -e "${GREEN}✓${NC} EULA accepted"
            return 0
        else
            echo -e "${RED}Error: Could not save EULA acceptance. Aborting startup.${NC}"
            return 1
        fi
    fi

    echo -e "${YELLOW}EULA not accepted. Startup aborted.${NC}"
    return 1
}

if ! check_eula_acceptance; then
    exit 1
fi

# ── Start sidecar (machine control agent) ────────────────────────────────────
SIDECAR_BIN="$SCRIPT_DIR/sidecar/dist/decisionsai-sidecar"
SIDECAR_PID=""

start_sidecar() {
    if [ ! -f "$SIDECAR_BIN" ]; then
        if command -v go &>/dev/null && [ -d "$SCRIPT_DIR/sidecar" ]; then
            echo -e "${YELLOW}Building sidecar...${NC}"
            mkdir -p "$SCRIPT_DIR/sidecar/dist"
            (cd "$SCRIPT_DIR/sidecar" && go mod tidy && go build -ldflags="-s -w" -o dist/decisionsai-sidecar . 2>/dev/null) && \
                echo -e "${GREEN}✓${NC} Sidecar built" || \
                echo -e "${YELLOW}⚠  Sidecar build failed — accessibility tree tools unavailable${NC}"
        fi
    fi

    if [ -f "$SIDECAR_BIN" ]; then
        mkdir -p "$HOME/.decisions/logs"
        # Run in local-only mode — no relay server needed, just the HTTP tool API
        "$SIDECAR_BIN" --local \
            > "$HOME/.decisions/logs/sidecar.log" 2>&1 &
        SIDECAR_PID=$!
        echo -e "${GREEN}✓${NC} Sidecar started (PID: $SIDECAR_PID, HTTP port: 11435)"
    fi
}

stop_sidecar() {
    if [ -n "$SIDECAR_PID" ] && kill -0 "$SIDECAR_PID" 2>/dev/null; then
        kill "$SIDECAR_PID" 2>/dev/null
        wait "$SIDECAR_PID" 2>/dev/null
    fi
}

trap stop_sidecar EXIT
mkdir -p "$HOME/.decisions/logs"
start_sidecar

# Run the application
echo ""
echo -e "${GREEN}booting up now. Please wait for the agent to speak to you...${NC}"
echo -e "${GREEN}YOU CAN NOW CLOSE THIS TERMINAL${NC}"
# Filter macOS dylib duplicate class warnings from stderr (harmless noise from cv2/av FFmpeg conflict)
"$VENV_DIR/bin/python" bin/start.py 2> >(grep -v "^objc\[" >&2)
