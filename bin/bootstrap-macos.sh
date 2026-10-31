#!/bin/bash
# User-owned runtime: no Homebrew, sudo, or changes to the user's shell required.
RUNTIME_DIR="$HOME/.decisions/runtime"
mkdir -p "$RUNTIME_DIR/bin" || return 1
if [ ! -x "$RUNTIME_DIR/bin/uv" ]; then
    curl -fsSL https://astral.sh/uv/install.sh -o "$RUNTIME_DIR/uv-install.sh" || return 1
    UV_INSTALL_DIR="$RUNTIME_DIR/bin" UV_NO_MODIFY_PATH=1 sh "$RUNTIME_DIR/uv-install.sh" || return 1
fi
export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
"$RUNTIME_DIR/bin/uv" python install --no-bin 3.12 || return 1
PYTHON_CMD=$("$RUNTIME_DIR/bin/uv" python find --managed-python 3.12) || return 1
export PATH="$RUNTIME_DIR/system/bin:$RUNTIME_DIR/bin:$PATH"
if ! command -v brew >/dev/null 2>&1; then
    if [ ! -x "$RUNTIME_DIR/bin/micromamba" ]; then
        case "$(uname -m)" in
            arm64) MAMBA_PLATFORM=osx-arm64 ;;
            x86_64) MAMBA_PLATFORM=osx-64 ;;
            *) echo 'Unsupported macOS architecture' >&2; exit 1 ;;
        esac
        curl -fsSL "https://micro.mamba.pm/api/micromamba/$MAMBA_PLATFORM/latest" -o "$RUNTIME_DIR/micromamba.tar.bz2" || return 1
        tar -xjf "$RUNTIME_DIR/micromamba.tar.bz2" -C "$RUNTIME_DIR" bin/micromamba || return 1
    fi
    if [ ! -f "$RUNTIME_DIR/system/lib/libportaudio.dylib" ] || [ ! -x "$RUNTIME_DIR/system/bin/ffmpeg" ]; then
        "$RUNTIME_DIR/bin/micromamba" create -y -p "$RUNTIME_DIR/system" -c conda-forge portaudio ffmpeg pkg-config || return 1
    fi
    export CFLAGS="-I$RUNTIME_DIR/system/include ${CFLAGS:-}"
    export LDFLAGS="-L$RUNTIME_DIR/system/lib -Wl,-rpath,$RUNTIME_DIR/system/lib ${LDFLAGS:-}"
    export PKG_CONFIG_PATH="$RUNTIME_DIR/system/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
fi
