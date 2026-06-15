#!/bin/bash
# Login-style environment for Dock / osascript / detached launches.
# Interactive terminal launches usually already have this; GUI spawns often do not.

[ -n "${DECISIONS_ENV_LOADED:-}" ] && return 0
export DECISIONS_ENV_LOADED=1

if [ -x /usr/libexec/path_helper ]; then
    eval "$(/usr/libexec/path_helper -s)"
fi

for _brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$_brew" ]; then
        # shellcheck disable=SC1090
        eval "$("$_brew" shellenv)"
        break
    fi
done

export HOME="${HOME:-$(eval echo ~$(id -un))}"
export USER="${USER:-$(id -un)}"
export SHELL="${SHELL:-$(command -v zsh 2>/dev/null || command -v bash 2>/dev/null || echo /bin/zsh)}"
export PATH="$HOME/.local/bin:$HOME/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:$PATH"
export WORKON_HOME="${WORKON_HOME:-$HOME/.virtualenvs}"
export VIRTUALENVWRAPPER_PYTHON="${VIRTUALENVWRAPPER_PYTHON:-$HOME/.virtualenvs/decisions/bin/python}"
export DECISIONS_PYTHON="${DECISIONS_PYTHON:-$VIRTUALENVWRAPPER_PYTHON}"
export DECISIONS_DB_DIR="${DECISIONS_DB_DIR:-}"
