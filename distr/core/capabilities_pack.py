"""Browser, media, and content-creation harness bootstrap for all IDEs/CLIs."""

from __future__ import annotations

import hashlib
import re
import importlib.metadata as package_metadata
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
from distr.core.plugins import community_skills_dir, ecc_vendor_dir, project_root

PROJECT_ROOT = project_root()
ECC_SKILLS = ecc_vendor_dir() / "skills"
LOCAL_SKILLS = PROJECT_ROOT / "skills"
STATE_VERSION = 2
BROWSER_USE_VERSION = "0.11.13"

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
    "decisions-computer-use",
    "decisions-browser-stack",
    "decisions-harness-stack",
)

# Craft + audit stay on the UI pre-chain. The rest are installed and explicit.
_DESIGN_SKILL_IDS: tuple[str, ...] = (
    "impeccable",
    "web-design-guidelines",
    "frontend-design",
    "web-artifacts-builder",
    "perfect-ui",
    "design-taste-frontend",
    "redesign-existing-projects",
    "minimalist-ui",
    "industrial-brutalist-ui",
    "high-end-visual-design",
    "image-to-code",
    "stitch-design-taste",
    "design-taste",
    "emil-design-eng",
    "animate",
    "review-animations",
    "improve-animations",
    "apple-design",
    "pick-ui-library",
    "prototype",
    "find-animation-opportunities",
    "animation-vocabulary",
    "break-ui",
    "mobile-native",
    "ui-ux-pro-max",
    "vercel-react-best-practices",
    "vercel-composition-patterns",
    "bencium-controlled-ux-designer",
    "bencium-innovative-ux-designer",
    "bencium-impact-designer",
    "design-audit",
    "ui-typography",
    "relationship-design",
    "accessibility-audit",
    "accessibility-diff",
    "accessibility-fix",
    "accessibility-inspect",
    "accessibility-scan",
    "frontend-stack",
)


def _home_skill_candidates(skill_id: str) -> tuple[str, ...]:
    return (
        f".agents/skills/{skill_id}",
        f".codex/skills/{skill_id}",
        f".claude/skills/{skill_id}",
        f".cursor/skills/{skill_id}",
    )


EXTERNAL_SKILL_CANDIDATES: dict[str, tuple[str, ...]] = {
    skill_id: _home_skill_candidates(skill_id) for skill_id in _DESIGN_SKILL_IDS
}

# First match wins. One lead, then at most two supports. Reasons are said to the agent.
_DESIGN_RECOMMENDATIONS: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        ("wcag", "accessibility", "screen reader", "contrast"),
        ("accessibility-audit", "web-design-guidelines"),
        "We will use accessibility-audit and web-design-guidelines because this is an accessibility check: AccessLint finds the violations, and web-design-guidelines checks focus, labels, and contrast.",
    ),
    (
        ("animation", "motion", "transition"),
        ("animate", "review-animations"),
        "We will use animate and review-animations because this is motion work: animate decides whether it should move and how, and review-animations checks the result.",
    ),
    (
        ("brutalist",),
        ("industrial-brutalist-ui", "impeccable"),
        "We will use industrial-brutalist-ui and impeccable because the request asks for a brutalist look, and impeccable keeps the interface workable.",
    ),
    (
        ("minimalist",),
        ("minimalist-ui", "impeccable"),
        "We will use minimalist-ui and impeccable because the request asks for a minimalist look, and impeccable keeps hierarchy and type honest.",
    ),
    (
        ("landing page", "portfolio", "marketing page", "hero section"),
        ("design-taste-frontend", "perfect-ui", "frontend-design"),
        "We will use design-taste-frontend, perfect-ui, and frontend-design because this is a marketing page: Taste sets the direction, Perfect UI is built for that surface, and frontend-design keeps it from looking templated.",
    ),
    (
        ("dashboard", "settings", "data table"),
        ("impeccable", "web-design-guidelines", "frontend-design"),
        "We will use impeccable, web-design-guidelines, and frontend-design because this is product UI: Impeccable shapes the screen, web-design-guidelines checks the table, focus, and labels, and frontend-design sets the visual direction.",
    ),
    (
        ("redesign", "looks generic", "ai slop"),
        ("impeccable", "redesign-existing-projects", "web-design-guidelines"),
        "We will use impeccable, redesign-existing-projects, and web-design-guidelines because this is a redesign: Impeccable critiques what is there, the redesign skill raises the quality, and web-design-guidelines checks the interface still holds.",
    ),
    (
        ("font pairing", "palette", "chart type"),
        ("ui-ux-pro-max",),
        "We will use ui-ux-pro-max because this needs a lookup: palettes, font pairings, chart types, or stack rules.",
    ),
    (
        ("frontend", "interface", "layout", "component", "css"),
        ("impeccable", "web-design-guidelines"),
        "We will use impeccable and web-design-guidelines because this is interface work: Impeccable decides how the screen should look, and web-design-guidelines checks focus, labels, and structure.",
    ),
)


def _mentions(text: str, phrase: str) -> bool:
    parts = [re.escape(part) for part in phrase.split()]
    return bool(re.search(rf"(?<!\w){' '.join(parts)}(?!\w)", text))


def recommend_design_skills(text: str) -> tuple[list[str], str]:
    """Return up to three design skills and the reason to say before using them."""
    lowered = re.sub(r"[-_/]+", " ", str(text or "").lower())
    if not lowered.strip():
        return [], ""
    from distr.core.harness.intake import classify_intake

    ui_work = bool(classify_intake(lowered).get("ui_heavy"))
    generic = ("frontend", "interface", "layout", "component", "css")
    for phrases, skill_ids, reason in _DESIGN_RECOMMENDATIONS:
        if not any(_mentions(lowered, phrase) for phrase in phrases):
            continue
        if phrases == generic and not ui_work:
            return [], ""
        return list(skill_ids), reason
    return [], ""


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "capabilities-pack-state.json"


def _registry_cache_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "capabilities-skills-registry.json"


def _mcp_recommendations_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "mcp-recommendations.json"


def _skill_sources(*, home: Path | None = None) -> dict[str, Path]:
    base_home = Path(home).expanduser() if home is not None else Path.home()
    sources: dict[str, Path] = {}
    for skill_id in BROWSER_CONTENT_ECC_SKILLS:
        path = ECC_SKILLS / skill_id
        if path.is_dir():
            sources[skill_id] = path
    for skill_id in LOCAL_HARNESS_SKILLS:
        path = LOCAL_SKILLS / skill_id
        if path.is_dir():
            sources[skill_id] = path
    pack = community_skills_dir()
    for skill_id, candidates in EXTERNAL_SKILL_CANDIDATES.items():
        vendored = pack / skill_id
        if (vendored / "SKILL.md").is_file():
            sources[skill_id] = vendored
            continue
        for candidate in candidates:
            path = base_home / candidate
            if (path / "SKILL.md").is_file():
                sources[skill_id] = path
                break
    return sources


def _fingerprint(detected: dict[str, bool], sources: dict[str, Path]) -> str:
    payload = {
        "state_version": STATE_VERSION,
        "skill_ids": sorted(sources),
        "detected": detected,
        "ecc_mtime": ECC_SKILLS.stat().st_mtime if ECC_SKILLS.is_dir() else 0,
        "source_mtimes": {
            skill_id: (path / "SKILL.md").stat().st_mtime
            for skill_id, path in sources.items()
            if (path / "SKILL.md").is_file()
        },
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

- **UI quality:** on interface work, say which skills you will use and why, then read them. One lead and at most two supports. Product screens: impeccable, then web-design-guidelines. Marketing pages: design-taste-frontend and perfect-ui. Accessibility: accessibility-audit. Motion: animate. Do not load the whole design pack at once.
- **Browser / QA:** browser-qa, webapp-testing, e2e-testing, decisions-playwright
- **Native / cross-app control:** decisions-computer-use when the active runtime exposes computer-use tools
- **Video / motion:** video-editing, remotion-video-creation, manim-video, videodb
- **Content:** content-engine, article-writing, brand-voice, crosspost, social-publisher, marketing-campaign
- **Media APIs:** fal-ai-media (configure MCP — see `{_mcp_recommendations_path(Path.home())}`)
- **Stack index:** decisions-harness-stack, decisions-browser-stack

ECC source skills: {skill_list}

Local Decisions skills: {local_list}

Registry cache: `{registry_path}`

## Runtime tools (Decisions server)

- **Playwright:** `playwright_browser` tool + workflow `playwright` steps. Chromium installed by `bin/setup.py`.
- **Computer use:** runtime-gated native/browser control. Use `decisions-computer-use`; do not assume the tool exists in every harness.
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
        "computer_use": {
            "description": "Runtime-gated native app and cross-app computer control",
            "setup": "Use the computer-use/CUA tool exposed by the active harness; no MCP is installed by Decisions",
            "skill": "decisions-computer-use",
            "status": "runtime_capability",
        },
        "impeccable": {
            "description": "UI design, refinement, audit, and polish skill set",
            "setup": "Install at ~/.agents/skills/impeccable or ~/.codex/skills/impeccable; Decisions projects it into detected harnesses",
            "skill": "impeccable",
            "status": "external_skill",
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

        browser_use_version = package_metadata.version("browser-use")
        pillow_major = int(package_metadata.version("Pillow").split(".", 1)[0])
        if browser_use_version == BROWSER_USE_VERSION and pillow_major < 12:
            return {
                "installed": True,
                "method": "existing",
                "version": browser_use_version,
            }
    except ImportError:
        pass
    except (package_metadata.PackageNotFoundError, TypeError, ValueError):
        pass
    try:
        # Browser Use 0.13 installs browser-harness, whose Pillow 12 pin also
        # conflicts with Pipecat. It is not a dependency of the supported line.
        try:
            package_metadata.version("browser-harness")
        except package_metadata.PackageNotFoundError:
            pass
        else:
            subprocess.run(
                [sys.executable, "-m", "pip", "uninstall", "-y", "browser-harness"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                f"browser-use=={BROWSER_USE_VERSION}",
                "Pillow>=11.2.1,<12",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        ok = result.returncode == 0
        if ok:
            try:
                ok = (
                    package_metadata.version("browser-use") == BROWSER_USE_VERSION
                    and int(package_metadata.version("Pillow").split(".", 1)[0]) < 12
                )
            except (package_metadata.PackageNotFoundError, TypeError, ValueError):
                ok = False
        return {
            "installed": ok,
            "method": "pip",
            "version": package_metadata.version("browser-use") if ok else "",
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:400],
        }
    except Exception as exc:
        return {"installed": False, "reason": str(exc)}


def default_browser_content_pre_chain() -> list[str]:
    return ["decisions-harness-stack"]


def merge_browser_content_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    from distr.core.competition_pack import merge_competition_pre_chain

    merged = merge_competition_pre_chain(skill_ids, project_folder=project_folder)
    blob = " ".join(merged).lower()
    routed: list[str] = []
    recommended, _reason = recommend_design_skills(blob)
    if recommended:
        routed.extend(recommended)
        routed.append("decisions-playwright")
    elif any(token in blob for token in ("ui", "frontend", "design", "css", "visual", "polish")):
        routed.extend(["impeccable", "web-design-guidelines", "decisions-playwright"])
    elif any(token in blob for token in ("browser", "playwright", "e2e", "webapp")):
        routed.append("decisions-playwright")
    if any(token in blob for token in ("computer use", "computer-use", "native app", "cross-app")):
        routed.append("decisions-computer-use")
    baseline = [*default_browser_content_pre_chain(), *routed]
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
    """Merge the default infrastructure and intent-routed skills for every workflow."""
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
    sources = _skill_sources(home=base_home)
    skill_ids = sorted(sources.keys())
    fingerprint = _fingerprint(detected, sources)
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
        rows = []
        for skill_id, path in sources.items():
            if skill_id in EXTERNAL_SKILL_CANDIDATES and not path.is_relative_to(ECC_SKILLS):
                source = (
                    "community_vendor"
                    if path.is_relative_to(community_skills_dir())
                    else "external"
                )
            elif skill_id in BROWSER_CONTENT_ECC_SKILLS:
                source = "ecc_vendor"
            elif skill_id in LOCAL_HARNESS_SKILLS:
                source = "local"
            else:
                source = "local"
            rows.append({"id": skill_id, "path": str(path), "source": source})
        _write_json(registry_path, rows)
        _write_json(_mcp_recommendations_path(base_home), _mcp_recommendations())
        try:
            from distr.core.skills.catalog import load_registry

            load_registry.cache_clear()
        except Exception:
            pass

    written = install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=sources,
        also_commands=True,
    )

    harness_text = _projection_text(harness="codex", registry_path=registry_path)
    for harness, path in projection_paths(base_home, detected, "decisions-browser-content-harness").items():
        text = _projection_text(harness=harness, registry_path=registry_path)
        if write_projection_skill(path, text):
            written.append(str(path))
    if (base_home / ".codex").is_dir():
        codex_skill = base_home / ".codex" / "skills" / "decisions-browser-content-harness" / "SKILL.md"
        if write_projection_skill(codex_skill, harness_text):
            written.append(str(codex_skill))

    # Cursor rules stub for browser QA visibility
    if detected.get("cursor"):
        rule_path = base_home / ".cursor" / "rules" / "decisions-browser-content.mdc"
        rule_text = (
            "---\n"
            "description: DecisionsAI browser, Playwright, and content-creation harness skills are installed.\n"
            "globs:\n"
            "alwaysApply: true\n"
            "---\n\n"
            "When the task is interface work, name the design skills you will use and why before editing. One lead, at most two supports. Product UI: impeccable and web-design-guidelines. Marketing pages: design-taste-frontend and perfect-ui. Accessibility: accessibility-audit. Motion: animate.\n"
            "For native or cross-app control use decisions-computer-use only when the runtime exposes computer-use tools.\n"
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
