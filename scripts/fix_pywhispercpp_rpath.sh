#!/bin/bash
# Repair pywhispercpp's bundled dylib linkage on macOS.
#
# Upstream stopped shipping scripts/install_pywhispercpp.sh, which used to
# rebuild pywhispercpp against whisper.cpp master. The PyPI/cached wheel now
# links its bundled dylibs (libwhisper, libggml*) and libc++ via an @rpath
# that points at the (now deleted) pip build directory, so `import
# pywhispercpp` fails with "Library not loaded: @rpath/libwhisper.1.dylib".
#
# The dylibs ARE present next to the extension in site-packages; only the
# rpath is wrong. This script repoints them at @loader_path and libc++ at the
# system copy, then re-signs (install_name_tool invalidates the signature).
#
# Idempotent and safe to run on every launch. No-op off macOS.
#
# Usage: scripts/fix_pywhispercpp_rpath.sh [VENV_DIR]

set -u

VENV_DIR="${1:-${VENV_DIR:-$HOME/.virtualenvs/decisions}}"

if [ "$(uname -s)" != "Darwin" ]; then
    exit 0
fi

PKG_DIR=""
for candidate in "$VENV_DIR"/lib/python*/site-packages; do
    [ -d "$candidate" ] && PKG_DIR="$candidate"
done
[ -n "$PKG_DIR" ] || exit 0

SO=$(ls "$PKG_DIR"/_pywhispercpp*.so 2>/dev/null | head -1)
[ -n "$SO" ] || exit 0

# Quick health check: if it already imports, nothing to do.
if "$VENV_DIR/bin/python" -c "import _pywhispercpp" >/dev/null 2>&1; then
    exit 0
fi

echo "Repairing pywhispercpp dylib linkage..."

DYLIB_NAMES=(
    "libwhisper.1.dylib"
    "libggml.0.dylib"
    "libggml-base.0.dylib"
    "libggml-cpu.0.dylib"
    "libggml-blas.0.dylib"
    "libggml-metal.0.dylib"
)

TARGETS=("$SO")
for name in "${DYLIB_NAMES[@]}"; do
    [ -f "$PKG_DIR/$name" ] && TARGETS+=("$PKG_DIR/$name")
done

for f in "${TARGETS[@]}"; do
    # Add @loader_path rpath if not already present.
    if ! otool -l "$f" 2>/dev/null | grep -q "@loader_path"; then
        install_name_tool -add_rpath @loader_path "$f" 2>/dev/null || true
    fi
    # libc++ ships with macOS; the wheel references it via stale @rpath.
    install_name_tool -change @rpath/libc++.1.dylib /usr/lib/libc++.1.dylib "$f" 2>/dev/null || true
    codesign --force --sign - "$f" >/dev/null 2>&1 || true
done

if "$VENV_DIR/bin/python" -c "import _pywhispercpp" >/dev/null 2>&1; then
    echo "✓ pywhispercpp linkage repaired"
else
    echo "⚠ pywhispercpp still not importable after repair" >&2
fi
