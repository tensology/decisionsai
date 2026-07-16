#!/usr/bin/env bash
# Build a deterministic DecisionsAI macOS application artifact.

set -euo pipefail

APP_NAME="DecisionsAI"
BUNDLE_ID="com.tensology.decisionsai"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION_FILE="$PROJECT_ROOT/VERSION"
ICON_FILE="$PROJECT_ROOT/assets/icons/favicon.png"
OUTPUT_DIR="${DECISIONSAI_OUTPUT_DIR:-$SCRIPT_DIR/release}"
BUILD_DIR="${DECISIONSAI_BUILD_DIR:-$SCRIPT_DIR/build}"
DIST_DIR="$BUILD_DIR/dist"
WORK_DIR="$BUILD_DIR/work"
SPEC_DIR="$BUILD_DIR/spec"
RELEASE=0
CREATE_DMG=1
PYTHON_BIN="${DECISIONSAI_PYTHON:-python3}"

usage() {
    printf '%s\n' \
        "Usage: installer/build_app.sh [--release] [--app-only]" \
        "" \
        "  --release   Require Developer ID signing and Apple notarization." \
        "  --app-only  Build the app and checksum without creating a DMG." \
        "" \
        "Environment:" \
        "  DECISIONSAI_OUTPUT_DIR          Artifact output directory." \
        "  DECISIONSAI_BUILD_DIR           Disposable build directory." \
        "  DECISIONSAI_VERSION             Override VERSION for a build." \
        "  DECISIONSAI_PYTHON              Supported Python 3.12 interpreter." \
        "  DEVELOPER_ID_APPLICATION        Signing identity (release required)." \
        "  NOTARYTOOL_PROFILE              notarytool keychain profile (release required)."
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --release) RELEASE=1 ;;
        --app-only) CREATE_DMG=0 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

[ "$(uname -s)" = "Darwin" ] || { printf 'macOS is required to build DecisionsAI.app.\n' >&2; exit 1; }
[ -f "$VERSION_FILE" ] || { printf 'Missing canonical VERSION file.\n' >&2; exit 1; }
[ -f "$ICON_FILE" ] || { printf 'Missing tracked application icon: %s\n' "$ICON_FILE" >&2; exit 1; }
VERSION="${DECISIONSAI_VERSION:-$(tr -d '[:space:]' < "$VERSION_FILE")}"
printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9]+)*$' || {
    printf 'Invalid release version: %s\n' "$VERSION" >&2
    exit 1
}

for command_name in "$PYTHON_BIN" /usr/libexec/PlistBuddy ditto rsync shasum; do
    command -v "$command_name" >/dev/null 2>&1 || {
        printf 'Required build tool is missing: %s\n' "$command_name" >&2
        exit 1
    }
done
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/verify_runtime.py"
"$PYTHON_BIN" -c 'import PyInstaller' >/dev/null 2>&1 || {
    printf 'PyInstaller is not installed for %s. Install the pinned release tool explicitly.\n' "$PYTHON_BIN" >&2
    exit 1
}

IDENTITY="${DEVELOPER_ID_APPLICATION:-}"
NOTARY_PROFILE="${NOTARYTOOL_PROFILE:-}"
if [ "$RELEASE" -eq 1 ]; then
    [ -n "$IDENTITY" ] || { printf 'DEVELOPER_ID_APPLICATION is required for --release.\n' >&2; exit 1; }
    [ -n "$NOTARY_PROFILE" ] || { printf 'NOTARYTOOL_PROFILE is required for --release.\n' >&2; exit 1; }
    security find-identity -v -p codesigning | grep -Fq "\"$IDENTITY\"" || {
        printf 'Signing identity is not installed: %s\n' "$IDENTITY" >&2
        exit 1
    }
    command -v xcrun >/dev/null 2>&1 || { printf 'xcrun is required for release builds.\n' >&2; exit 1; }
fi

rm -rf "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$WORK_DIR" "$SPEC_DIR" "$OUTPUT_DIR"
rm -rf "$OUTPUT_DIR/$APP_NAME.app"
rm -f "$OUTPUT_DIR/$APP_NAME-$VERSION.dmg" "$OUTPUT_DIR/SHA256SUMS"

# Bundle application resources, but never copy mutable user data, downloaded
# models, caches, logs, or scratch files into a release artifact.
DATA_DIR="$BUILD_DIR/data"
mkdir -p "$DATA_DIR/assets" "$DATA_DIR/distr"
rsync -a \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'tmp/' \
    "$PROJECT_ROOT/assets/" "$DATA_DIR/assets/"
rsync -a \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'core/agent/models/' \
    --exclude 'core/db/tool_embeddings_cache.json' \
    --exclude 'db/' \
    --exclude 'recordings/' \
    "$PROJECT_ROOT/distr/" "$DATA_DIR/distr/"

cd "$PROJECT_ROOT"
"$PYTHON_BIN" -m PyInstaller \
    --noconfirm \
    --clean \
    --name "$APP_NAME" \
    --windowed \
    --onedir \
    --workpath "$WORK_DIR" \
    --distpath "$DIST_DIR" \
    --specpath "$SPEC_DIR" \
    --add-data "$DATA_DIR/assets:assets" \
    --add-data "$DATA_DIR/distr:distr" \
    --add-data "$PROJECT_ROOT/VERSION:." \
    --add-data "$PROJECT_ROOT/CHANGELOG.md:." \
    --add-data "$PROJECT_ROOT/README.md:." \
    --add-data "$PROJECT_ROOT/LICENSE.md:." \
    --add-data "$PROJECT_ROOT/requirements.txt:." \
    --hidden-import PyQt6.QtCore \
    --hidden-import PyQt6.QtGui \
    --hidden-import PyQt6.QtWidgets \
    --hidden-import AppKit \
    --hidden-import distr \
    --hidden-import sqlalchemy \
    --hidden-import litellm \
    --hidden-import ollama \
    --hidden-import pipecat \
    --collect-all vosk \
    --collect-data kokoro_onnx \
    --collect-data language_tags \
    --collect-submodules scipy._external.array_api_compat \
    --copy-metadata pipecat-ai \
    --copy-metadata browser-use \
    --copy-metadata Pillow \
    --exclude-module tkinter \
    --exclude-module matplotlib \
    --exclude-module pandas \
    --icon "$ICON_FILE" \
    --osx-bundle-identifier "$BUNDLE_ID" \
    "$PROJECT_ROOT/bin/start.py"

BUILT_APP="$DIST_DIR/$APP_NAME.app"
[ -d "$BUILT_APP" ] || { printf 'PyInstaller did not produce %s.\n' "$BUILT_APP" >&2; exit 1; }
PLIST="$BUILT_APP/Contents/Info.plist"
[ -f "$PLIST" ] || { printf 'Built app has no Info.plist.\n' >&2; exit 1; }

set_plist() {
    /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}
set_plist CFBundleIdentifier "$BUNDLE_ID"
set_plist CFBundleShortVersionString "$VERSION"
set_plist CFBundleVersion "$VERSION"
set_plist NSHumanReadableCopyright "Copyright © $(date +%Y) Tensology (Pty) Ltd"

if [ "$RELEASE" -eq 1 ]; then
    codesign --force --deep --options runtime --timestamp --sign "$IDENTITY" "$BUILT_APP"
    codesign --verify --deep --strict --verbose=2 "$BUILT_APP"
fi

ditto "$BUILT_APP" "$OUTPUT_DIR/$APP_NAME.app"
if [ "$RELEASE" -eq 1 ]; then
    "$PYTHON_BIN" "$SCRIPT_DIR/verify_release.py" "$OUTPUT_DIR/$APP_NAME.app" \
        --version "$VERSION" --require-signature
else
    "$PYTHON_BIN" "$SCRIPT_DIR/verify_release.py" "$OUTPUT_DIR/$APP_NAME.app" --version "$VERSION"
fi

if [ "$CREATE_DMG" -eq 1 ]; then
    DMG="$OUTPUT_DIR/$APP_NAME-$VERSION.dmg"
    "$SCRIPT_DIR/create_dmg.sh" "$OUTPUT_DIR/$APP_NAME.app" "$DMG"
    if [ "$RELEASE" -eq 1 ]; then
        codesign --force --timestamp --sign "$IDENTITY" "$DMG"
        xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
        xcrun stapler staple "$OUTPUT_DIR/$APP_NAME.app"
        xcrun stapler staple "$DMG"
        spctl --assess --type execute --verbose=2 "$OUTPUT_DIR/$APP_NAME.app"
        spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
    fi
fi

(
    cd "$OUTPUT_DIR"
    shasum -a 256 "$APP_NAME.app/Contents/Info.plist" > SHA256SUMS
    if [ -f "$APP_NAME-$VERSION.dmg" ]; then
        shasum -a 256 "$APP_NAME-$VERSION.dmg" >> SHA256SUMS
    fi
)

printf 'Built DecisionsAI %s at %s\n' "$VERSION" "$OUTPUT_DIR"
if [ "$RELEASE" -eq 0 ]; then
    printf 'Artifact is unsigned. Re-run with --release for distribution.\n'
fi
