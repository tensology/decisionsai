#!/bin/bash

# Script to create a DMG file for DecisionsAI
# This script should be run from the installer/ directory

APP_NAME="DecisionsAI"
DMG_NAME="${APP_NAME}.dmg"
INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VOLUME_NAME="${APP_NAME}"
TEMP_DMG="temp_${DMG_NAME}"

# Clean up any existing DMG
rm -f "${INSTALLER_DIR}/${DMG_NAME}"
rm -f "${INSTALLER_DIR}/${TEMP_DMG}"

# Create a temporary directory for the DMG contents
TEMP_DIR=$(mktemp -d)
echo "Creating temporary directory: ${TEMP_DIR}"

# Copy project files to temp directory (excluding unnecessary files)
echo "Copying project files..."
cd "$PROJECT_ROOT"
rsync -av --exclude='.git' \
          --exclude='__pycache__' \
          --exclude='*.pyc' \
          --exclude='.DS_Store' \
          --exclude='*.log' \
          --exclude='recordings' \
          --exclude='assets/tmp' \
          --exclude='db/logs' \
          --exclude='playground' \
          --exclude='tests' \
          --exclude='distr/assets' \
          --exclude='node_modules' \
          --exclude='.venv' \
          --exclude='venv' \
          --exclude='env' \
          --exclude='installer' \
          . "${TEMP_DIR}/${APP_NAME}/"

# Create a README for the DMG
cat > "${TEMP_DIR}/README.txt" << 'EOF'
DecisionsAI Installation

1. Copy the DecisionsAI folder to your Applications folder or desired location
2. Open Terminal and navigate to the DecisionsAI folder
3. Install dependencies: pip install -r requirements.txt
4. Run setup: python bin/setup.py
5. Start the application: python bin/start.py

For more information, see README.md in the DecisionsAI folder.
EOF

# Create the DMG
echo "Creating DMG file..."
cd "$INSTALLER_DIR"
hdiutil create -volname "${VOLUME_NAME}" -srcfolder "${TEMP_DIR}" -ov -format UDZO "${TEMP_DMG}"

# Convert to final DMG with better compression
echo "Finalizing DMG..."
hdiutil convert "${TEMP_DMG}" -format UDZO -o "${DMG_NAME}"

# Clean up
rm -f "${TEMP_DMG}"
rm -rf "${TEMP_DIR}"

echo "DMG created successfully: ${INSTALLER_DIR}/${DMG_NAME}"

