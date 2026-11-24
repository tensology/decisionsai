#!/bin/bash
# Use a private Python runtime without changing the user's shell or system Python.
RUNTIME_DIR="$HOME/.decisions/runtime"
mkdir -p "$RUNTIME_DIR/bin" || return 1
if [ ! -x "$RUNTIME_DIR/bin/uv" ]; then
    curl -fsSL https://astral.sh/uv/install.sh -o "$RUNTIME_DIR/uv-install.sh" || return 1
    UV_INSTALL_DIR="$RUNTIME_DIR/bin" UV_NO_MODIFY_PATH=1 sh "$RUNTIME_DIR/uv-install.sh" || return 1
fi

export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
if ! PYTHON_CMD=$("$RUNTIME_DIR/bin/uv" python find --managed-python 3.12 2>/dev/null); then
    "$RUNTIME_DIR/bin/uv" python install --no-bin 3.12 || return 1
    PYTHON_CMD=$("$RUNTIME_DIR/bin/uv" python find --managed-python 3.12) || return 1
fi
