#!/bin/bash
# Build decisions.app at the project root (dev launcher for Dock / Finder).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_DIR="$PROJECT_ROOT/installer/decisions-app-template"
OUTPUT_APP="$PROJECT_ROOT/decisions.app"
ICON_SOURCE_PNG="$PROJECT_ROOT/assets/img/icons/tray.png"
ICON_FALLBACK_PNG="$PROJECT_ROOT/assets/favicon.png"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [[ "$OSTYPE" != darwin* ]]; then
    echo -e "${RED}decisions.app can only be built on macOS.${NC}"
    exit 1
fi

if [ ! -d "$TEMPLATE_DIR/Contents" ]; then
    echo -e "${RED}Missing template: $TEMPLATE_DIR${NC}"
    exit 1
fi

_build_icns() {
    local png_path="$1"
    local icns_path="$2"
    local iconset_dir
    iconset_dir="$(mktemp -d)/icon.iconset"

    mkdir -p "$iconset_dir"
    sips -z 16 16 "$png_path" --out "$iconset_dir/icon_16x16.png" >/dev/null
    sips -z 32 32 "$png_path" --out "$iconset_dir/icon_16x16@2x.png" >/dev/null
    sips -z 32 32 "$png_path" --out "$iconset_dir/icon_32x32.png" >/dev/null
    sips -z 64 64 "$png_path" --out "$iconset_dir/icon_32x32@2x.png" >/dev/null
    sips -z 128 128 "$png_path" --out "$iconset_dir/icon_128x128.png" >/dev/null
    sips -z 256 256 "$png_path" --out "$iconset_dir/icon_128x128@2x.png" >/dev/null
    sips -z 256 256 "$png_path" --out "$iconset_dir/icon_256x256.png" >/dev/null
    sips -z 512 512 "$png_path" --out "$iconset_dir/icon_256x256@2x.png" >/dev/null
    sips -z 512 512 "$png_path" --out "$iconset_dir/icon_512x512.png" >/dev/null
    sips -z 1024 1024 "$png_path" --out "$iconset_dir/icon_512x512@2x.png" >/dev/null

    iconutil -c icns "$iconset_dir" -o "$icns_path"
}

echo -e "${GREEN}Building decisions.app...${NC}"

SAVED_ICNS=""
if [ -f "$OUTPUT_APP/Contents/Resources/icon.icns" ]; then
    SAVED_ICNS="$(mktemp).icns"
    cp "$OUTPUT_APP/Contents/Resources/icon.icns" "$SAVED_ICNS"
fi

rm -rf "$OUTPUT_APP"
mkdir -p "$OUTPUT_APP/Contents/MacOS" "$OUTPUT_APP/Contents/Resources"

cp "$TEMPLATE_DIR/Contents/Info.plist" "$OUTPUT_APP/Contents/Info.plist"
cp "$TEMPLATE_DIR/Contents/MacOS/decisions" "$OUTPUT_APP/Contents/MacOS/decisions"
chmod +x "$OUTPUT_APP/Contents/MacOS/decisions"

ICNS_OUT="$OUTPUT_APP/Contents/Resources/icon.icns"
if [ -n "$SAVED_ICNS" ]; then
    cp "$SAVED_ICNS" "$ICNS_OUT"
    rm -f "$SAVED_ICNS"
else
    PNG="$ICON_SOURCE_PNG"
    if [ ! -f "$PNG" ]; then
        PNG="$ICON_FALLBACK_PNG"
    fi
    if [ ! -f "$PNG" ]; then
        echo -e "${RED}No icon source PNG found (expected tray.png).${NC}"
        exit 1
    fi
    echo -e "${YELLOW}Generating icon.icns from $PNG${NC}"
    _build_icns "$PNG" "$ICNS_OUT"
fi

echo ""
echo -e "${GREEN}✓ Built: $OUTPUT_APP${NC}"
echo ""
echo "Next steps:"
echo "  1. Drag decisions.app to your Dock (once)."
echo "  2. Launch from the Dock — no Terminal window needed."
echo "  3. Re-run this script after launcher/template changes."
echo ""
echo "Logs: ~/.decisions/logs/launcher.log"
