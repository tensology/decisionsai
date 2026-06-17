"""DecisionsAI competition harness pack bootstrap (Ponytail + Fallow).

Projects vendored ponytail/fallow skills and rules into installed harnesses,
mirroring the ECC harness pack flow.
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

from distr.core.plugins import (
    CODEX_PLUGIN_NAME,
    COMPETITION_PACK_DIR,
    codex_ide_source,
    competition_ponytail_skills_dir,
    competition_fallow_skills_dir,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_VERSION = 1
DEFAULT_WORKFLOW_PRE_CHAIN = ("ponytail", "fallow")


def _home(path: Path | None = None) -> Path:
    return Path(path).expanduser() if path is not None else Path.home()


def _vendor_metadata() -> dict[str, Any]:
    metadata_path = COMPETITION_PACK_DIR / ".decisions-vendor.json"
    if not metadata_path.is_file():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _vendor_ready() -> bool:
    return (
        (competition_ponytail_skills_dir() / "ponytail" / "SKILL.md").is_file()
        and (competition_fallow_skills_dir() / "fallow" / "SKILL.md").is_file()
    )


def _detected_harnesses() -> dict[str, bool]:
    return {
        "codex": bool(shutil.which("codex")),
        "claude": bool(shutil.which("claude")),
        "cursor": bool(shutil.which("cursor") or shutil.which("cursor-agent")),
        "pi": bool(shutil.which("pi")),
    }


def competition_skill_roots() -> list[Path]:
    return [competition_ponytail_skills_dir(), competition_fallow_skills_dir()]


def _registry_rows() -> list[dict[str, Any]]:
    from distr.core.skills.registry import SkillRegistry

    registry = SkillRegistry(competition_roots=competition_skill_roots()).scan()
    rows: list[dict[str, Any]] = []
    for entry in registry.entries.values():
        rows.append(
            {
                "id": entry.canonical_id,
                "name": entry.name,
                "description": entry.description,
                "path": str(entry.path),
                "source": "competition_vendor",
                "vendor": "competition-pack",
            }
        )
    return rows


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


def _projection_text(*, harness: str, registry_path: Path) -> str:
    vendor_path = COMPETITION_PACK_DIR.relative_to(PROJECT_ROOT)
    return f"""---
name: decisions-competition-harness
description: Use when DecisionsAI work should apply Ponytail minimal-code discipline and Fallow JS/TS codebase intelligence before shipping or opening a PR.
---

# DecisionsAI Competition Harness Pack

DecisionsAI vendors Ponytail and Fallow once at `{vendor_path}` and projects them into
installed developer harnesses. This `{harness}` projection is an access point, not a
second source copy.

## Prerequisites

1. **Ponytail** — always-on lazy senior dev ladder (YAGNI → stdlib → one line → minimum code).
   Use skills `ponytail`, `ponytail-review`, `ponytail-audit`, `ponytail-debt`, `ponytail-help`.
2. **Fallow** — for JavaScript/TypeScript repos, run `npx fallow audit --format json --quiet || true`
   before merge/PR and after material agent edits. Use skill `fallow` for command routing.

Registry cache: `{registry_path}`

## Workflow defaults

DecisionsAI workflow steps should treat this pack as a baseline when `pre_chain` is empty:

- `ponytail` — keep diffs minimal while implementing
- `fallow` — audit changed JS/TS before completion (skip when the project is not JS/TS)

## Rules

- Prefer native DecisionsAI skills when they conflict with the same id.
- Report completion, audit output, and blockers back through DecisionsAI/Hermes.
- If DecisionsAI is unreachable, continue local `{harness}` work; ambient reporting fails silently.
- Ponytail never trades away trust-boundary validation, data-loss handling, security, or accessibility.
- Fallow exit code 1 means findings exist, not CLI failure; use `|| true` in shell wrappers.
"""


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "competition-pack-state.json"


def _registry_cache_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "competition-skills-registry.json"


def _required_projection_paths(home: Path, detected: dict[str, bool]) -> list[Path]:
    paths: list[Path] = []
    if detected.get("codex"):
        paths.append(home / "plugins" / CODEX_PLUGIN_NAME / "skills" / "decisions-competition-harness" / "SKILL.md")
    if detected.get("claude"):
        paths.append(home / ".claude" / "skills" / "decisions-competition-harness" / "SKILL.md")
    if detected.get("cursor"):
        paths.append(home / ".cursor" / "decisions-competition-harness.md")
    if detected.get("pi"):
        paths.append(home / ".pi" / "skills" / "decisions-competition-harness" / "SKILL.md")
    return paths


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
    return all(path.is_file() for path in _required_projection_paths(home, detected))


def _copy_skill_tree(src: Path, dest: Path) -> bool:
    if not src.is_dir():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return True


def _install_harness_skills(home: Path, detected: dict[str, bool]) -> list[str]:
    written: list[str] = []
    ponytail_src = competition_ponytail_skills_dir()
    fallow_src = competition_fallow_skills_dir()
    cursor_rule = COMPETITION_PACK_DIR / "ponytail" / "rules" / "cursor-ponytail.mdc"

    skill_targets: dict[str, Path] = {}
    if detected.get("codex"):
        base = home / "plugins" / CODEX_PLUGIN_NAME / "skills"
        for skill_dir in sorted(ponytail_src.iterdir()) if ponytail_src.is_dir() else []:
            if skill_dir.is_dir():
                skill_targets[f"codex:{skill_dir.name}"] = base / skill_dir.name
        skill_targets["codex:fallow"] = base / "fallow"
        skill_targets["codex:decisions-competition-harness"] = base / "decisions-competition-harness"
    if detected.get("claude"):
        base = home / ".claude" / "skills"
        for skill_dir in sorted(ponytail_src.iterdir()) if ponytail_src.is_dir() else []:
            if skill_dir.is_dir():
                skill_targets[f"claude:{skill_dir.name}"] = base / skill_dir.name
        skill_targets["claude:fallow"] = base / "fallow"
        skill_targets["claude:decisions-competition-harness"] = base / "decisions-competition-harness"
    if detected.get("pi"):
        base = home / ".pi" / "skills"
        for skill_dir in sorted(ponytail_src.iterdir()) if ponytail_src.is_dir() else []:
            if skill_dir.is_dir():
                skill_targets[f"pi:{skill_dir.name}"] = base / skill_dir.name
        skill_targets["pi:fallow"] = base / "fallow"
        skill_targets["pi:decisions-competition-harness"] = base / "decisions-competition-harness"

    for key, dest in skill_targets.items():
        harness, name = key.split(":", 1)
        if name == "decisions-competition-harness":
            continue
        src = ponytail_src / name if name.startswith("ponytail") else fallow_src / name
        if _copy_skill_tree(src, dest):
            written.append(str(dest))

    registry_path = _registry_cache_path(home)
    projection_targets = {
        "codex": home / "plugins" / CODEX_PLUGIN_NAME / "skills" / "decisions-competition-harness" / "SKILL.md",
        "claude": home / ".claude" / "skills" / "decisions-competition-harness" / "SKILL.md",
        "cursor": home / ".cursor" / "decisions-competition-harness.md",
        "pi": home / ".pi" / "skills" / "decisions-competition-harness" / "SKILL.md",
    }
    for harness, path in projection_targets.items():
        if not detected.get(harness):
            continue
        if _write_if_changed(path, _projection_text(harness=harness, registry_path=registry_path)):
            written.append(str(path))

    if detected.get("cursor") and cursor_rule.is_file():
        dest_rule = home / ".cursor" / "rules" / "decisions-ponytail.mdc"
        if _write_if_changed(dest_rule, cursor_rule.read_text(encoding="utf-8", errors="replace")):
            written.append(str(dest_rule))

    return written


def _install_codex_plugin_if_requested(*, enabled: bool, detected: dict[str, bool]) -> None:
    if not enabled or not detected.get("codex"):
        return
    script = codex_ide_source() / "scripts" / "install_local.py"
    if script.is_file():
        try:
            subprocess.run([sys.executable, str(script)], cwd=PROJECT_ROOT, timeout=30, check=False)
        except Exception:
            pass
    if not shutil.which("codex"):
        return
    for cmd in (
        ["codex", "plugin", "marketplace", "add", "DietrichGebert/ponytail"],
    ):
        try:
            subprocess.run(cmd, timeout=120, check=False, env={**os.environ, "TERM": "dumb"})
        except Exception:
            pass


def _install_fallow_cli_if_requested(*, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"installed": False, "reason": "skipped"}
    if shutil.which("fallow"):
        return {"installed": True, "method": "existing"}
    if not shutil.which("npm"):
        return {"installed": False, "reason": "npm not found"}
    try:
        result = subprocess.run(
            ["npm", "install", "-g", "fallow"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        ok = result.returncode == 0 and bool(shutil.which("fallow"))
        return {
            "installed": ok,
            "method": "npm-global",
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:500],
        }
    except Exception as exc:
        return {"installed": False, "reason": str(exc)}


_CURSOR_BACKENDS = frozenset({"cursor", "cursor_ide", "vscode_ide"})


def ponytail_cursor_rule_source() -> Path | None:
    path = COMPETITION_PACK_DIR / "ponytail" / "rules" / "cursor-ponytail.mdc"
    return path if path.is_file() else None


def push_ponytail_cursor_rule_to_project(*, project_folder: str, backend_id: str = "") -> str | None:
    """Copy Ponytail always-on rule into the active project for Cursor backends."""
    from distr.core.project_cli_backends import normalize_backend_id

    bid = normalize_backend_id(backend_id or "")
    if bid not in _CURSOR_BACKENDS:
        return None
    src = ponytail_cursor_rule_source()
    if not src:
        return None
    project_path = Path(project_folder).expanduser().resolve()
    if not project_path.is_dir():
        return None
    rules_dir = project_path / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    dest = rules_dir / "ponytail.mdc"
    shutil.copy2(src, dest)
    return str(dest)


def project_has_js_ts_surface(folder: str) -> bool:
    root = Path(folder or "").expanduser()
    if not root.is_dir():
        return False
    markers = ("package.json", "tsconfig.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb")
    return any((root / name).is_file() for name in markers)


def default_competition_pre_chain(*, project_folder: str = "") -> list[str]:
    chain = ["ponytail"]
    if project_has_js_ts_surface(project_folder):
        chain.append("fallow")
    return chain


def merge_competition_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    baseline = default_competition_pre_chain(project_folder=project_folder)
    merged: list[str] = []
    seen: set[str] = set()
    for skill_id in [*baseline, *skill_ids]:
        key = str(skill_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def ensure_competition_pack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
    install_codex_plugin: bool = True,
    install_fallow_cli: bool = True,
) -> dict[str, Any]:
    base_home = _home(home)
    detected = _detected_harnesses()
    rows = _registry_rows()
    fingerprint = _fingerprint(detected, rows)
    registry_path = _registry_cache_path(base_home)

    if not run_full and _state_is_current(base_home, fingerprint, detected):
        written = _install_harness_skills(base_home, detected)
        return {
            "status": "current" if not written else "refreshed",
            "vendor_ready": _vendor_ready(),
            "detected": detected,
            "fingerprint": fingerprint,
            "registry_path": str(registry_path),
            "written": written,
        }

    _write_json(registry_path, rows)
    _install_codex_plugin_if_requested(enabled=install_codex_plugin, detected=detected)
    fallow_install = _install_fallow_cli_if_requested(enabled=install_fallow_cli and run_full)
    written = _install_harness_skills(base_home, detected)

    state = {
        "state_version": STATE_VERSION,
        "status": "configured",
        "vendor_ready": _vendor_ready(),
        "vendor": _vendor_metadata(),
        "detected": detected,
        "fingerprint": fingerprint,
        "registry_path": str(registry_path),
        "written": written,
        "fallow_cli": fallow_install,
        "node_available": bool(shutil.which("node")),
    }
    _write_json(_state_path(base_home), state)
    return state


def ensure_competition_pack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_COMPETITION_PACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_competition_pack_setup(run_full=False)
    except Exception:
        pass
