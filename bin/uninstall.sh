#!/bin/bash

# Uninstall script for DecisionsAI
# Removes virtual environment only (for clean reinstall)

# Get the project root directory (parent of bin/)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${RED}DecisionsAI Uninstaller${NC}"
echo "================================"
echo ""
echo -e "${CYAN}This script ONLY removes the virtual environment.${NC}"
echo ""
echo -e "${GREEN}The following will NOT be removed:${NC}"
echo "  - Your database and settings"
echo "  - Your recordings and actions"
echo "  - Downloaded models (Kokoro TTS)"
echo "  - Ollama and its models"
echo "  - Project source files"
echo ""

# Parse arguments
FORCE=false

for arg in "$@"; do
    case $arg in
        --force|-f)
            FORCE=true
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --force, -f       Skip confirmation prompts"
            echo "  --help, -h        Show this help message"
            echo ""
            echo "This script removes the virtual environment so you can do a clean reinstall."
            echo "Run ./decisions after uninstalling to reinstall."
            exit 0
            ;;
    esac
done

# Confirmation prompt
if [ "$FORCE" = false ]; then
    echo -e "${YELLOW}This will remove the DecisionsAI virtual environment.${NC}"
    echo "Your data (database, recordings, settings) will NOT be removed."
    echo ""
    read -p "Are you sure you want to continue? [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Uninstall cancelled."
        exit 0
    fi
fi

echo ""

# Function to find and remove virtual environment
remove_virtualenv() {
    local venv_removed=false
    
    # Check common virtualenvwrapper locations
    local search_dirs=()
    
    if [ -n "$WORKON_HOME" ]; then
        search_dirs+=("$WORKON_HOME")
    fi
    search_dirs+=("$HOME/.virtualenvs" "$HOME/.virtualenv")
    
    local venv_names=("decisions" "DecisionsAI" "decisionsai" "Decisions")
    
    for dir in "${search_dirs[@]}"; do
        if [ -d "$dir" ]; then
            for name in "${venv_names[@]}"; do
                if [ -d "$dir/$name" ]; then
                    echo -e "${YELLOW}Removing virtual environment: $dir/$name${NC}"
                    rm -rf "$dir/$name"
                    venv_removed=true
                    echo -e "${GREEN}✓${NC} Removed $dir/$name"
                fi
            done
        fi
    done
    
    # Check local venv directories
    if [ -d "$SCRIPT_DIR/venv" ]; then
        echo -e "${YELLOW}Removing local venv: $SCRIPT_DIR/venv${NC}"
        rm -rf "$SCRIPT_DIR/venv"
        venv_removed=true
        echo -e "${GREEN}✓${NC} Removed local venv"
    fi
    
    if [ "$venv_removed" = false ]; then
        echo -e "${CYAN}No virtual environment found to remove${NC}"
    fi
}

# Remove installation markers so dependencies get reinstalled
remove_markers() {
    if [ -f "$SCRIPT_DIR/installer/.requirements_installed" ]; then
        rm -f "$SCRIPT_DIR/installer/.requirements_installed"
    fi
    if [ -f "$SCRIPT_DIR/installer/.requirements_installed_external" ]; then
        rm -f "$SCRIPT_DIR/installer/.requirements_installed_external"
    fi
}

# Execute uninstall
echo -e "${CYAN}Removing virtual environment...${NC}"
remove_virtualenv
remove_markers
echo ""

echo "================================"
echo -e "${GREEN}Uninstall complete.${NC}"
echo ""
echo "To reinstall, run: ./decisions"
