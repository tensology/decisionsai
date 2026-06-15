#!/bin/bash
# macOS permission setup — probe sidecar + Python and guide System Settings.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

# shellcheck source=decisions-env.sh
source "$SCRIPT_DIR/bin/decisions-env.sh"

VENV_DIR="${VENV_DIR:-$HOME/.virtualenvs/decisions}"
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ "$OSTYPE" != darwin* ]]; then
    echo "Permission setup is only required on macOS."
    exit 0
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "Virtualenv not found at $VENV_DIR"
    echo "Run ./decisions once to install dependencies, then retry."
    exit 1
fi

# shellcheck source=decisions-sidecar.sh
source "$SCRIPT_DIR/bin/decisions-sidecar.sh"
decisions_start_sidecar "$SCRIPT_DIR"

FILTERED_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --permissions|--setup-permissions) ;;
        *) FILTERED_ARGS+=("$arg") ;;
    esac
done

if [ "${#FILTERED_ARGS[@]}" -gt 0 ]; then
    exec "$VENV_PYTHON" -m distr.core.macos_permissions "${FILTERED_ARGS[@]}"
else
    exec "$VENV_PYTHON" -m distr.core.macos_permissions
fi
