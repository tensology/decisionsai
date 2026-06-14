#!/usr/bin/env python3
"""Install the DecisionsAI Cursor plugin into Cursor's local plugin folder."""

from __future__ import annotations

import shutil
from pathlib import Path


PLUGIN_NAME = "decisions-cursor"


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    manifest = source / ".cursor-plugin" / "plugin.json"
    if not manifest.exists():
        raise SystemExit(f"missing plugin manifest: {manifest}")

    target = Path.home() / ".cursor" / "plugins" / "local" / PLUGIN_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)

    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
    )
    print(f"Installed {PLUGIN_NAME} to {target}")
    print("Restart Cursor or run Developer: Reload Window, then enable DecisionsAI Cursor from Plugins.")


if __name__ == "__main__":
    main()
