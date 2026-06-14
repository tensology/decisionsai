"""DecisionsAI bundled harness pack bootstrap.

ECC is vendored once under ``plugins/ecc``. This module projects that single
source into the local harnesses a developer already has installed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from distr.core.plugins import CODEX_PLUGIN_NAME, codex_ide_source, ecc_vendor_dir


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ECC_VENDOR_DIR = ecc_vendor_dir()
STATE_VERSION = 1


def _home(path: Path | None = None) -> Path:
    return Path(path).expanduser() if path is not None else Path.home()


def _vendor_metadata() -> dict[str, Any]:
    metadata_path = ECC_VENDOR_DIR / ".decisions-vendor.json"
    if not metadata_path.is_file():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _vendor_ready() -> bool:
    return (ECC_VENDOR_DIR / "skills").is_dir() and (ECC_VENDOR_DIR / "README.md").is_file()


def _detected_harnesses() -> dict[str, bool]:
    return {
        "codex": bool(shutil.which("codex")),
        "claude": bool(shutil.which("claude")),
        "cursor": bool(shutil.which("cursor") or shutil.which("cursor-agent")),
        "pi": bool(shutil.which("pi")),
    }


def _registry_rows() -> list[dict[str, Any]]:
    from distr.core.skills.catalog import load_registry

    rows: list[dict[str, Any]] = []
    for row in load_registry():
        if str(row.get("source") or "") not in {"ecc", "ecc_vendor"}:
            continue
        rows.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "description": row.get("description"),
                "path": row.get("path"),
                "source": row.get("source") or "ecc_vendor",
                "vendor": row.get("vendor"),
            }
        )
    return rows


def _surface_manifest() -> dict[str, Any]:
    surfaces = {
        "skills": "skills",
        "agents": "agents",
        "commands": "commands",
        "rules": "rules",
        "hooks": "hooks",
        "mcp_configs": "mcp-configs",
        "schemas": "schemas",
        "scripts": "scripts",
        "codex_plugin": ".codex-plugin",
        "claude_plugin": ".claude-plugin",
        "opencode": ".opencode",
        "cursor": ".cursor",
        "gemini": ".gemini",
        "zed": ".zed",
        "rust_control_plane": "ecc2",
    }
    entries: dict[str, dict[str, Any]] = {}
    for name, relative in surfaces.items():
        path = ECC_VENDOR_DIR / relative
        if not path.is_dir():
            continue
        entries[name] = {
            "path": str(path.relative_to(PROJECT_ROOT)),
            "file_count": sum(1 for item in path.rglob("*") if item.is_file()),
        }
    return {
        "name": "DecisionsAI ECC Harness Pack",
        "vendor": _vendor_metadata(),
        "root": str(ECC_VENDOR_DIR.relative_to(PROJECT_ROOT)),
        "surfaces": entries,
    }


def _fingerprint(detected: dict[str, bool], rows: list[dict[str, Any]]) -> str:
    payload = {
        "state_version": STATE_VERSION,
        "vendor": _vendor_metadata(),
        "detected": detected,
        "skill_ids": sorted(str(row.get("id") or "") for row in rows),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def _projection_text(*, harness: str, registry_path: Path, manifest_path: Path) -> str:
    vendor_path = ECC_VENDOR_DIR.relative_to(PROJECT_ROOT)
    return f"""---
name: decisions-ecc-harness
description: Use when DecisionsAI, Hermes, Codex, Claude, Cursor, or Pi needs the bundled ECC harness pack: vendored skills, agents, commands, rules, hooks, MCP conventions, and cross-harness operator guidance.
---

# DecisionsAI ECC Harness Pack

DecisionsAI vendors ECC once at `{vendor_path}` and projects it into installed
developer harnesses from a single registry. This `{harness}` projection is an
access point, not a second source copy.

Use the cached registry at `{registry_path}` to discover ECC skills. Use the
surface manifest at `{manifest_path}` to find ECC agents, commands, rules, hooks,
MCP configs, schemas, scripts, and control-plane code. Both files point back to
the vendored source under `plugins/ecc`.

Rules:
- Prefer native DecisionsAI skills when a native and ECC skill share the same id.
- Use ECC skills when they add ecosystem, language, security, MCP, orchestration,
  workflow, or operator coverage that DecisionsAI does not already provide.
- Report useful skill choices, project context, steering, blockers, and completion
  back through DecisionsAI/Hermes. When work is not attached to a workflow, report
  an ambient harness event to `http://127.0.0.1:8765/api/harness/events` with the
  current project folder.
- If DecisionsAI is switched off or unreachable, ambient reporting must fail
  silently and never interrupt local `{harness}` work.
- Keep generated harness projection files small; the source of truth remains
  `plugins/ecc`.
"""


def _required_projection_paths(home: Path, detected: dict[str, bool]) -> list[Path]:
    paths: list[Path] = []
    if detected.get("codex"):
        paths.append(home / "plugins" / CODEX_PLUGIN_NAME / "skills" / "ecc-harness-pack" / "SKILL.md")
    if detected.get("claude"):
        paths.append(home / ".claude" / "skills" / "decisions-ecc-harness" / "SKILL.md")
    if detected.get("cursor"):
        paths.append(home / ".cursor" / "decisions-ecc-harness.md")
    if detected.get("pi"):
        paths.append(home / ".pi" / "skills" / "decisions-ecc-harness" / "SKILL.md")
    return paths


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "harness-pack-state.json"


def _registry_cache_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "ecc-skills-registry.json"


def _surface_manifest_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "ecc-surface-manifest.json"


def _state_is_current(home: Path, fingerprint: str, detected: dict[str, bool]) -> bool:
    path = _state_path(home)
    if not path.is_file():
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if state.get("fingerprint") != fingerprint:
        return False
    if not _registry_cache_path(home).is_file():
        return False
    if not _surface_manifest_path(home).is_file():
        return False
    return all(path.is_file() for path in _required_projection_paths(home, detected))


def _install_codex_plugin_if_requested(*, enabled: bool, detected: dict[str, bool]) -> None:
    if not enabled or not detected.get("codex"):
        return
    script = codex_ide_source() / "scripts" / "install_local.py"
    if not script.is_file():
        return
    try:
        subprocess.run([sys.executable, str(script)], cwd=PROJECT_ROOT, timeout=30, check=False)
    except Exception:
        pass


def _install_editor_extension_if_requested(*, enabled: bool, detected: dict[str, bool], run_full: bool) -> None:
    if not enabled or not run_full or not detected.get("cursor"):
        return
    script = PROJECT_ROOT / "vscode_extension" / "install_vscode_extension.sh"
    if not script.is_file():
        return
    try:
        subprocess.run(["bash", str(script)], cwd=PROJECT_ROOT, timeout=180, check=False)
    except Exception:
        pass


def _write_harness_projections(
    home: Path,
    detected: dict[str, bool],
    registry_path: Path,
    manifest_path: Path,
) -> list[str]:
    written: list[str] = []
    targets = {
        "codex": home / "plugins" / CODEX_PLUGIN_NAME / "skills" / "ecc-harness-pack" / "SKILL.md",
        "claude": home / ".claude" / "skills" / "decisions-ecc-harness" / "SKILL.md",
        "cursor": home / ".cursor" / "decisions-ecc-harness.md",
        "pi": home / ".pi" / "skills" / "decisions-ecc-harness" / "SKILL.md",
    }
    for harness, path in targets.items():
        if not detected.get(harness):
            continue
        if _write_if_changed(
            path,
            _projection_text(
                harness=harness,
                registry_path=registry_path,
                manifest_path=manifest_path,
            ),
        ):
            written.append(str(path))
    return written


def ensure_harness_pack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
    install_codex_plugin: bool = True,
    install_editor_extension: bool = True,
) -> dict[str, Any]:
    """Ensure the vendored ECC harness pack is projected to installed harnesses."""
    base_home = _home(home)
    detected = _detected_harnesses()
    rows = _registry_rows()
    fingerprint = _fingerprint(detected, rows)
    registry_path = _registry_cache_path(base_home)
    manifest_path = _surface_manifest_path(base_home)

    if not run_full and _state_is_current(base_home, fingerprint, detected):
        return {
            "status": "current",
            "vendor_ready": _vendor_ready(),
            "detected": detected,
            "fingerprint": fingerprint,
            "registry_path": str(registry_path),
            "manifest_path": str(manifest_path),
            "written": [],
        }

    _write_json(registry_path, rows)
    _write_json(manifest_path, _surface_manifest())
    _install_codex_plugin_if_requested(enabled=install_codex_plugin, detected=detected)
    _install_editor_extension_if_requested(
        enabled=install_editor_extension,
        detected=detected,
        run_full=run_full,
    )
    written = _write_harness_projections(base_home, detected, registry_path, manifest_path)

    state = {
        "state_version": STATE_VERSION,
        "status": "configured",
        "vendor_ready": _vendor_ready(),
        "vendor": _vendor_metadata(),
        "detected": detected,
        "fingerprint": fingerprint,
        "registry_path": str(registry_path),
        "manifest_path": str(manifest_path),
        "written": written,
    }
    _write_json(_state_path(base_home), state)
    return state


def ensure_harness_pack_setup_quiet() -> None:
    """Best-effort launch hook; never block DecisionsAI startup."""
    if (os.environ.get("DECISIONSAI_SKIP_HARNESS_PACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_harness_pack_setup(run_full=False)
    except Exception:
        pass
