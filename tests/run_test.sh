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

echo -e "${GREEN}DecisionsAI Setup & Run${NC}"
echo "================================"

# Detect Python command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}Error: Python not found. Please install Python 3.8 or higher.${NC}"
    exit 1
fi

# Check Python version
PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
REQUIRED_VERSION="3.8"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo -e "${RED}Error: Python 3.8 or higher is required. Found: $PYTHON_VERSION${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Python found: $($PYTHON_CMD --version)"

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

# Check for existing virtual environments
USE_EXISTING_VENV=false
VENV_ACTIVATED=false

# Function to find existing venv with various name patterns
find_existing_venv() {
    local search_dirs=()
    
    # Add WORKON_HOME if set
    if [ -n "$WORKON_HOME" ]; then
        search_dirs+=("$WORKON_HOME")
    fi
    
    # Add standard virtualenvwrapper locations
    search_dirs+=("$HOME/.virtualenvs" "$HOME/.virtualenv")
    
    # Check for common venv names (prioritize "decisions" lowercase)
    local venv_names=("decisions" "DecisionsAI" "decisionsai" "Decisions")
    
    for dir in "${search_dirs[@]}"; do
        if [ -d "$dir" ]; then
            for name in "${venv_names[@]}"; do
                if [ -d "$dir/$name" ]; then
                    VENV_DIR="$dir/$name"
                    USE_EXISTING_VENV=true
                    echo -e "${GREEN}✓${NC} Found existing virtualenv at $VENV_DIR"
                    return 0
                fi
            done
        fi
    done
    
    return 1
}

# Prioritize virtualenvwrapper locations, then check local venv
if ! find_existing_venv; then
    # No venv found in virtualenvwrapper locations, check local
    if [ -d "venv" ]; then
        VENV_DIR="venv"
        USE_EXISTING_VENV=true
    else
        # No existing venv found, will create one
        VENV_DIR="venv"
    fi
fi

# Double-check if we found an existing venv (in case it was created between checks)
if [ "$USE_EXISTING_VENV" = false ] && [ ! -d "$VENV_DIR" ]; then
    # Try one more time to find existing venv
    if find_existing_venv; then
        USE_EXISTING_VENV=true
    fi
fi

# Create virtual environment if it doesn't exist
if [ "$USE_EXISTING_VENV" = false ] && [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    
    # Always prefer virtualenvwrapper if available
    VENV_NAME="decisions"
    
    # Check if virtualenvwrapper is available
    if command -v mkvirtualenv &> /dev/null || [ -n "$WORKON_HOME" ] || [ -d "$HOME/.virtualenvs" ]; then
        echo -e "${YELLOW}Using virtualenvwrapper. Creating 'decisions' environment with $($PYTHON_CMD --version)...${NC}"
        
        # Try to source virtualenvwrapper if not already loaded
        if ! command -v workon &> /dev/null && ! command -v mkvirtualenv &> /dev/null; then
            # Try common virtualenvwrapper locations
            if [ -f "$HOME/.virtualenvwrapper/virtualenvwrapper.sh" ]; then
                source "$HOME/.virtualenvwrapper/virtualenvwrapper.sh"
            elif [ -f "/usr/local/bin/virtualenvwrapper.sh" ]; then
                source /usr/local/bin/virtualenvwrapper.sh
            elif [ -f "/usr/share/virtualenvwrapper/virtualenvwrapper.sh" ]; then
                source /usr/share/virtualenvwrapper/virtualenvwrapper.sh
            fi
        fi
        
        if command -v mkvirtualenv &> /dev/null; then
            # Create venv using virtualenvwrapper with current Python version
            mkvirtualenv -p "$PYTHON_CMD" "$VENV_NAME"
            
            # Determine where it was created
            if [ -n "$WORKON_HOME" ]; then
                VENV_DIR="$WORKON_HOME/$VENV_NAME"
            elif [ -d "$HOME/.virtualenvs/$VENV_NAME" ]; then
                VENV_DIR="$HOME/.virtualenvs/$VENV_NAME"
            elif [ -d "$HOME/.virtualenv/$VENV_NAME" ]; then
                VENV_DIR="$HOME/.virtualenv/$VENV_NAME"
            else
                # Fallback to local venv
                echo -e "${YELLOW}Could not determine virtualenvwrapper location, using local venv...${NC}"
                $PYTHON_CMD -m venv "$VENV_DIR"
                VENV_ACTIVATED=false
            fi
            
            if [ -d "$VENV_DIR" ]; then
                VENV_ACTIVATED=true
                echo -e "${GREEN}✓${NC} Virtual environment created at $VENV_DIR"
            fi
        else
            # virtualenvwrapper not fully available, but directories exist - create manually
            if [ -n "$WORKON_HOME" ]; then
                VENV_DIR="$WORKON_HOME/$VENV_NAME"
            elif [ -d "$HOME/.virtualenvs" ]; then
                VENV_DIR="$HOME/.virtualenvs/$VENV_NAME"
            else
                VENV_DIR="venv"
            fi
            echo -e "${YELLOW}Creating venv at $VENV_DIR with $($PYTHON_CMD --version)...${NC}"
            $PYTHON_CMD -m venv "$VENV_DIR"
        fi
    else
        # No virtualenvwrapper, create local venv
        echo -e "${YELLOW}Creating local venv with $($PYTHON_CMD --version)...${NC}"
        $PYTHON_CMD -m venv "$VENV_DIR"
    fi
    
    if [ "$VENV_ACTIVATED" = false ]; then
        echo -e "${GREEN}✓${NC} Virtual environment created at $VENV_DIR"
    fi
elif [ "$USE_EXISTING_VENV" = true ]; then
    echo -e "${GREEN}✓${NC} Using existing virtual environment at $VENV_DIR"
fi

# Activate virtual environment
if [ "$VENV_ACTIVATED" = false ]; then
    # Check if this is a virtualenvwrapper venv and try workon first
    if [[ "$VENV_DIR" == *"/.virtualenvs/"* ]] || [[ "$VENV_DIR" == *"/.virtualenv/"* ]] || [ -n "$WORKON_HOME" ]; then
        # Try to source virtualenvwrapper if not already loaded
        if ! command -v workon &> /dev/null; then
            if [ -f "$HOME/.virtualenvwrapper/virtualenvwrapper.sh" ]; then
                source "$HOME/.virtualenvwrapper/virtualenvwrapper.sh"
            elif [ -f "/usr/local/bin/virtualenvwrapper.sh" ]; then
                source /usr/local/bin/virtualenvwrapper.sh
            elif [ -f "/usr/share/virtualenvwrapper/virtualenvwrapper.sh" ]; then
                source /usr/share/virtualenvwrapper/virtualenvwrapper.sh
            fi
        fi
        
        # Try workon with the venv name
        if command -v workon &> /dev/null; then
            VENV_NAME=$(basename "$VENV_DIR")
            if workon "$VENV_NAME" 2>/dev/null; then
                echo -e "${GREEN}✓${NC} Virtual environment activated via virtualenvwrapper (workon $VENV_NAME)"
                VENV_ACTIVATED=true
            fi
        fi
    fi
    
    # Fall back to direct activation if workon didn't work
    if [ "$VENV_ACTIVATED" = false ] && [ -f "$VENV_DIR/bin/activate" ]; then
        source "$VENV_DIR/bin/activate"
        echo -e "${GREEN}✓${NC} Virtual environment activated"
        VENV_ACTIVATED=true
    fi
    
    # Error if still not activated
    if [ "$VENV_ACTIVATED" = false ]; then
        echo -e "${RED}Error: Could not activate virtual environment at $VENV_DIR${NC}"
        exit 1
    fi
fi

# Check pip version (skip upgrade to avoid hanging)
echo -e "${YELLOW}Checking pip...${NC}"
PIP_VERSION=$(pip --version 2>/dev/null | awk '{print $2}' || echo "unknown")
if [ "$PIP_VERSION" != "unknown" ]; then
    echo -e "${GREEN}✓${NC} pip version: $PIP_VERSION"
else
    echo -e "${YELLOW}Warning: Could not determine pip version${NC}"
fi

# Install requirements if not already installed
# Use a marker file in the installer folder to track installation
if [ "$VENV_DIR" = "venv" ]; then
    REQUIREMENTS_MARKER="installer/.requirements_installed"
else
    REQUIREMENTS_MARKER="installer/.requirements_installed_external"
fi

# Verify that all critical dependencies are actually installed
check_dependencies_installed() {
    # Check a comprehensive list of critical packages
    local critical_packages=(
        "numpy"
        "PyQt6"
        "torch"
        "transformers"
        "langchain"
        "ollama"
        "scipy"
        "litellm"
        "vosk"
        "pipecat"
        "pywhispercpp"
        "kokoro_onnx"
        "sqlalchemy"
        "beautifulsoup4:bs4"
        "lxml"
        "elevenlabs"
        "sounddevice"
        "soundfile"
        "pynput"
        "pyautogui"
        "pyaudio"
        "fuzzywuzzy"
        "resampy"
        "syntok"
        "colorama"
    )
    
    local missing=0
    
    for pkg_spec in "${critical_packages[@]}"; do
        # Handle package:import_name format
        local pkg_name="${pkg_spec%%:*}"
        local import_name="${pkg_spec##*:}"
        [[ "$import_name" == "$pkg_name" ]] && import_name="$pkg_name"
        
        # Try to import
        if ! python -c "import $import_name" 2>/dev/null 1>/dev/null; then
            missing=$((missing + 1))
            if [ "$missing" -eq 1 ]; then
                echo -e "${YELLOW}Missing dependencies detected:${NC}" >&2
            fi
            echo -e "${YELLOW}  - $pkg_name${NC}" >&2
        fi
    done
    
    # Also check macOS-specific packages
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if ! python -c "import AppKit" 2>/dev/null 1>/dev/null; then
            missing=$((missing + 1))
            if [ "$missing" -eq 1 ]; then
                echo -e "${YELLOW}Missing dependencies detected:${NC}" >&2
            fi
            echo -e "${YELLOW}  - pyobjc-framework-Cocoa${NC}" >&2
        fi
    fi
    
    if [ "$missing" -gt 0 ]; then
        echo -e "${YELLOW}Found $missing missing package(s)${NC}" >&2
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
    
    pip install -r requirements.txt
    
    # Install PyObjC-Cocoa on macOS if not already installed (needed for AppKit)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        python -c "import AppKit" 2>/dev/null || {
            echo -e "${YELLOW}Installing PyObjC-Cocoa for macOS...${NC}"
            pip install pyobjc-framework-Cocoa
        }
    fi
    
    # Ensure installer directory exists
    mkdir -p installer
    touch "$REQUIREMENTS_MARKER"
    echo -e "${GREEN}✓${NC} Dependencies installed"
else
    # Check if PyObjC-Cocoa is installed on macOS (might have been missed)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        python -c "import AppKit" 2>/dev/null || {
            echo -e "${YELLOW}Installing PyObjC-Cocoa for macOS...${NC}"
            pip install pyobjc-framework-Cocoa
        }
    fi
    echo -e "${GREEN}✓${NC} Dependencies already installed"
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
    $PYTHON_CMD bin/setup.py
    echo -e "${GREEN}✓${NC} Setup complete"
else
    echo -e "${GREEN}✓${NC} Models already installed"
fi

# Run the application
echo ""
echo -e "${GREEN}Starting DecisionsAI...${NC}"
echo "================================"
$PYTHON_CMD tests/test_telegram_limit.py
