#!/bin/bash

# Build DecisionsAI as a self-contained macOS .app bundle and package it in a DMG
# This script should be run from the installer/ directory

APP_NAME="DecisionsAI"
APP_BUNDLE="${APP_NAME}.app"
DMG_NAME="${APP_NAME}.dmg"

# Get the project root directory (parent of installer/)
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check for SwitchAudioSource (macOS only, for audio device management)
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! command -v SwitchAudioSource &> /dev/null; then
        echo "⚠️  Warning: SwitchAudioSource not found."
        echo "   Audio device management features will be limited."
        echo "   Install with: brew install switchaudio-osx"
        echo ""
    else
        echo "✓ SwitchAudioSource found"
    fi
fi

# Clean up previous builds
echo "Cleaning up previous builds..."
rm -rf "${INSTALLER_DIR}/${APP_BUNDLE}"
rm -rf "${INSTALLER_DIR}/build"
rm -rf "${INSTALLER_DIR}/dist"
rm -f "${INSTALLER_DIR}/${DMG_NAME}"
rm -f "${INSTALLER_DIR}/${APP_NAME}.spec"

# Install PyInstaller if not already installed
if ! command -v pyinstaller &> /dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

# Build with PyInstaller (output to installer directory)
echo "Building ${APP_NAME}.app with PyInstaller..."
cd "$PROJECT_ROOT"
pyinstaller --name="${APP_NAME}" \
    --windowed \
    --onedir \
    --workpath "${INSTALLER_DIR}/build" \
    --distpath "${INSTALLER_DIR}/dist" \
    --specpath "${INSTALLER_DIR}" \
    --add-data "assets:assets" \
    --add-data "distr:distr" \
    --add-data "db:db" \
    --add-data "README.md:." \
    --add-data "LICENSE.md:." \
    --add-data "requirements.txt:." \
    --add-data "info.plist:." \
    --add-data "bin/setup.py:bin" \
    --add-data "bin/start.py:bin" \
    --hidden-import=PyQt6.QtCore \
    --hidden-import=PyQt6.QtGui \
    --hidden-import=PyQt6.QtWidgets \
    --hidden-import=AppKit \
    --hidden-import=distr \
    --hidden-import=vosk \
    --hidden-import=ollama \
    --hidden-import=pipecat \
    --hidden-import=langchain \
    --hidden-import=langchain_community \
    --hidden-import=litellm \
    --hidden-import=torch \
    --hidden-import=torchaudio \
    --hidden-import=transformers \
    --hidden-import=numpy \
    --hidden-import=scipy \
    --hidden-import=sounddevice \
    --hidden-import=soundfile \
    --hidden-import=kokoro_onnx \
    --hidden-import=pywhispercpp \
    --hidden-import=pynput \
    --hidden-import=pyautogui \
    --hidden-import=sqlalchemy \
    --hidden-import=beautifulsoup4 \
    --hidden-import=lxml \
    --hidden-import=elevenlabs \
    --hidden-import=resampy \
    --hidden-import=syntok \
    --hidden-import=colorama \
    --exclude-module=tkinter \
    --exclude-module=matplotlib \
    --exclude-module=pandas \
    --icon "${PROJECT_ROOT}/decisions.app/Contents/Resources/icon.icns" \
    --osx-bundle-identifier=com.tensology.decisionsai \
    bin/start.py

# Check if build was successful
if [ ! -d "${INSTALLER_DIR}/dist/${APP_BUNDLE}" ]; then
    echo "❌ Error: App bundle not created. Trying alternative method..."
    
    # Alternative: Create app bundle manually
    echo "Creating app bundle manually..."
    mkdir -p "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/MacOS"
    mkdir -p "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/Resources"
    
    # Create Info.plist
    cat > "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/Info.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>DecisionsAI</string>
    <key>CFBundleIdentifier</key>
    <string>com.tensology.decisionsai</string>
    <key>CFBundleName</key>
    <string>DecisionsAI</string>
    <key>CFBundleDisplayName</key>
    <string>DecisionsAI</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>LSUIElement</key>
    <string>1</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright © 2024 Tensology (Pty) Ltd</string>
</dict>
</plist>
EOF
    
    # Create launcher
    cat > "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/MacOS/DecisionsAI" << 'LAUNCHER'
#!/bin/bash
APP_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
cd "$APP_DIR"
exec python3 bin/start.py
LAUNCHER
    
    chmod +x "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/MacOS/DecisionsAI"
    
    # Copy files (from project root)
    rsync -av --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
          --exclude='*.log' --exclude='recordings' --exclude='assets/tmp' --exclude='db/logs' \
          --exclude='playground' --exclude='tests' --exclude='distr/assets' --exclude='node_modules' \
          --exclude='.venv' --exclude='venv' --exclude='env' --exclude='*.app' --exclude='build' \
          --exclude='dist' --exclude='installer/build_app.sh' --exclude='installer/create_dmg.sh' \
          --exclude='installer/setup_app.py' "$PROJECT_ROOT/" "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/Resources/"
    
    # Ensure bin directory exists and copy setup.py and start.py
    mkdir -p "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/Resources/bin"
    cp "$PROJECT_ROOT/bin/setup.py" "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/Resources/bin/"
    cp "$PROJECT_ROOT/bin/start.py" "${INSTALLER_DIR}/${APP_BUNDLE}/Contents/Resources/bin/"
else
    # Use PyInstaller's app bundle (already in installer/dist/)
    cp -R "${INSTALLER_DIR}/dist/${APP_BUNDLE}" "${INSTALLER_DIR}/"
fi

# Create DMG
echo "Creating DMG..."
cd "$INSTALLER_DIR"
hdiutil create -volname "${APP_NAME}" -srcfolder "${APP_BUNDLE}" -ov -format UDZO "${DMG_NAME}"

# Clean up build artifacts (keep only final outputs)
echo "Cleaning up build artifacts..."
rm -rf "${INSTALLER_DIR}/build"
rm -rf "${INSTALLER_DIR}/dist"
rm -f "${INSTALLER_DIR}/${APP_NAME}.spec"

echo ""
echo "✅ Success! App bundle created at: ${INSTALLER_DIR}/${APP_BUNDLE}"
echo "✅ DMG created at: ${INSTALLER_DIR}/${DMG_NAME}"
echo ""
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! command -v SwitchAudioSource &> /dev/null; then
        echo "⚠️  Note: For full audio device management features, users should install:"
        echo "   brew install switchaudio-osx"
        echo ""
    fi
fi
echo "You can now:"
echo "1. Open ${INSTALLER_DIR}/${DMG_NAME}"
echo "2. Drag ${APP_BUNDLE} to your Applications folder"
echo "3. Double-click to run"
