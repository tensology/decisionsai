"""Apply elorm.xyz loop presets to an existing workflow (from JSON bundles)."""

from __future__ import annotations

import json
import logging
from typing import Any

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
from distr.core.workflow.loop_preset_loader import (
    load_bundle_by_name,
    list_preset_summaries,
    plan_steps_from_bundle,
    save_user_loop_bundle,
    validate_loop_bundle,
    workflow_export_to_loop_bundle,
)
from distr.core.workflow.planning import (
    WORKFLOW_LOOP_MAX_STEPS,
    _persist_planned_steps,
    loop_contract_to_context_rules,
)

logger = logging.getLogger(__name__)


def list_loop_presets() -> list[dict[str, Any]]:
    """Return preset metadata for the workflow UI."""
    return list_preset_summaries()


def _find_preset(preset_name: str) -> dict[str, Any] | None:
    return load_bundle_by_name(preset_name)


def plan_preset_steps(preset_name: str) -> dict[str, Any]:
    """Build preset steps and loop contract from the JSON bundle."""
    bundle = _find_preset(preset_name)
    if not bundle:
        return {"success": False, "error": f"Unknown loop preset: {preset_name}"}
    return plan_steps_from_bundle(bundle)


def export_loop_preset_json(workflow_id: int) -> dict[str, Any] | None:
    """Export the workflow's current loop steps as a preset bundle dict."""
    from distr.core.workflow.service import export_workflow, get_workflow

    export_data = export_workflow(workflow_id)
    wf = get_workflow(workflow_id)
    if not export_data or not wf:
        return None
    steps = export_data.get("steps") or []
    if not steps:
        return None
    return workflow_export_to_loop_bundle(
        name=str(export_data.get("name") or wf.get("name") or "Loop"),
        description=str(export_data.get("description") or wf.get("description") or ""),
        workflow_input=str(wf.get("workflow_input") or ""),
        export_steps=steps,
    )


def apply_loop_bundle(
    workflow_id: int,
    bundle: dict[str, Any],
    *,
    mode: str = "replace",
) -> dict[str, Any]:
    """Replace or append workflow steps from a loop preset bundle dict."""
    apply_mode = (mode or "replace").strip().lower()
    if apply_mode not in {"replace", "append"}:
        return {"success": False, "error": "mode must be replace or append", "status_code": 422}

    validated, err = validate_loop_bundle(bundle)
    if err or not validated:
        return {"success": False, "error": err or "Invalid bundle", "status_code": 422}

    planned = plan_steps_from_bundle(validated)
    if not planned.get("success"):
        return planned

    kickoff = str(validated.get("kickoff") or "").strip()
    loop_contract = dict(planned.get("loop_contract") or {})
    steps_data = list(planned.get("steps") or [])

    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not wf:
            return {"success": False, "error": "Workflow not found"}

        existing_count = (
            db.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == wf.id)
            .count()
        )
        if apply_mode == "append":
            if existing_count + len(steps_data) > WORKFLOW_LOOP_MAX_STEPS:
                return {
                    "success": False,
                    "error": (
                        f"Cannot append preset ({len(steps_data)} steps) — workflow already has "
                        f"{existing_count} steps (max {WORKFLOW_LOOP_MAX_STEPS})."
                    ),
                    "status_code": 422,
                    "current_steps": existing_count,
                    "preset_steps": len(steps_data),
                    "max_steps": WORKFLOW_LOOP_MAX_STEPS,
                }
            start_position = existing_count
        else:
            db.query(AutoWorkflowStep).filter(AutoWorkflowStep.workflow_id == wf.id).delete()
            db.flush()
            start_position = 0

        _persist_planned_steps(db, wf.id, steps_data, start_position=start_position)

        merged_input: dict[str, Any] = {}
        try:
            merged_input = json.loads(wf.workflow_input or "{}") or {}
        except Exception:
            merged_input = {}
        merged_input.update({k: v for k, v in loop_contract.items() if v not in (None, "", [])})
        merged_input["loop_contract"] = loop_contract
        merged_input["preset_name"] = validated.get("name")
        merged_input["preset_slug"] = validated.get("slug")
        merged_input["planning_mode"] = "loop_preset"
        merged_input["preset_source"] = validated.get("origin") or "bundle"

        if apply_mode == "replace" and kickoff:
            wf.description = kickoff
            wf.context_rules = loop_contract_to_context_rules(loop_contract)
        if apply_mode == "replace":
            for chain_field in ("pre_chain", "post_chain"):
                chain_value = validated.get(chain_field)
                if isinstance(chain_value, list):
                    cleaned = [str(item).strip() for item in chain_value if str(item).strip()]
                    setattr(wf, chain_field, json.dumps(cleaned) if cleaned else None)
            preset_run_settings = validated.get("run_settings")
            if isinstance(preset_run_settings, dict):
                try:
                    current_run_settings = json.loads(wf.run_settings or "{}") or {}
                except Exception:
                    current_run_settings = {}
                if not isinstance(current_run_settings, dict):
                    current_run_settings = {}
                current_run_settings.update(preset_run_settings)
                wf.run_settings = json.dumps(current_run_settings, sort_keys=True)
        merged_input["preset_apply_mode"] = apply_mode
        wf.workflow_input = json.dumps(merged_input)
        db.commit()

    return {
        "success": True,
        "workflow_id": int(workflow_id),
        "preset": validated.get("name"),
        "preset_slug": validated.get("slug"),
        "mode": apply_mode,
        "step_count": len(steps_data),
        "total_steps": (existing_count + len(steps_data)) if apply_mode == "append" else len(steps_data),
        "loop_contract": loop_contract,
    }


def apply_loop_preset(
    workflow_id: int,
    preset_name: str,
    *,
    mode: str = "replace",
) -> dict[str, Any]:
    """Replace or append workflow steps from a named loop preset bundle."""
    bundle = _find_preset(preset_name)
    if not bundle:
        return {"success": False, "error": f"Unknown loop preset: {preset_name}"}
    result = apply_loop_bundle(workflow_id, bundle, mode=mode)
    # Preset bundle names label the preset catalog, not the user's workflow tab.
    return result


def import_loop_preset_json(
    workflow_id: int,
    bundle_data: dict[str, Any],
    *,
    mode: str = "replace",
) -> dict[str, Any]:
    """Import an uploaded loop preset JSON into a workflow."""
    return apply_loop_bundle(workflow_id, bundle_data, mode=mode)


def save_loop_preset_from_workflow(workflow_id: int, preset_name: str) -> dict[str, Any]:
    """Export current workflow steps and save as a user preset."""
    name = (preset_name or "").strip()
    if not name:
        return {"success": False, "error": "Preset name is required", "status_code": 422}

    bundle = export_loop_preset_json(workflow_id)
    if not bundle:
        return {"success": False, "error": "Workflow not found or has no steps to save", "status_code": 404}

    bundle["name"] = name
    bundle["slug"] = None  # recomputed in save_user_loop_bundle
    return save_user_loop_bundle(bundle)
