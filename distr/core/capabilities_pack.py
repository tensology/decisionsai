"""Browser, media, and content-creation harness bootstrap for all IDEs/CLIs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from distr.core.harness_bootstrap import (
    detected_harnesses,
    install_skills_to_harnesses,
    projection_paths,
    write_projection_skill,
)
from distr.core.plugins import ecc_vendor_dir, project_root

PROJECT_ROOT = project_root()
ECC_SKILLS = ecc_vendor_dir() / "skills"
LOCAL_SKILLS = PROJECT_ROOT / "skills"
STATE_VERSION = 1

# ECC skills for browser QA, Playwright, video, Remotion, and content pipelines.
BROWSER_CONTENT_ECC_SKILLS: tuple[str, ...] = (
    "browser-qa",
    "webapp-testing",
    "e2e-testing",
    "video-editing",
    "remotion-video-creation",
    "manim-video",
    "videodb",
    "content-engine",
    "article-writing",
    "brand-voice",
    "crosspost",
    "social-publisher",
    "marketing-campaign",
    "fal-ai-media",
    "pixazo-media",
    "frontend-design",
    "strategic-compact",
)

# Decisions-native skills (repo skills/).
LOCAL_HARNESS_SKILLS: tuple[str, ...] = (
    "decisions-playwright",
    "decisions-browser-stack",
    "decisions-harness-stack",
)


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "capabilities-pack-state.json"


def _registry_cache_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "capabilities-skills-registry.json"


def _mcp_recommendations_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "mcp-recommendations.json"


def _skill_sources() -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for skill_id in BROWSER_CONTENT_ECC_SKILLS:
        path = ECC_SKILLS / skill_id
        if path.is_dir():
            sources[skill_id] = path
    for skill_id in LOCAL_HARNESS_SKILLS:
        path = LOCAL_SKILLS / skill_id
        if path.is_dir():
            sources[skill_id] = path
    return sources


def _fingerprint(detected: dict[str, bool], skill_ids: list[str]) -> str:
    payload = {
        "state_version": STATE_VERSION,
        "skill_ids": sorted(skill_ids),
        "detected": detected,
        "ecc_mtime": ECC_SKILLS.stat().st_mtime if ECC_SKILLS.is_dir() else 0,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _projection_text(*, harness: str, registry_path: Path) -> str:
    skill_list = ", ".join(BROWSER_CONTENT_ECC_SKILLS[:8]) + ", …"
    local_list = ", ".join(LOCAL_HARNESS_SKILLS)
    return f"""---
name: decisions-browser-content-harness
description: Browser automation, Playwright, browser-use, video/Remotion, and content-creation skills for DecisionsAI workflows across {harness}.
---

# DecisionsAI Browser & Content Harness

DecisionsAI installs browser, media, and content skills into this harness so they work with
Hermes workflows, Codex, Cursor, Claude, and Pi without hunting the ECC tree.

## Installed skill families

- **Browser / QA:** browser-qa, webapp-testing, e2e-testing, decisions-playwright
- **Video / motion:** video-editing, remotion-video-creation, manim-video, videodb
- **Content:** content-engine, article-writing, brand-voice, crosspost, social-publisher, marketing-campaign
- **Media APIs:** fal-ai-media (configure MCP — see `{_mcp_recommendations_path(Path.home())}`)
- **Stack index:** decisions-harness-stack, decisions-browser-stack

ECC source skills: {skill_list}

Local Decisions skills: {local_list}

Registry cache: `{registry_path}`

## Runtime tools (Decisions server)

- **Playwright:** `playwright_browser` tool + workflow `playwright` steps. Chromium installed by `bin/setup.py`.
- **browser-use:** Python package in Decisions venv when setup runs. Use for agentic browser loops; fall back to Playwright scripts.
- **RTK:** compresses shell output (git, tests) — install via `scripts/setup_project_clis.sh rtk`.
- **Remotion:** per-project `npm install` — skills guide composition; no global Remotion required.
- **Higgsfield:** no native integration yet — use fal-ai-media or manual export until a dedicated skill ships.

## Workflow usage

Loop presets (article-from-ticket, polish-verify-and-ship, e2e-until-green) already reference
these skills. Workflow `pre_chain` also receives ponytail + fallow from the competition pack.

When completing a browser or content step, cite which skill you followed and attach evidence
(screenshots, audit JSON, draft paths) in the result packet.
"""


def _mcp_recommendations() -> dict[str, Any]:
    return {
        "fal_ai_media": {
            "description": "Image, video, and audio generation via fal.ai",
            "mcp": {
                "command": "npx",
                "args": ["-y", "fal-ai-mcp-server"],
                "env": {"FAL_KEY": "YOUR_FAL_KEY_HERE"},
            },
            "docs": "https://fal.ai",
            "skill": "fal-ai-media",
        },
        "pixazo_media": {
            "description": "Image, video, TTS, and music via Pixazo (one API key)",
            "mcp": {
                "url": "https://gateway.pixazo.ai/pixazo/mcp",
            },
            "docs": "https://www.pixazo.ai/models/mcp",
            "skill": "pixazo-media",
        },
        "playwright": {
            "description": "Decisions Hermes playwright_browser tool + workflow playwright steps",
            "setup": "bin/setup.py installs playwright + chromium in the Decisions venv",
            "skill": "decisions-playwright",
        },
        "browser_use": {
            "description": "Agentic browser automation (Python)",
            "setup": "pip install browser-use (Decisions setup.py)",
            "skill": "browser-qa",
        },
        "higgsfield": {
            "description": "Not bundled — use fal-ai-media or export manually until Decisions adds a Higgsfield skill",
            "status": "planned_external",
        },
    }


def _ensure_playwright_browsers() -> dict[str, Any]:
    if not shutil.which(sys.executable):
        return {"ok": False, "reason": "python missing"}
    try:
        import playwright  # noqa: F401
    except ImportError:
        return {"ok": False, "reason": "playwright not installed in venv"}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        return {"ok": result.returncode == 0, "returncode": result.returncode}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def _ensure_browser_use_package(*, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"installed": False, "reason": "skipped"}
    try:
        import browser_use  # noqa: F401

        return {"installed": True, "method": "existing"}
    except ImportError:
        pass
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "browser-use"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        ok = result.returncode == 0
        if ok:
            try:
                import browser_use  # noqa: F401

                ok = True
            except ImportError:
                ok = False
        return {
            "installed": ok,
            "method": "pip",
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:400],
        }
    except Exception as exc:
        return {"installed": False, "reason": str(exc)}


def default_browser_content_pre_chain() -> list[str]:
    return ["decisions-harness-stack", "browser-qa", "decisions-playwright"]


def merge_browser_content_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    from distr.core.competition_pack import merge_competition_pre_chain

    merged = merge_competition_pre_chain(skill_ids, project_folder=project_folder)
    baseline = default_browser_content_pre_chain()
    out: list[str] = []
    seen: set[str] = set()
    for skill_id in [*baseline, *merged]:
        key = str(skill_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    from distr.core.design_reference_pack import merge_design_reference_pre_chain

    chain = merge_design_reference_pre_chain(out, project_folder=project_folder)
    from distr.core.agent_reach_pack import merge_agent_reach_pre_chain

    return merge_agent_reach_pre_chain(chain, project_folder=project_folder)


def merge_harness_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    """Full workflow pre_chain merge: competition → browser → design → agent-reach → community."""
    chain = merge_browser_content_pre_chain(skill_ids, project_folder=project_folder)
    from distr.core.community_skills_pack import merge_community_pre_chain

    chain = merge_community_pre_chain(chain, project_folder=project_folder)
    from distr.core.yt_dlp_pack import merge_ytdlp_pre_chain

    chain = merge_ytdlp_pre_chain(chain, project_folder=project_folder)
    from distr.core.composio_pack import merge_composio_pre_chain

    chain = merge_composio_pre_chain(chain, project_folder=project_folder)
    from distr.core.visual_plan_pack import merge_visual_plan_pre_chain

    return merge_visual_plan_pre_chain(chain)


def ensure_capabilities_pack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
    install_browser_use: bool = True,
) -> dict[str, Any]:
    base_home = Path(home).expanduser() if home is not None else Path.home()
    detected = detected_harnesses()
    sources = _skill_sources()
    skill_ids = sorted(sources.keys())
    fingerprint = _fingerprint(detected, skill_ids)
    registry_path = _registry_cache_path(base_home)

    state_path = _state_path(base_home)
    skip_heavy = False
    if not run_full and state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("fingerprint") == fingerprint and registry_path.is_file():
                skip_heavy = True
        except Exception:
            pass

    if not skip_heavy:
        rows = [
            {
                "id": skill_id,
                "path": str(path),
                "source": "ecc_vendor" if skill_id in BROWSER_CONTENT_ECC_SKILLS else "local",
            }
            for skill_id, path in sources.items()
        ]
        _write_json(registry_path, rows)
        _write_json(_mcp_recommendations_path(base_home), _mcp_recommendations())

    written = install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=sources,
        also_commands=True,
    )

    for harness, path in projection_paths(base_home, detected, "decisions-browser-content-harness").items():
        if write_projection_skill(path, _projection_text(harness=harness, registry_path=registry_path)):
            written.append(str(path))

    # Cursor rules stub for browser QA visibility
    if detected.get("cursor"):
        rule_path = base_home / ".cursor" / "rules" / "decisions-browser-content.mdc"
        rule_text = (
            "---\n"
            "description: DecisionsAI browser, Playwright, and content-creation harness skills are installed.\n"
            "globs:\n"
            "alwaysApply: false\n"
            "---\n\n"
            "For web QA use skills browser-qa, webapp-testing, e2e-testing, or decisions-playwright.\n"
            "For video/content use video-editing, remotion-video-creation, content-engine, article-writing.\n"
            "For generated media configure fal-ai MCP (see ~/.decisions/harness/mcp-recommendations.json).\n"
        )
        if write_projection_skill(rule_path, rule_text):
            written.append(str(rule_path))

    playwright = _ensure_playwright_browsers() if run_full else {"ok": True, "skipped": True}
    browser_use = _ensure_browser_use_package(enabled=install_browser_use and run_full)

    status = "current" if skip_heavy and not written else "configured"
    payload = {
        "state_version": STATE_VERSION,
        "status": status,
        "detected": detected,
        "fingerprint": fingerprint,
        "registry_path": str(registry_path),
        "skill_count": len(skill_ids),
        "written": written,
        "playwright": playwright,
        "browser_use": browser_use,
    }
    _write_json(state_path, payload)
    return payload


def ensure_capabilities_pack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_CAPABILITIES_PACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_capabilities_pack_setup(run_full=False)
    except Exception:
        pass
