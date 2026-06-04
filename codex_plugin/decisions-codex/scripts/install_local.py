#!/usr/bin/env python3
"""Install the DecisionsAI Codex plugin into the local Codex plugin marketplace."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


PLUGIN_NAME = "decisions-codex"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {
            "name": "local-codex-plugins",
            "interface": {"displayName": "Local Codex Plugins"},
            "plugins": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    payload.setdefault("name", "local-codex-plugins")
    payload.setdefault("interface", {"displayName": "Local Codex Plugins"})
    payload.setdefault("plugins", [])
    if not isinstance(payload["plugins"], list):
        raise ValueError(f"{path} field 'plugins' must be a list")
    return payload


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    target = Path.home() / "plugins" / PLUGIN_NAME
    marketplace = Path.home() / ".agents" / "plugins" / "marketplace.json"

    target.parent.mkdir(parents=True, exist_ok=True)
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
    )

    payload = _load_json(marketplace)
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Developer Tools",
    }
    payload["plugins"] = [
        item for item in payload["plugins"]
        if not (isinstance(item, dict) and item.get("name") == PLUGIN_NAME)
    ]
    payload["plugins"].append(entry)
    marketplace.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Installed {PLUGIN_NAME} to {target}")
    print(f"Updated marketplace: {marketplace}")
    print("Restart Codex or reload plugins, then enable DecisionsAI Codex from the plugin list.")


if __name__ == "__main__":
    main()
