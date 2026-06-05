#!/usr/bin/env python3
"""Install and repair the DecisionsAI Codex plugin locally."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


PLUGIN_NAME = "decisions-codex"
MARKETPLACE_NAME = "local-codex-plugins"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {
            "name": MARKETPLACE_NAME,
            "interface": {"displayName": "Local Codex Plugins"},
            "plugins": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    payload.setdefault("name", MARKETPLACE_NAME)
    payload.setdefault("interface", {"displayName": "Local Codex Plugins"})
    payload.setdefault("plugins", [])
    if not isinstance(payload["plugins"], list):
        raise ValueError(f"{path} field 'plugins' must be a list")
    return payload


def _plugin_hash(plugin_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(plugin_root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.name == ".DS_Store":
            continue
        relative = path.relative_to(plugin_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _with_codex_cachebuster(version: str, cachebuster: str) -> str:
    prefix = str(version or "0.1.0").split("+", 1)[0]
    return f"{prefix}+codex.{cachebuster}"


def _cachebust_installed_manifest(plugin_root: Path) -> str:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path} must contain a JSON object")
    old_version = str(manifest.get("version") or "0.1.0")
    next_version = _with_codex_cachebuster(old_version, _plugin_hash(plugin_root))
    manifest["version"] = next_version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return next_version


def _write_marketplace(marketplace: Path) -> str:
    payload = _load_json(marketplace)
    marketplace_name = str(payload.get("name") or MARKETPLACE_NAME)
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
        "policy": {"installation": "INSTALLED_BY_DEFAULT", "authentication": "ON_INSTALL"},
        "category": "Developer Tools",
    }
    payload["plugins"] = [
        item for item in payload["plugins"]
        if not (isinstance(item, dict) and item.get("name") == PLUGIN_NAME)
    ]
    payload["plugins"].append(entry)
    marketplace.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return marketplace_name


def _reinstall_in_codex(marketplace_name: str) -> tuple[bool, str]:
    codex = shutil.which("codex")
    if not codex:
        return False, "Codex CLI was not found; repaired the local marketplace only."
    selector = f"{PLUGIN_NAME}@{marketplace_name}"
    try:
        result = subprocess.run(
            [codex, "plugin", "add", selector],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        return False, f"Codex plugin reinstall skipped: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, f"Codex plugin reinstall failed: {detail}"
    return True, f"Reinstalled {selector} in Codex."


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

    version = _cachebust_installed_manifest(target)
    marketplace_name = _write_marketplace(marketplace)
    reinstalled, reinstall_message = _reinstall_in_codex(marketplace_name)

    print(f"Installed {PLUGIN_NAME} to {target}")
    print(f"Updated marketplace: {marketplace}")
    print(f"Prepared Codex plugin version: {version}")
    print(reinstall_message)
    if reinstalled:
        print("Codex plugin is installed and enabled.")


if __name__ == "__main__":
    main()
