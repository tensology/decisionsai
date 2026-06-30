"""Curated community skills (humanizer, last30days, marketing, design aesthetics)."""

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
from distr.core.plugins import COMMUNITY_SKILLS_PACK_DIR, community_skills_dir, project_root

PROJECT_ROOT = project_root()
LOCAL_SKILLS = PROJECT_ROOT / "skills"
MANIFEST_PATH = COMMUNITY_SKILLS_PACK_DIR / "manifest.json"
STATE_VERSION = 1


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "community-skills-pack-state.json"


def _registry_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "community-skills-registry.json"


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _vendor_ready() -> bool:
    return community_skills_dir().is_dir() and any(community_skills_dir().iterdir())


def _skill_sources() -> tuple[dict[str, Path], dict[str, bool]]:
    sources: dict[str, Path] = {}
    overwrite: dict[str, bool] = {}
    base = community_skills_dir()
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
        if path.is_dir():
            sources[skill_id] = path
            overwrite[skill_id] = True
    return sources, overwrite


def _fingerprint(sources: dict[str, Path], detected: dict[str, bool]) -> str:
    vendor_path = COMMUNITY_SKILLS_PACK_DIR / ".decisions-vendor.json"
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


def merge_community_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    blob = " ".join(skill_ids).lower()
    prepend: list[str] = []
    if any(t in blob for t in ("article", "content", "copy", "landing", "publish", "humanize")):
        prepend.append("humanizer")
    if any(t in blob for t in ("research", "last30", "sentiment", "competitor", "trend")):
        prepend.append("last30days")
    if any(t in blob for t in ("marketing", "cro", "seo-audit", "copywriting", "launch")):
        prepend.extend(["product-marketing", "decisions-marketing-skills"])
    if any(t in blob for t in ("design", "aesthetic", "dashboard", "minimal", "ui")):
        prepend.append("decisions-design-aesthetics")
    if any(
        t in blob
        for t in ("design", "landing", "portfolio", "redesign", "frontend", "anti-slop")
    ):
        prepend.append("design-taste-frontend")
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
name: decisions-community-skills-harness
description: Curated community skills for {harness} — humanizer, last30days, marketing, design aesthetics, taste-skill.
---

# DecisionsAI Community Skills Harness

Curated pack (not a bulk dump of 44 marketing or 50 design skills).

- **humanizer** — remove AI writing tells before publish
- **last30days** — 30-day social/web research synthesis
- **product-marketing**, **cro**, **copywriting**, **seo-audit**, **ai-seo**, **competitors** — marketing stack (skipped if already in harness)
- **minimal**, **enterprise**, **professional**, **shadcn**, **bento** — design aesthetic tokens
- **decisions-marketing-skills** / **decisions-design-aesthetics** — indexes for on-demand skills
- **design-taste-frontend** — anti-slop frontend taste and anti-template UI guidance

Registry: `{registry_path}`

Reference clones: `{ref}/marketingskills`, `{ref}/awesome-design-skills`

Sync vendor: `python3 scripts/sync_community_skills_pack.py`
"""


def ensure_community_skills_pack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
) -> dict[str, Any]:
    base_home = Path(home).expanduser() if home is not None else Path.home()
    detected = detected_harnesses()
    sources, overwrite_map = _skill_sources()
    fingerprint = _fingerprint(sources, detected)
    registry_path = _registry_path(base_home)

    rows = [{"id": k, "path": str(v), "source": "community_vendor"} for k, v in sources.items()]
    _write_json(registry_path, rows)

    written = install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=sources,
        also_commands=True,
        overwrite=True,
        overwrite_by_skill=overwrite_map,
    )

    for harness, path in projection_paths(base_home, detected, "decisions-community-skills-harness").items():
        if write_projection_skill(path, _projection_text(harness=harness, registry_path=registry_path)):
            written.append(str(path))

    return {
        "state_version": STATE_VERSION,
        "status": "configured",
        "vendor_ready": _vendor_ready(),
        "detected": detected,
        "fingerprint": fingerprint,
        "skill_count": len(sources),
        "written": written,
        "registry_path": str(registry_path),
    }


def ensure_community_skills_pack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_COMMUNITY_SKILLS_PACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_community_skills_pack_setup(run_full=False)
    except Exception:
        pass
