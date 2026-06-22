"""Read-only assessment for Decisions harness setup and projections."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from distr.core.harness_bootstrap import detected_harnesses, projection_paths
from distr.core.plugins import CODEX_PLUGIN_NAME, project_root as default_project_root
from distr.core.project_cli_backends import get_backend_statuses


REFERENCE_SKILLS = (
    "decisions-frontier-prep",
    "decisions-harness-audit",
    "decisions-harness-optimize",
    "codebase-design",
    "domain-modeling",
    "architecture-deepening-review",
)

SETUP_COMMAND = "python3 bin/setup.py"
HARNESS_REPAIR_COMMAND = (
    "python3 -c \"from distr.core.harness_stack import ensure_harness_stack_setup; "
    "ensure_harness_stack_setup(run_full=True)\""
)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _row(
    *,
    item_id: str,
    name: str,
    state_path: Path | None = None,
    required_paths: list[Path] | None = None,
    repair_command: str = SETUP_COMMAND,
    category: str = "pack",
) -> dict[str, Any]:
    required = list(required_paths or [])
    missing_paths = [str(path) for path in required if not path.exists()]
    state_exists = bool(state_path and state_path.is_file()) if state_path else True
    if state_path and not state_exists:
        missing_paths.insert(0, str(state_path))

    ready = state_exists and not missing_paths
    if ready:
        state = "ready"
    elif state_exists and len(missing_paths) < len(required) + (1 if state_path else 0):
        state = "partial"
    else:
        state = "missing"

    return {
        "id": item_id,
        "name": name,
        "category": category,
        "ready": ready,
        "state": state,
        "state_path": str(state_path) if state_path else "",
        "state_payload": _read_json(state_path) if state_path else None,
        "required_paths": [str(path) for path in required],
        "missing_paths": missing_paths,
        "repair_command": "" if ready else repair_command,
    }


def _detected_with_homes(home: Path) -> dict[str, bool]:
    detected = dict(detected_harnesses())
    return {
        "codex": bool(detected.get("codex") or shutil.which("codex") or (home / ".codex").exists() or (home / ".agents").exists()),
        "claude": bool(detected.get("claude") or shutil.which("claude") or (home / ".claude").exists()),
        "cursor": bool(detected.get("cursor") or shutil.which("cursor") or shutil.which("cursor-agent") or (home / ".cursor").exists()),
        "pi": bool(detected.get("pi") or shutil.which("pi") or (home / ".pi").exists()),
        "cline": bool(detected.get("cline") or shutil.which("cline") or (home / ".cline").exists()),
        "gemini": bool(shutil.which("gemini") or (home / ".gemini").exists()),
        "rtk": bool(shutil.which("rtk")),
    }


def _pack_rows(home: Path) -> dict[str, dict[str, Any]]:
    harness_dir = home / ".decisions" / "harness"
    pack_specs = [
        (
            "ecc",
            "ECC harness pack",
            home / ".decisions" / "harness-pack-state.json",
            [harness_dir / "ecc-skills-registry.json", harness_dir / "ecc-surface-manifest.json"],
        ),
        (
            "competition",
            "Competition harness pack",
            home / ".decisions" / "competition-pack-state.json",
            [harness_dir / "competition-skills-registry.json"],
        ),
        (
            "capabilities",
            "Capabilities harness pack",
            home / ".decisions" / "capabilities-pack-state.json",
            [harness_dir / "capabilities-skills-registry.json", harness_dir / "mcp-recommendations.json"],
        ),
        (
            "design_references",
            "Design reference pack",
            home / ".decisions" / "design-reference-pack-state.json",
            [],
        ),
        (
            "agent_reach",
            "Agent reach pack",
            home / ".decisions" / "agent-reach-pack-state.json",
            [harness_dir / "agent-reach-skills-registry.json"],
        ),
        (
            "community_skills",
            "Community skills pack",
            home / ".decisions" / "community-skills-pack-state.json",
            [],
        ),
        (
            "visual_plan",
            "Visual plan pack",
            home / ".decisions" / "visual-plan-pack-state.json",
            [],
        ),
        (
            "yt_dlp",
            "yt-dlp reference pack",
            home / ".decisions" / "yt-dlp-pack-state.json",
            [],
        ),
        (
            "composio",
            "Composio pack",
            home / ".decisions" / "composio-pack-state.json",
            [],
        ),
        (
            "mcp",
            "MCP harness catalog",
            home / ".decisions" / "mcp-harness-state.json",
            [harness_dir / "mcp-recommendations.json"],
        ),
    ]
    return {
        item_id: _row(
            item_id=item_id,
            name=name,
            state_path=state_path,
            required_paths=required,
            repair_command=HARNESS_REPAIR_COMMAND,
            category="pack",
        )
        for item_id, name, state_path, required in pack_specs
    }


def _reference_skill_paths(home: Path, harness_id: str) -> list[Path]:
    if harness_id == "codex":
        return [home / ".codex" / "commands" / f"{skill_id}.md" for skill_id in REFERENCE_SKILLS]
    if harness_id == "claude":
        return [home / ".claude" / "commands" / f"{skill_id}.md" for skill_id in REFERENCE_SKILLS]
    if harness_id == "cursor":
        return [home / ".cursor" / "commands" / f"{skill_id}.md" for skill_id in REFERENCE_SKILLS]
    if harness_id == "pi":
        return [home / ".pi" / "skills" / skill_id / "SKILL.md" for skill_id in REFERENCE_SKILLS]
    if harness_id == "cline":
        return [home / ".cline" / "skills" / skill_id / "SKILL.md" for skill_id in REFERENCE_SKILLS]
    if harness_id == "gemini":
        return [home / ".gemini" / "commands" / f"{skill_id}.md" for skill_id in REFERENCE_SKILLS]
    return []


def _projection_rows(home: Path, harness_id: str, detected: dict[str, bool]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for projection_id, skill_name in (
        ("ecc", "ecc-harness-pack"),
        ("competition", "decisions-competition-harness"),
        ("capabilities", "decisions-browser-content-harness"),
    ):
        paths = projection_paths(home, detected, skill_name)
        required = [paths[harness_id]] if harness_id in paths else []
        rows[projection_id] = _row(
            item_id=projection_id,
            name=f"{projection_id} projection",
            required_paths=required,
            repair_command=HARNESS_REPAIR_COMMAND,
            category="projection",
        )

    rows["reference_skills"] = _row(
        item_id="reference_skills",
        name="Reference skill commands",
        required_paths=_reference_skill_paths(home, harness_id),
        repair_command=HARNESS_REPAIR_COMMAND,
        category="projection",
    )
    return rows


def _cli_statuses() -> dict[str, dict[str, Any]]:
    try:
        payload = get_backend_statuses()
    except Exception as exc:
        return {
            "error": {
                "id": "error",
                "name": "CLI backend registry",
                "ready": False,
                "state": "error",
                "message": str(exc),
            }
        }
    return {str(row.get("id") or ""): row for row in payload.get("backends") or [] if row.get("id")}


def _setup_command_for_harness(harness_id: str) -> str:
    if harness_id == "claude":
        return "NONINTERACTIVE=1 bash scripts/setup_project_clis.sh claude"
    if harness_id in {"codex", "cursor", "pi", "cline", "rtk"}:
        return f"NONINTERACTIVE=1 bash scripts/setup_project_clis.sh {harness_id}"
    if harness_id == "gemini":
        return "NONINTERACTIVE=1 bash scripts/setup_project_clis.sh all"
    return SETUP_COMMAND


def _harness_rows(home: Path, detected: dict[str, bool], clis: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cli_by_harness = {
        "codex": "codex",
        "claude": "claude_code",
        "cursor": "cursor",
        "pi": "pi",
        "cline": "cline",
        "gemini": "gemini",
        "rtk": "rtk",
    }
    rows: dict[str, dict[str, Any]] = {}
    for harness_id in ("codex", "claude", "cursor", "pi", "cline", "gemini", "rtk"):
        cli_id = cli_by_harness[harness_id]
        cli = clis.get(cli_id, {})
        projections = {} if harness_id == "rtk" else _projection_rows(home, harness_id, detected)
        detected_now = bool(detected.get(harness_id))
        cli_ready = bool(cli.get("ready")) if cli else detected_now
        projection_ready = all(row.get("ready") for row in projections.values()) if projections else detected_now
        ready = detected_now and cli_ready and projection_ready
        rows[harness_id] = {
            "id": harness_id,
            "name": harness_id.upper() if harness_id == "rtk" else harness_id.title(),
            "detected": detected_now,
            "ready": ready,
            "state": "ready" if ready else ("partial" if detected_now else "missing"),
            "cli_id": cli_id,
            "cli": cli,
            "projections": projections,
            "repair_command": "" if ready else _setup_command_for_harness(harness_id),
        }
    return rows


def _summarize(packs: dict[str, Any], harnesses: dict[str, Any], clis: dict[str, Any]) -> dict[str, int]:
    rows: list[dict[str, Any]] = []
    rows.extend(packs.values())
    rows.extend(harnesses.values())
    rows.extend(clis.values())
    ready = sum(1 for row in rows if row.get("ready"))
    stale = sum(1 for row in rows if row.get("state") == "stale")
    missing = sum(1 for row in rows if not row.get("ready"))
    return {"ready": ready, "missing": missing, "stale": stale, "total": len(rows)}


def _repair_actions(packs: dict[str, Any], harnesses: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in list(packs.values()) + list(harnesses.values()):
        if row.get("ready"):
            continue
        command = str(row.get("repair_command") or "").strip()
        if not command:
            continue
        key = (str(row.get("id") or ""), command)
        if key in seen:
            continue
        seen.add(key)
        actions.append(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or row.get("id") or ""),
                "command": command,
                "reason": str(row.get("state") or "missing"),
            }
        )
    return actions


def assess_harness_stack(*, home: Path | None = None, project_root: Path | None = None) -> dict[str, Any]:
    """Return a read-only report for Decisions harness and CLI setup."""
    base_home = Path(home).expanduser() if home else Path.home()
    repo_root = Path(project_root) if project_root else default_project_root()
    detected = _detected_with_homes(base_home)
    clis = _cli_statuses()
    packs = _pack_rows(base_home)
    harnesses = _harness_rows(base_home, detected, clis)
    summary = _summarize(packs, harnesses, clis)
    return {
        "ok": summary["missing"] == 0 and summary["stale"] == 0,
        "generated_by": "decisions-harness-doctor",
        "home": str(base_home),
        "project_root": str(repo_root),
        "detected": detected,
        "summary": summary,
        "packs": packs,
        "harnesses": harnesses,
        "clis": clis,
        "repair_actions": _repair_actions(packs, harnesses),
    }
