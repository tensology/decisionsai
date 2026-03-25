#!/bin/bash
# DecisionsAI Installer
# This script sets up DecisionsAI and adds it to the system PATH

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the project root directory
INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$INSTALLER_DIR/.." && pwd)"

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     DecisionsAI Installation         ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Detect shell
if [ -n "$ZSH_VERSION" ]; then
    SHELL_RC="$HOME/.zshrc"
    SHELL_NAME="zsh"
elif [ -n "$BASH_VERSION" ]; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        SHELL_RC="$HOME/.bash_profile"
        if [ ! -f "$SHELL_RC" ] && [ -f "$HOME/.bashrc" ]; then
            SHELL_RC="$HOME/.bashrc"
        fi
    else
        SHELL_RC="$HOME/.bashrc"
    fi
    SHELL_NAME="bash"
else
    SHELL_RC="$HOME/.profile"
    SHELL_NAME="sh"
fi

echo -e "${GREEN}Step 1: Setting up PATH...${NC}"

# Check if PATH entry already exists
FOUND_IN=""
for rc_file in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    if [ -f "$rc_file" ] && grep -q "$PROJECT_ROOT" "$rc_file" 2>/dev/null; then
        FOUND_IN="$rc_file"
        break
    fi
done

if [ -n "$FOUND_IN" ]; then
    echo -e "${GREEN}✓${NC} PATH entry already exists in $FOUND_IN"
else
    echo -e "${YELLOW}Adding DecisionsAI to PATH in $SHELL_RC...${NC}"
    
    # Create the file if it doesn't exist
    if [ ! -f "$SHELL_RC" ]; then
        touch "$SHELL_RC"
        echo -e "${GREEN}✓${NC} Created $SHELL_RC"
    fi
    
    # Backup the file
    if [ -f "$SHELL_RC" ]; then
        BACKUP_FILE="$SHELL_RC.backup.$(date +%Y%m%d_%H%M%S)"
        cp "$SHELL_RC" "$BACKUP_FILE" 2>/dev/null || true
        echo -e "${GREEN}✓${NC} Created backup: $BACKUP_FILE"
    fi
    
    # Add PATH entry
    echo "" >> "$SHELL_RC"
    echo "# DecisionsAI PATH - Added by installer" >> "$SHELL_RC"
    echo "export PATH=\"\$PATH:$PROJECT_ROOT\"" >> "$SHELL_RC"
    
    echo -e "${GREEN}✓${NC} Added PATH entry to $SHELL_RC"
fi

# Add to current session PATH (for immediate use)
export PATH="$PATH:$PROJECT_ROOT"

echo ""
echo -e "${GREEN}Step 2: Running DecisionsAI setup...${NC}"
echo ""

# Run the main setup script
cd "$PROJECT_ROOT"
bash bin/decisions.sh

echo ""
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Installation Complete!            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}✓${NC} DecisionsAI has been installed and added to your PATH"
echo ""
echo "You can now run DecisionsAI from anywhere using:"
echo -e "  ${YELLOW}decisions${NC}"
echo ""
echo -e "${YELLOW}Important:${NC} To use the 'decisions' command in your current terminal, run:"
echo -e "  ${BLUE}source $SHELL_RC${NC}"
echo ""
echo "Or simply open a new terminal window."
echo ""
