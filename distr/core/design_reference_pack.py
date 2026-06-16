"""Design reference sources (Refero, Mobbin, Aceternity, Godly) for UI ideation and build."""

from __future__ import annotations

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
from distr.core.plugins import project_root

PROJECT_ROOT = project_root()
LOCAL_SKILLS = PROJECT_ROOT / "skills"
STATE_VERSION = 1

LOCAL_DESIGN_SKILLS: tuple[str, ...] = (
    "decisions-ui-ideation",
    "decisions-design-references",
)

ECC_DESIGN_SKILLS: tuple[str, ...] = (
    "frontend-design-direction",
)


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "design-reference-pack-state.json"


def _mcp_setup_script_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "mcp-setup-design.sh"


def project_has_ui_surface(folder: str) -> bool:
    root = Path(folder or "").expanduser()
    if not root.is_dir():
        return False
    markers = (
        "package.json",
        "tailwind.config.js",
        "tailwind.config.ts",
        "vite.config.ts",
        "vite.config.js",
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
    )
    if any((root / name).is_file() for name in markers):
        return True
    for pattern in ("**/*.tsx", "**/*.jsx", "**/*.vue", "**/*.svelte"):
        try:
            if next(root.glob(pattern), None) is not None:
                return True
        except Exception:
            pass
    return False


def _skill_ids_mention_ui(skill_ids: list[str]) -> bool:
    ui_tokens = (
        "frontend",
        "design",
        "landing",
        "dashboard",
        "ui",
        "aceternity",
        "mobbin",
        "refero",
        "tailwind",
        "react",
        "vue",
    )
    blob = " ".join(skill_ids).lower()
    return any(token in blob for token in ui_tokens)


def default_design_pre_chain(*, project_folder: str = "", skill_ids: list[str] | None = None) -> list[str]:
    chain = ["decisions-design-references"]
    if project_has_ui_surface(project_folder) or _skill_ids_mention_ui(skill_ids or []):
        chain = ["decisions-ui-ideation", "decisions-design-references", "frontend-design-direction"]
    return chain


def merge_design_reference_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    baseline = default_design_pre_chain(project_folder=project_folder, skill_ids=skill_ids)
    merged: list[str] = list(skill_ids)
    anchor = "decisions-harness-stack"
    insert_at = merged.index(anchor) + 1 if anchor in merged else 0
    offset = 0
    for skill_id in baseline:
        if skill_id in merged:
            continue
        merged.insert(insert_at + offset, skill_id)
        offset += 1
    return merged


def design_mcp_recommendations() -> dict[str, Any]:
    return {
        "refero": {
            "description": "130k+ real product screens, flows, and DESIGN.md style tokens via official MCP",
            "docs": "https://refero.design/mcp",
            "requires": "Refero Pro subscription (OAuth on first use in Cursor)",
            "auto_merge": True,
            "cursor_name": "refero",
            "skill": "decisions-design-references",
            "refero_agent_skill": "npx skills add https://github.com/referodesign/refero_skill",
            "mcp": {
                "url": "https://api.refero.design/mcp",
            },
            "token_env": ["REFERO_TOKEN", "REFERO_API_TOKEN"],
            "mcp_with_token": {
                "url": "https://api.refero.design/mcp",
                "headers": {"Authorization": "Bearer ${env:REFERO_TOKEN}"},
            },
            "example_prompts": [
                "Find onboarding flows from fintech apps",
                "Show how Linear handles empty states",
                "Extract spacing and typography from SaaS settings pages",
            ],
        },
        "mobbin": {
            "description": "Mobile and web UI pattern library via official HTTP MCP (paid Pro plan)",
            "docs": "https://mobbin.com",
            "endpoint": "https://api.mobbin.com/mcp",
            "requires": "Mobbin Pro (~€10/month) + browser OAuth on first call",
            "auto_merge": True,
            "cursor_name": "mobbin",
            "skill": "decisions-design-references",
            "mcp": {
                "url": "https://api.mobbin.com/mcp",
            },
            "setup_commands": {
                "claude_code": (
                    "claude mcp add mobbin --scope user --transport http https://api.mobbin.com/mcp"
                ),
                "cursor_mcp_json": {
                    "mobbin": {
                        "url": "https://api.mobbin.com/mcp",
                        "transport": "http",
                    }
                },
            },
        },
        "aceternity_ui": {
            "description": "Copy-paste React + Tailwind + Framer Motion components (no MCP)",
            "docs": "https://ui.aceternity.com",
            "skill": "decisions-design-references",
            "usage": "Browse component pages; paste code or give the agent a direct component URL",
        },
        "godly": {
            "description": "Curated visual gallery — no API or MCP",
            "docs": "https://godly.website",
            "skill": "decisions-design-references",
            "usage": "Attach screenshots or links as reference images in the chat before implementing",
        },
        "design_references_unknown_10x": {
            "description": (
                "“10x” from social reels is unidentified — not a known fifth design MCP alongside "
                "Refero/Mobbin/Aceternity/Godly. Treat as slang unless the user names a specific product."
            ),
            "status": "unresolved",
        },
    }


def _mcp_setup_script_text() -> str:
    return """#!/usr/bin/env bash
# DecisionsAI — optional design-reference MCP setup (run manually; requires paid plans where noted)
set -euo pipefail

echo "Refero MCP: upgrade at https://refero.design/mcp then follow signed-in install steps."
echo "Refero agent skill (optional): npx skills add https://github.com/referodesign/refero_skill"
echo ""

if command -v claude >/dev/null 2>&1; then
  echo "Adding Mobbin MCP for Claude Code (OAuth in browser on first use)..."
  claude mcp add mobbin --scope user --transport http https://api.mobbin.com/mcp 2>/dev/null || \\
    echo "  (Mobbin may already be configured — check: claude mcp list)"
else
  echo "Claude Code not found — skip Mobbin CLI setup or add https://api.mobbin.com/mcp to ~/.cursor/mcp.json"
fi

echo ""
echo "Aceternity UI: no MCP — use https://ui.aceternity.com component URLs in prompts."
echo "Godly: no MCP — attach screenshots from https://godly.website"
echo "Done. See ~/.decisions/harness/mcp-recommendations.json for JSON snippets."
"""


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _projection_text(*, harness: str) -> str:
    script = _mcp_setup_script_path(Path.home())
    return f"""---
name: decisions-design-reference-harness
description: Design reference harness for {harness} — Refero MCP, Mobbin MCP, Aceternity UI, Godly gallery, and UI ideation workflow.
---

# DecisionsAI Design Reference Harness

Use **decisions-ui-ideation** before writing UI code. Use **decisions-design-references** for source-specific playbooks.

MCP setup script: `{script}`

Full JSON catalog: `~/.decisions/harness/mcp-recommendations.json` (refero, mobbin, aceternity_ui, godly keys).

Paid MCPs (Refero Pro, Mobbin Pro) need your subscription — Decisions only documents and projects skills, it does not provision accounts.
"""


def ensure_design_reference_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
) -> dict[str, Any]:
    base_home = Path(home).expanduser() if home is not None else Path.home()
    detected = detected_harnesses()
    sources = {
        skill_id: LOCAL_SKILLS / skill_id
        for skill_id in LOCAL_DESIGN_SKILLS
        if (LOCAL_SKILLS / skill_id).is_dir()
    }
    from distr.core.plugins import ecc_vendor_dir

    for skill_id in ECC_DESIGN_SKILLS:
        path = ecc_vendor_dir() / "skills" / skill_id
        if path.is_dir():
            sources[skill_id] = path

    written = install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=sources,
        also_commands=True,
    )

    for harness, path in projection_paths(base_home, detected, "decisions-design-reference-harness").items():
        if write_projection_skill(path, _projection_text(harness=harness)):
            written.append(str(path))

    script_path = _mcp_setup_script_path(base_home)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_text = _mcp_setup_script_text()
    if not script_path.exists() or script_path.read_text(encoding="utf-8") != script_text:
        script_path.write_text(script_text, encoding="utf-8")
        script_path.chmod(0o755)
        written.append(str(script_path))

    # MCP catalog is owned by distr.core.mcp_harness (recalibrated on harness stack start)

    payload = {
        "state_version": STATE_VERSION,
        "status": "configured",
        "detected": detected,
        "written": written,
        "mcp_setup_script": str(script_path),
        "refero_skill_install": "npx skills add https://github.com/referodesign/refero_skill",
    }
    _write_json(_state_path(base_home), payload)
    return payload


def ensure_design_reference_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_DESIGN_REFERENCE_SETUP") or "").strip() == "1":
        return
    try:
        ensure_design_reference_setup(run_full=False)
    except Exception:
        pass
