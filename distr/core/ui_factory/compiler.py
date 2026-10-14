"""Deterministic compiler from a ScreenContract to bounded React artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from .contracts import ScreenContract
from .template_packs import get_template_pack


@dataclass(frozen=True)
class CompilationResult:
    screen_id: str
    contract_fingerprint: str
    template_pack: str
    template_version: str
    files: tuple[str, ...]


def _component_name(screen_id: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", screen_id) if part) + "Screen"


def compile_screen_contract(
    contract: ScreenContract | dict[str, Any],
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> CompilationResult:
    resolved = ScreenContract.from_dict(contract) if isinstance(contract, dict) else contract
    resolved.validate()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = _component_name(resolved.screen_id)
    pack = get_template_pack(resolved.screen_type)
    rendered = pack.render(resolved, name)
    artifacts = {
        output / f"{name}.tsx": rendered.component,
        output / f"{name}.css": rendered.stylesheet,
        output / f"{name}.spec.ts": rendered.playwright_spec,
    }
    collisions = [str(path) for path in artifacts if path.exists() and not overwrite]
    if collisions:
        raise FileExistsError("Refusing to overwrite generated files: " + ", ".join(collisions))
    manifest_path = output / f"{name}.ui-manifest.json"
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite generated file: {manifest_path}")
    for path, content in artifacts.items():
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "screen_id": resolved.screen_id,
        "contract_fingerprint": resolved.fingerprint,
        "template_pack": pack.pack_id,
        "template_version": pack.version,
        "generated_files": [path.name for path in artifacts],
        "acceptance_criteria": [item.criterion_id for item in resolved.acceptance_criteria],
        "required_viewports": list(resolved.viewports),
        "required_themes": list(resolved.theme_modes),
        "required_states": list(resolved.states),
        "allowed_files": list(resolved.allowed_files),
        "anti_bloat_constraints": list(resolved.anti_bloat_constraints),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return CompilationResult(
        screen_id=resolved.screen_id,
        contract_fingerprint=resolved.fingerprint,
        template_pack=pack.pack_id,
        template_version=pack.version,
        files=tuple(str(path) for path in (*artifacts.keys(), manifest_path)),
    )
