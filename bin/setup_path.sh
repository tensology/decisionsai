#!/bin/bash
# Setup script to add DecisionsAI to system PATH and create decisions command

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the project root directory (parent of bin/)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DECISIONS_DIR="$SCRIPT_DIR"

echo -e "${GREEN}DecisionsAI PATH Setup${NC}"
echo "================================"
echo ""

# Detect shell and determine which RC file to use
# Check multiple files and use the most appropriate one
if [ -n "$ZSH_VERSION" ]; then
    SHELL_NAME="zsh"
    if [ -f "$HOME/.zshrc" ]; then
        SHELL_RC="$HOME/.zshrc"
    else
        SHELL_RC="$HOME/.zshrc"
    fi
elif [ -n "$BASH_VERSION" ]; then
    SHELL_NAME="bash"
    # macOS typically uses .bash_profile, Linux uses .bashrc
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if [ -f "$HOME/.bash_profile" ]; then
            SHELL_RC="$HOME/.bash_profile"
        elif [ -f "$HOME/.bashrc" ]; then
            SHELL_RC="$HOME/.bashrc"
        else
            SHELL_RC="$HOME/.bash_profile"
        fi
    else
        if [ -f "$HOME/.bashrc" ]; then
            SHELL_RC="$HOME/.bashrc"
        else
            SHELL_RC="$HOME/.bashrc"
        fi
    fi
else
    # Fallback to .profile
    SHELL_NAME="sh"
    if [ -f "$HOME/.profile" ]; then
        SHELL_RC="$HOME/.profile"
    else
        SHELL_RC="$HOME/.profile"
    fi
fi

# Check if PATH entry already exists in any common RC file
FOUND_IN=""
for rc_file in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    if [ -f "$rc_file" ] && grep -q "$DECISIONS_DIR" "$rc_file" 2>/dev/null; then
        FOUND_IN="$rc_file"
        break
    fi
done

PATH_ENTRY="export PATH=\"\$PATH:$DECISIONS_DIR\""

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
        cp "$SHELL_RC" "$SHELL_RC.backup.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
    fi
    
    # Add PATH entry
    echo "" >> "$SHELL_RC"
    echo "# DecisionsAI PATH - Added by setup_path.sh" >> "$SHELL_RC"
    echo "$PATH_ENTRY" >> "$SHELL_RC"
    
    echo -e "${GREEN}✓${NC} Added PATH entry to $SHELL_RC"
    echo -e "${YELLOW}Note: You may need to restart your terminal or run: source $SHELL_RC${NC}"
fi

# Add to current session PATH (for immediate use)
export PATH="$PATH:$DECISIONS_DIR"

echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "You can now run DecisionsAI from anywhere using:"
echo -e "  ${YELLOW}decisions${NC}"
echo ""
echo "The application will run in the background."
echo ""
echo -e "${YELLOW}⚠️  IMPORTANT: To use the 'decisions' command in your current terminal, run:${NC}"
echo -e "  ${BLUE}source $SHELL_RC${NC}"
echo ""
echo "Or open a new terminal window - the command will be available automatically."
echo ""
echo -e "${GREEN}To verify PATH is set, run:${NC}"
echo "  echo \$PATH | grep -q '$DECISIONS_DIR' && echo 'PATH is set!' || echo 'PATH not set - run: source $SHELL_RC'"
echo ""
