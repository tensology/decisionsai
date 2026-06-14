#!/usr/bin/env python3
"""Write loop preset JSON bundles from hand-authored definitions. Run from repo root."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from distr.core.skills.catalog import filter_known_skill_ids  # noqa: E402
from distr.core.workflow.loop_preset_definitions import LOOP_PRESET_DEFINITIONS  # noqa: E402
from distr.core.workflow.loop_preset_loader import (  # noqa: E402
    BUNDLE_VERSION,
    bundles_dir,
    presets_root,
)

BUNDLES_DIR = bundles_dir()
MANIFEST_PATH = presets_root() / "manifest.json"


def _validate_skills() -> None:
    all_skills: set[str] = set()
    for preset in LOOP_PRESET_DEFINITIONS:
        for step in preset.get("steps") or []:
            all_skills.update(step.get("skills") or [])
    known = set(filter_known_skill_ids(list(all_skills)))
    missing = sorted(all_skills - known)
    if missing:
        raise SystemExit(f"Unknown skill ids in preset definitions: {missing}")


def _validate_step1_alignment(preset: dict) -> None:
    """Ensure loop_contract.step_1 aligns with the first persisted step instruction."""
    steps = preset.get("steps") or []
    loop_contract = preset.get("loop_contract") or {}
    step_1 = str(loop_contract.get("step_1") or "").strip().lower()
    if not steps or not step_1:
        return
    first_instruction = str(steps[0].get("instruction") or "").strip().lower()
    if not first_instruction:
        return
    if first_instruction[:40] in step_1 or step_1[:40] in first_instruction:
        return
    raise SystemExit(
        f"Preset {preset.get('slug')}: loop_contract.step_1 does not align with steps[0].instruction"
    )


def main() -> None:
    _validate_skills()
    BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
    manifest_presets = []
    for preset in LOOP_PRESET_DEFINITIONS:
        _validate_step1_alignment(preset)
        slug = preset["slug"]
        filename = f"bundles/{slug}.json"
        path = presets_root() / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(preset, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        manifest_presets.append(
            {
                "slug": slug,
                "name": preset["name"],
                "role": preset.get("role"),
                "category": preset.get("category"),
                "archetype": preset.get("archetype"),
                "file": filename,
            }
        )
        print(f"wrote {path} ({len(preset.get('steps') or [])} steps)")

    manifest = {
        "format_version": BUNDLE_VERSION,
        "format": "decisionsai_loop_preset_manifest_v1",
        "description": "Active role-based loop presets. Older presets remain parked as unlisted bundle files.",
        "presets": manifest_presets,
    }
    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"wrote {MANIFEST_PATH} ({len(manifest_presets)} presets)")


if __name__ == "__main__":
    main()
