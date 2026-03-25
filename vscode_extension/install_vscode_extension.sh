#!/bin/bash

# DecisionsAI VS Code Extension Installer
# Packages and installs the extension into Cursor/VS Code

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔧 DecisionsAI Extension Installer"
echo "=================================="

# Check if npm dependencies are installed
if [ ! -d "node_modules" ]; then
    echo "📦 Installing dependencies..."
    npm install
fi

# Package the extension
echo "📦 Packaging extension..."
npm run package

# Find the generated .vsix file (get the latest one)
VSIX_FILE=$(ls -t *.vsix 2>/dev/null | head -n 1)

if [ -z "$VSIX_FILE" ]; then
    echo "❌ Error: No .vsix file found after packaging"
    exit 1
fi

echo "📄 Found: $VSIX_FILE"

# Detect which editor to install to (Cursor or VS Code)
if command -v cursor &> /dev/null; then
    EDITOR_CMD="cursor"
    EDITOR_NAME="Cursor"
elif command -v code &> /dev/null; then
    EDITOR_CMD="code"
    EDITOR_NAME="VS Code"
else
    echo "⚠️  Neither 'cursor' nor 'code' CLI found in PATH"
    echo "📄 Extension packaged at: $SCRIPT_DIR/$VSIX_FILE"
    echo ""
    echo "To install manually:"
    echo "  1. Open Cursor/VS Code"
    echo "  2. Cmd+Shift+P → 'Extensions: Install from VSIX...'"
    echo "  3. Select: $SCRIPT_DIR/$VSIX_FILE"
    exit 0
fi

# Uninstall existing version (if any)
echo "🗑️  Removing old version (if exists)..."
$EDITOR_CMD --uninstall-extension decisionsai.decisionsai 2>/dev/null || true

# Install the extension
echo "📥 Installing extension to $EDITOR_NAME..."
$EDITOR_CMD --install-extension "$SCRIPT_DIR/$VSIX_FILE"

echo ""
echo "✅ DecisionsAI extension installed successfully!"
echo ""
echo "🔄 Please reload $EDITOR_NAME to activate the extension."
echo "   (Cmd+Shift+P → 'Developer: Reload Window')"
