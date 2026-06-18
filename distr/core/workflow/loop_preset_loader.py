"""Load loop preset bundles from JSON files under loop_presets/bundles/."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from distr.core.workflow.loop_catalog import infer_loop_archetype
from distr.core.workflow.loop_text import GUARDRAILS_FOOTER
from distr.core.workflow.planning import WORKFLOW_LOOP_MAX_STEPS, parse_loop_contract
from distr.core.workflow.step_harness import (
    ARCHETYPE_SKILL_BUNDLES,
    derive_action_type_from_ui_tools,
)

logger = logging.getLogger(__name__)

BUNDLE_FORMAT = "decisionsai_loop_preset_v1"
BUNDLE_VERSION = "1.0"

_SCOPE_GUARDRAIL = (
    "- Stay on the ticket scope; avoid unrelated refactors\n"
    "- Prefer minimal diffs and evidence over broad rewrites"
)

_PRESETS_ROOT = Path(__file__).resolve().parent / "loop_preset_bundles"
_BUNDLES_DIR = _PRESETS_ROOT / "bundles"
_MANIFEST_PATH = _PRESETS_ROOT / "manifest.json"
_USER_MANIFEST_FILENAME = "manifest.json"


def presets_root() -> Path:
    return _PRESETS_ROOT


def bundles_dir() -> Path:
    return _BUNDLES_DIR


def user_presets_dir() -> Path:
    """Writable directory for user-saved loop presets."""
    path = Path.home() / ".decisions" / "loop_presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _user_manifest_path() -> Path:
    return user_presets_dir() / _USER_MANIFEST_FILENAME


def _load_user_manifest() -> dict[str, Any]:
    path = _user_manifest_path()
    if not path.is_file():
        return {"format_version": BUNDLE_VERSION, "presets": []}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _save_user_manifest(manifest: dict[str, Any]) -> None:
    path = _user_manifest_path()
    with path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _all_manifest_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in _load_manifest().get("presets") or []:
        item = dict(entry)
        item.setdefault("origin", "builtin")
        entries.append(item)
    for entry in _load_user_manifest().get("presets") or []:
        item = dict(entry)
        item["origin"] = "user"
        entries.append(item)
    return entries


def _resolve_bundle_path(entry: dict[str, Any]) -> Path | None:
    slug = str(entry.get("slug") or "").strip()
    origin = str(entry.get("origin") or "builtin").strip().lower()
    if origin == "user":
        candidate = user_presets_dir() / f"{slug}.json"
        return candidate if candidate.is_file() else None
    rel = str(entry.get("file") or "").strip()
    if rel:
        path = (_PRESETS_ROOT / rel).resolve()
        if path.is_file():
            return path
    candidate = _BUNDLES_DIR / f"{slug}.json"
    return candidate if candidate.is_file() else None


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower())
    return slug.strip("-")


def _default_guardrails(loop_contract: dict[str, Any]) -> list[str]:
    items = [line.strip()[2:].strip() for line in GUARDRAILS_FOOTER.splitlines() if line.strip().startswith("- ")]
    for raw in loop_contract.get("guardrails") or []:
        text = str(raw).strip().lstrip("- ").strip()
        if text and text not in items:
            items.append(text)
    return items


def _guardrail_text(loop_contract: dict[str, Any], *, extra: str = "") -> str:
    lines = [_SCOPE_GUARDRAIL]
    if extra:
        lines.append(extra)
    for item in _default_guardrails(loop_contract):
        lines.append(f"- {item}" if not str(item).startswith("-") else str(item))
    return "\n".join(lines)


def _check_tools(check_cmd: str) -> tuple[list[str], str]:
    lower = (check_cmd or "").lower()
    browserish = any(token in lower for token in ("playwright", "e2e", "browser", "cypress", "http://", "https://"))
    if browserish:
        return ["playwright", "browser_use"], ""
    return ["other"], f"Shell check: {check_cmd}"


def _resolve_routing_action(
    step: dict[str, Any],
    *,
    position: int,
    step_count: int,
    pass_key: str,
    goto_key: str,
) -> int | None:
    """Map validation_pass_action / validation_fail_action to goto positions when explicit goto omitted."""
    if goto_key in step:
        return step.get(goto_key)
    action = str(step.get(pass_key) or "").strip().lower()
    if not action:
        return None
    if action in {"next", "next_step", "continue"}:
        return position + 1 if position + 1 < step_count else None
    if action in {"end", "end_loop", "break", "break_loop", "exit"}:
        return None
    if action in {"retry", "retry_loop", "loop", "restart"}:
        return 0
    return None


def normalize_bundle_steps(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert bundle step records into planner/persist step dicts."""
    loop_contract = dict(bundle.get("loop_contract") or {})
    archetype = str(bundle.get("archetype") or infer_loop_archetype(bundle.get("kickoff") or "", loop_contract))
    default_skills = list(ARCHETYPE_SKILL_BUNDLES.get(archetype) or [])
    steps_out: list[dict[str, Any]] = []
    raw_steps = list(bundle.get("steps") or [])
    step_count = len(raw_steps)

    for i, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            continue
        tools = [str(t).strip().lower() for t in (raw.get("tools") or []) if str(t).strip()]
        skills = list(raw.get("skills") or default_skills)
        guardrail = str(raw.get("guardrail") or "").strip()
        if not guardrail:
            guardrail = _guardrail_text(loop_contract)
        failure_checklist = raw.get("failure_checklist")
        if isinstance(failure_checklist, str):
            failure_checklist = [line.strip() for line in failure_checklist.splitlines() if line.strip()]
        elif not isinstance(failure_checklist, list):
            failure_checklist = []

        validation_prompt = str(
            raw.get("validation_prompt") or raw.get("verification") or ""
        ).strip()
        validation_type = str(raw.get("validation_type") or "llm_judgment").strip() or "llm_judgment"
        if not validation_prompt and validation_type == "exit_code":
            validation_prompt = "Command exits 0."

        other_tool = str(raw.get("other_tool") or "").strip()
        cfg: dict[str, Any] = {
            "skills": skills,
            "tools": tools,
            "guardrail": guardrail,
            "model": str(raw.get("model") or "auto"),
            "backend_id": str(raw.get("backend_id") or ""),
        }
        nested_cfg = raw.get("config")
        if isinstance(nested_cfg, dict):
            for key, value in nested_cfg.items():
                if value not in (None, "") and key not in cfg:
                    cfg[key] = value
        if failure_checklist:
            cfg["failure_checklist"] = failure_checklist
        if other_tool:
            cfg["other_tool"] = other_tool
        if raw.get("command"):
            cfg["command"] = raw["command"]
        timeout_seconds = raw.get("timeout_seconds")
        if timeout_seconds not in (None, ""):
            try:
                cfg["timeout_seconds"] = int(timeout_seconds)
            except (TypeError, ValueError):
                pass

        on_pass = _resolve_routing_action(
            raw, position=i, step_count=step_count, pass_key="validation_pass_action", goto_key="on_pass_goto_position"
        )
        on_fail = _resolve_routing_action(
            raw, position=i, step_count=step_count, pass_key="validation_fail_action", goto_key="on_fail_goto_position"
        )

        steps_out.append(
            {
                "title": str(raw.get("name") or raw.get("title") or f"Step {i + 1}"),
                "instruction": str(raw.get("instruction") or "").strip(),
                "action_type": str(raw.get("action_type") or derive_action_type_from_ui_tools(tools)),
                "verification": validation_prompt,
                "validation_type": validation_type,
                "validation_prompt": validation_prompt,
                "wait_for_continue": bool(raw.get("wait_for_continue", False)),
                "routing_mode": str(raw.get("routing_mode") or "static"),
                "config": cfg,
                "timeout_seconds": int(cfg.get("timeout_seconds") or raw.get("timeout_seconds") or 300),
                "on_pass_goto_position": on_pass,
                "on_fail_goto_position": on_fail,
            }
        )

    return steps_out[:WORKFLOW_LOOP_MAX_STEPS]


@lru_cache(maxsize=8)
def _load_manifest_cached(mtime_ns: int) -> dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        return {"format_version": BUNDLE_VERSION, "presets": []}
    with _MANIFEST_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_manifest() -> dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        return {"format_version": BUNDLE_VERSION, "presets": []}
    return _load_manifest_cached(_MANIFEST_PATH.stat().st_mtime_ns)


def _bundle_path_for_slug(slug: str) -> Path | None:
    key = (slug or "").strip().lower()
    if not key:
        return None
    for entry in _all_manifest_entries():
        if str(entry.get("slug") or "").strip().lower() == key:
            path = _resolve_bundle_path(entry)
            if path:
                return path
    user_candidate = user_presets_dir() / f"{key}.json"
    return user_candidate if user_candidate.is_file() else None


def load_bundle_by_slug(slug: str) -> dict[str, Any] | None:
    path = _bundle_path_for_slug(slug)
    if not path or not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return None
    return data


def load_bundle_by_name(name: str) -> dict[str, Any] | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    for item in list_preset_summaries():
        if str(item.get("name") or "").strip().lower() == key:
            return load_bundle_by_slug(str(item.get("slug") or ""))
        if str(item.get("slug") or "").strip().lower() == key:
            return load_bundle_by_slug(str(item.get("slug") or ""))
    return None


def list_preset_summaries() -> list[dict[str, Any]]:
    """Metadata for each bundle (name, slug, category, step_count)."""
    out: list[dict[str, Any]] = []
    for entry in _all_manifest_entries():
        slug = str(entry.get("slug") or "").strip()
        path = _resolve_bundle_path(entry)
        if not path or not path.is_file():
            logger.warning("Loop preset bundle missing for slug=%s", slug)
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                bundle = json.load(handle)
        except Exception as exc:
            logger.warning("Failed to read loop preset %s: %s", path, exc)
            continue
        steps = normalize_bundle_steps(bundle)
        loop_contract = dict(bundle.get("loop_contract") or {})
        origin = str(entry.get("origin") or bundle.get("origin") or "builtin")
        out.append(
            {
                "slug": slug,
                "name": bundle.get("name") or entry.get("name") or slug,
                "role": bundle.get("role") or entry.get("role"),
                "category": bundle.get("category") or entry.get("category") or ("Saved" if origin == "user" else ""),
                "archetype": bundle.get("archetype") or entry.get("archetype"),
                "description": (bundle.get("description") or "")[:300],
                "expected_check_command": loop_contract.get("check_command") or bundle.get("expected_check_command"),
                "expected_max_iterations": loop_contract.get("max_iterations"),
                "step_count": len(steps),
                "source": "user" if origin == "user" else "bundle",
                "origin": origin,
                "file": str(path),
            }
        )
    return out


def list_preset_catalog_entries() -> list[dict[str, Any]]:
    """Catalog-shaped entries for loop_catalog.ELORM_LOOP_KICKOFFS compatibility."""
    entries: list[dict[str, Any]] = []
    for summary in list_preset_summaries():
        bundle = load_bundle_by_slug(str(summary.get("slug") or ""))
        if not bundle:
            continue
        loop_contract = dict(bundle.get("loop_contract") or {})
        entries.append(
            {
                "name": bundle.get("name") or summary.get("name"),
                "slug": summary.get("slug"),
                "category": bundle.get("category") or summary.get("category"),
                "archetype": bundle.get("archetype") or summary.get("archetype"),
                "kickoff": str(bundle.get("kickoff") or "").strip(),
                "expected_check_command": loop_contract.get("check_command") or bundle.get("expected_check_command"),
                "expected_max_iterations": loop_contract.get("max_iterations"),
                "description": bundle.get("description") or "",
            }
        )
    return entries


def plan_steps_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return loop contract + normalized steps from a loaded bundle."""
    loop_contract = dict(bundle.get("loop_contract") or {})
    if bundle.get("kickoff") and not loop_contract.get("goal"):
        parsed = parse_loop_contract(str(bundle.get("kickoff")))
        for key, value in parsed.items():
            if value not in (None, "", []) and key not in loop_contract:
                loop_contract[key] = value
    loop_contract.setdefault("goal", bundle.get("name"))
    if not loop_contract.get("guardrails"):
        loop_contract["guardrails"] = _default_guardrails(loop_contract)
    steps = normalize_bundle_steps(bundle)
    return {
        "success": True,
        "preset": bundle.get("name"),
        "slug": bundle.get("slug"),
        "step_count": len(steps),
        "loop_contract": loop_contract,
        "steps": steps,
    }


def validate_loop_bundle(data: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Return (bundle, error_message) for uploaded JSON."""
    if not isinstance(data, dict):
        return None, "Preset file must be a JSON object"
    fmt = str(data.get("format") or "").strip()
    if fmt and fmt != BUNDLE_FORMAT:
        return None, f"Unsupported preset format: {fmt}"
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return None, "Preset must include a non-empty steps array"
    if len(steps) > WORKFLOW_LOOP_MAX_STEPS:
        return None, f"Preset has {len(steps)} steps (max {WORKFLOW_LOOP_MAX_STEPS})"
    name = str(data.get("name") or "").strip()
    if not name:
        return None, "Preset must include a name"
    bundle = dict(data)
    bundle.setdefault("format", BUNDLE_FORMAT)
    bundle.setdefault("format_version", BUNDLE_VERSION)
    bundle["slug"] = _slugify(str(bundle.get("slug") or name))
    bundle["name"] = name
    return bundle, None


def workflow_export_to_loop_bundle(
    *,
    name: str,
    description: str,
    workflow_input: str,
    export_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a loop preset bundle from exported workflow step data."""
    wf_input: dict[str, Any] = {}
    try:
        wf_input = json.loads(workflow_input or "{}") or {}
    except Exception:
        wf_input = {}
    loop_contract = dict(wf_input.get("loop_contract") or {})
    kickoff = str(description or wf_input.get("kickoff") or "").strip()
    if kickoff and not loop_contract:
        loop_contract = parse_loop_contract(kickoff)
    loop_contract.setdefault("goal", loop_contract.get("goal") or name)
    archetype = str(
        wf_input.get("archetype")
        or loop_contract.get("archetype")
        or infer_loop_archetype(kickoff, loop_contract)
    )

    bundle_steps: list[dict[str, Any]] = []
    ordered = sorted(export_steps, key=lambda s: int(s.get("position") or 0))
    for step in ordered:
        cfg = step.get("config") or {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg) or {}
            except Exception:
                cfg = {}
        bundle_step: dict[str, Any] = {
            "name": str(step.get("name") or "Step"),
            "instruction": str(step.get("instruction") or "").strip(),
            "guardrail": str(cfg.get("guardrail") or "").strip(),
            "failure_checklist": list(cfg.get("failure_checklist") or []),
            "validation_prompt": str(step.get("validation_prompt") or step.get("verification") or "").strip(),
            "validation_type": str(step.get("validation_type") or "llm_judgment"),
            "skills": list(cfg.get("skills") or []),
            "tools": list(cfg.get("tools") or []),
            "other_tool": str(cfg.get("other_tool") or "").strip(),
            "action_type": str(step.get("action_type") or derive_action_type_from_ui_tools(cfg.get("tools"))),
            "wait_for_continue": bool(step.get("wait_for_continue", False)),
            "routing_mode": str(step.get("routing_mode") or "static"),
        }
        if cfg.get("command"):
            bundle_step["command"] = cfg["command"]
        pass_pos = step.get("on_pass_goto_position")
        fail_pos = step.get("on_fail_goto_position")
        if pass_pos is not None:
            bundle_step["on_pass_goto_position"] = pass_pos
        if fail_pos is not None:
            bundle_step["on_fail_goto_position"] = fail_pos
        bundle_steps.append(bundle_step)

    return {
        "format_version": BUNDLE_VERSION,
        "format": BUNDLE_FORMAT,
        "slug": _slugify(name),
        "name": name,
        "category": wf_input.get("category") or loop_contract.get("category") or "Custom",
        "archetype": archetype,
        "description": kickoff or name,
        "kickoff": kickoff,
        "loop_contract": loop_contract,
        "steps": bundle_steps,
        "origin": "export",
    }


def save_user_loop_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Persist a bundle to ~/.decisions/loop_presets/ and register in user manifest."""
    bundle, err = validate_loop_bundle(bundle)
    if err or not bundle:
        return {"success": False, "error": err or "Invalid bundle", "status_code": 422}

    slug = str(bundle.get("slug") or _slugify(str(bundle.get("name"))))
    bundle["slug"] = slug
    bundle["origin"] = "user"
    bundle["category"] = bundle.get("category") or "Saved"

    user_dir = user_presets_dir()
    out_path = user_dir / f"{slug}.json"
    if out_path.is_file():
        return {
            "success": False,
            "error": f"A preset named '{bundle.get('name')}' already exists. Choose a different name.",
            "status_code": 409,
        }

    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(bundle, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    manifest = _load_user_manifest()
    presets = list(manifest.get("presets") or [])
    presets = [p for p in presets if str(p.get("slug") or "").lower() != slug.lower()]
    presets.append(
        {
            "slug": slug,
            "name": bundle.get("name"),
            "category": bundle.get("category") or "Saved",
            "archetype": bundle.get("archetype"),
        }
    )
    manifest["format_version"] = BUNDLE_VERSION
    manifest["format"] = "decisionsai_loop_preset_manifest_v1"
    manifest["presets"] = presets
    _save_user_manifest(manifest)
    _load_manifest_cached.cache_clear()

    return {"success": True, "slug": slug, "name": bundle.get("name"), "path": str(out_path)}
