"""BuilderIO visual-plan skills vendored for DecisionsAI harness projection."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from distr.core.harness_bootstrap import (
    detected_harnesses,
    install_skills_to_harnesses,
    projection_paths,
    write_projection_skill,
)
from distr.core.plugins import VISUAL_PLAN_PACK_DIR, project_root, visual_plan_skills_dir

PROJECT_ROOT = project_root()
LOCAL_SKILLS = PROJECT_ROOT / "skills"
MANIFEST_PATH = VISUAL_PLAN_PACK_DIR / "manifest.json"
STATE_VERSION = 1


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "visual-plan-pack-state.json"


def _registry_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "visual-plan-registry.json"


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _vendor_ready() -> bool:
    base = visual_plan_skills_dir()
    return base.is_dir() and any(base.iterdir())


def _skill_sources() -> tuple[dict[str, Path], dict[str, bool]]:
    sources: dict[str, Path] = {}
    overwrite: dict[str, bool] = {}
    base = visual_plan_skills_dir()
    for entry in _load_manifest().get("project_full") or []:
        skill_id = str(entry.get("id") or "").strip()
        if not skill_id:
            continue
        path = base / skill_id
        if path.is_dir() and (path / "SKILL.md").is_file():
            sources[skill_id] = path
            overwrite[skill_id] = bool(entry.get("overwrite", True))
    for skill_id in _load_manifest().get("index_only") or []:
        path = LOCAL_SKILLS / skill_id
        if path.is_dir() and (path / "SKILL.md").is_file():
            sources[skill_id] = path
            overwrite[skill_id] = True
    return sources, overwrite


def _fingerprint(sources: dict[str, Path], detected: dict[str, bool]) -> str:
    vendor_path = VISUAL_PLAN_PACK_DIR / ".decisions-vendor.json"
    vendor_mtime = vendor_path.stat().st_mtime if vendor_path.is_file() else 0
    payload = {
        "skill_ids": sorted(sources.keys()),
        "detected": detected,
        "vendor_mtime": vendor_mtime,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_visual_plan_pre_chain(skill_ids: list[str]) -> list[str]:
    blob = " ".join(skill_ids).lower()
    prepend: list[str] = []
    if any(t in blob for t in ("plan", "architecture", "diagram", "mermaid", "visual", "recap", "pr review")):
        prepend.append("decisions-visual-plan")
    if any(t in blob for t in ("diagram", "mermaid", "erdiagram", "sequence", "flowchart")):
        prepend.append("decisions-mermaid-diagrams")
    if any(t in blob for t in ("open design", "prototype", "deck", "hyperframe", "excalidraw")):
        prepend.append("decisions-open-design")
    merged: list[str] = []
    seen: set[str] = set()
    for skill_id in [*prepend, *skill_ids]:
        key = str(skill_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def _projection_text(*, harness: str, registry_path: Path) -> str:
    ref = PROJECT_ROOT.parent / "reference"
    return f"""---
name: decisions-visual-plan-harness
description: Visual planning for {harness} — Mermaid viewer, BuilderIO visual-plan/recap, Open Design routing.
---

# DecisionsAI Visual Plan Harness

- **decisions-visual-plan** — route between surfaces
- **decisions-mermaid-diagrams** — freestanding Mermaid viewer (`/diagram/`)
- **decisions-open-design** — Open Design app + MCP for UI/decks/motion
- **visual-plan**, **visual-recap**, **quick-recap** — BuilderIO skills (vendored)

Registry: `{registry_path}`

Reference: `{ref}/builderio-skills`, `{ref}/open-design`

Sync vendor: `python3 scripts/sync_visual_plan_pack.py`
"""


def ensure_visual_plan_pack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
) -> dict[str, Any]:
    base_home = Path(home).expanduser() if home is not None else Path.home()
    if not _vendor_ready():
        return {"status": "skipped", "reason": "run scripts/sync_visual_plan_pack.py"}

    detected = detected_harnesses()
    sources, overwrite_map = _skill_sources()
    fingerprint = _fingerprint(sources, detected)
    registry_path = _registry_path(base_home)

    rows = [{"id": k, "path": str(v), "source": "visual_plan_vendor"} for k, v in sources.items()]
    _write_json(registry_path, rows)

    written = install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=sources,
        also_commands=True,
        overwrite=True,
        overwrite_by_skill=overwrite_map,
    )

    for harness, path in projection_paths(base_home, detected, "decisions-visual-plan-harness").items():
        if write_projection_skill(path, _projection_text(harness=harness, registry_path=registry_path)):
            written.append(str(path))

    _write_json(_state_path(base_home), {"version": STATE_VERSION, "fingerprint": fingerprint})

    return {
        "state_version": STATE_VERSION,
        "status": "configured",
        "vendor_ready": _vendor_ready(),
        "detected": detected,
        "fingerprint": fingerprint,
        "skill_count": len(sources),
        "written": written,
        "registry_path": str(registry_path),
        "run_full": run_full,
    }


def ensure_visual_plan_pack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_VISUAL_PLAN_PACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_visual_plan_pack_setup(run_full=False)
    except Exception:
        pass
