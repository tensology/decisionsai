"""
Workflow Import/Export — export, import, presets, bundles, serialization.

Extracted from service.py as part of the module decomposition.
"""
import json
import logging
import os
from typing import List, Dict, Any, Optional

from distr.core.db import get_session, Action
from distr.core.workflow.migration import _SESSION_STATUS_MAP
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowVariable,
)

logger = logging.getLogger(__name__)

# Duplicated from service.py to avoid circular imports
_VALID_WORKFLOW_TYPES = {"manual", "instruction", "scheduled", "audit"}


def _safe_json_loads(text: Optional[str]) -> Any:
    """Parse a JSON string, returning an empty dict on None or invalid JSON."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


def _get_presets_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "steprunner", "presets")


# ── Serialization ──

def _serialize_workflow(wf: AutoWorkflow) -> Dict[str, Any]:
    steps = sorted(wf.steps, key=lambda s: s.position)
    return {
        "id": wf.id, "name": wf.name,
        "description": wf.description or "",
        "workflow_type": wf.workflow_type or "manual",
        "run_settings": _safe_json_loads(getattr(wf, "run_settings", None)) or {},
        "schedule_enabled": wf.schedule_enabled,
        "schedule_preset": wf.schedule_preset,
        "schedule_cron": wf.schedule_cron,
        "schedule_time": wf.schedule_time,
        "schedule_days": wf.schedule_days,
        "schedule_timezone": wf.schedule_timezone,
        "next_run_at": wf.next_run_at.isoformat() if wf.next_run_at else None,
        "last_run_at": wf.last_run_at.isoformat() if wf.last_run_at else None,
        "start_step_position": wf.start_step_position or 0,
        "created_date": wf.created_date.isoformat() if wf.created_date else None,
        "modified_date": wf.modified_date.isoformat() if wf.modified_date else None,
        "steps": [_serialize_step(s) for s in steps],
        "context_items": [
            {
                "id": v.id,
                "title": v.name or "",
                "content": v.default_value or "",
                "notes": v.description or "",
            }
            for v in wf.variables
        ],
        "runs": [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "current_step_id": r.current_step_id,
            }
            for r in sorted(wf.runs, key=lambda r: r.started_at or wf.created_date, reverse=True)[:5]
        ],
    }


def _serialize_step(step: AutoWorkflowStep) -> Dict[str, Any]:
    return {
        "id": step.id, "position": step.position,
        "name": step.name, "description": step.description or "",
        "action_type": step.action_type or "agent_instruction",
        "instruction": step.instruction or "",
        "validation_type": step.validation_type or "none",
        "validation_prompt": step.validation_prompt or "",
        "screenshot_path": step.screenshot_path or "",
        "recording_filename": step.recording_filename or "",
        "action_id": step.action_id,
        "routing_mode": step.routing_mode or "static",
        "routing_prompt": step.routing_prompt or "",
        "on_pass_goto": step.on_pass_goto,
        "on_fail_goto": step.on_fail_goto,
        "wait_before_next": step.wait_before_next or 0,
        "max_retries": step.max_retries or 0,
        "timeout_seconds": step.timeout_seconds or 300,
        "require_approval": step.require_approval or False,
        "status": step.status or "pending",
        "result": step.result,
        "code": step.code or "",
        "validation_code": step.validation_code or "",
        "linked_project_id": step.linked_project_id,
        "wait_for_continue": step.wait_for_continue or False,
    }


# ── Position / ID helpers ──

def _step_id_to_position(step_id: Optional[int], steps: list) -> Optional[int]:
    """Convert a step ID to its position number for export. Returns None if not found or -1 for explicit end."""
    if step_id is None:
        return None
    if step_id == -1:
        return -1
    for s in steps:
        if s.id == step_id:
            return s.position
    return None


def _position_to_step_id(position: Optional[int], position_map: dict) -> Optional[int]:
    if position is None:
        return None
    if position == -1:
        return -1
    step = position_map.get(position)
    return step.id if step else None


# ── Export ──

def export_workflow(workflow_id: int) -> Optional[Dict[str, Any]]:
    """Export a workflow + steps as a portable JSON dict (metadata only, no files)."""
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        steps = sorted(wf.steps, key=lambda s: s.position)

        # Collect linked actions for steps that have them
        linked_actions = {}
        for s in steps:
            if s.action_id:
                action = db.query(Action).filter(Action.id == s.action_id).first()
                if action:
                    linked_actions[s.action_id] = {
                        "title": action.title or "",
                        "description": action.description or "",
                        "additional_trigger_words": action.additional_trigger_words or "[]",
                        "is_instruction": action.is_instruction or False,
                        "instruction_text": action.instruction_text or "",
                        "recording_filename": action.recording_filename or "",
                    }

        return {
            "format_version": "2.0",
            "format": "decisionsai_workflow_v1",
            "name": wf.name,
            "description": wf.description or "",
            "workflow_type": wf.workflow_type or "manual",
            "context_rules": wf.context_rules or "",
            "start_step_position": wf.start_step_position or 0,
            "variables": [
                {
                    "name": v.name,
                    "default_value": v.default_value or "",
                    "description": v.description or "",
                }
                for v in sorted(wf.variables, key=lambda x: (x.name or "", x.id))
            ],
            "schedule_preset": wf.schedule_preset,
            "schedule_time": wf.schedule_time,
            "schedule_days": wf.schedule_days,
            "steps": [
                {
                    "position": s.position, "name": s.name,
                    "description": s.description or "",
                    "action_type": s.action_type or "agent_instruction",
                    "step_type": s.step_type or "agent_instruction",
                    "instruction": s.instruction or "",
                    "verification": s.verification or "",
                    "config": _safe_json_loads(s.config),
                    "validation_type": s.validation_type or "none",
                    "validation_prompt": s.validation_prompt or "",
                    "routing_mode": s.routing_mode or "static",
                    "routing_prompt": s.routing_prompt or "",
                    "on_pass_goto_position": _step_id_to_position(s.on_pass_goto, steps),
                    "on_fail_goto_position": _step_id_to_position(s.on_fail_goto, steps),
                    "wait_before_next": s.wait_before_next or 0,
                    "max_retries": s.max_retries or 0,
                    "timeout_seconds": s.timeout_seconds or 300,
                    "require_approval": s.require_approval or False,
                    "recording_filename": s.recording_filename or "",
                    "screenshot_filename": os.path.basename(s.screenshot_path) if s.screenshot_path else "",
                    "linked_action": linked_actions.get(s.action_id) if s.action_id else None,
                    "code": s.code or "",
                    "validation_code": s.validation_code or "",
                    "linked_project_id": s.linked_project_id,
                    "wait_for_continue": s.wait_for_continue or False,
                }
                for s in steps
            ],
        }


def export_workflow_bundle(workflow_id: int) -> Optional[bytes]:
    """
    Export a workflow as a .dwf bundle (ZIP with custom extension).
    Includes: workflow.json + recordings/*.json + screenshots/*
    Returns raw bytes of the ZIP archive.
    """
    import zipfile
    import io
    from distr.core.paths import RECORDINGS_DIR, DB_DIR

    data = export_workflow(workflow_id)
    if not data:
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write the workflow manifest
        zf.writestr("workflow.json", json.dumps(data, indent=2))

        # Bundle recording files
        for s in data.get("steps", []):
            rec = s.get("recording_filename", "")
            if rec:
                rec_path = os.path.join(RECORDINGS_DIR, rec)
                if os.path.isfile(rec_path):
                    zf.write(rec_path, f"recordings/{rec}")

            # Bundle linked action's recording if different from step recording
            linked = s.get("linked_action")
            if linked:
                linked_rec = linked.get("recording_filename", "")
                if linked_rec and linked_rec != rec:
                    linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                    if os.path.isfile(linked_rec_path):
                        zf.write(linked_rec_path, f"recordings/{linked_rec}")

            # Bundle screenshot files
            scr = s.get("screenshot_filename", "")
            if scr:
                scr_path = os.path.join(DB_DIR, "workflow_screenshots", scr)
                if os.path.isfile(scr_path):
                    zf.write(scr_path, f"screenshots/{scr}")

    return buf.getvalue()


# ── Legacy conversion ──

def _convert_legacy_to_unified(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a legacy StepRunner session export (no format_version or '1.0') to unified v2.0 format.

    Uses the migration field mapping from the design document:
    - Session: instruction → description, session_type → workflow_type, enabled → schedule_enabled, etc.
    - Steps: title → name, step_type/config/verification/code/position preserved
    """
    # Map session_type to workflow_type
    session_type = data.get("session_type", "instruction")
    type_map = {"instruction": "instruction", "scheduled": "scheduled"}
    workflow_type = type_map.get(session_type, "manual")

    unified: Dict[str, Any] = {
        "format_version": "2.0",
        "name": data.get("name", data.get("instruction", "Imported Workflow")[:80] or "Imported Workflow"),
        "description": data.get("instruction", ""),
        "workflow_type": workflow_type,
        "context_rules": data.get("context_rules", ""),
        "workflow_input": data.get("workflow_input", ""),
        "start_step_position": data.get("start_step_position", 0),
        "schedule_preset": data.get("schedule"),
        "schedule_time": data.get("schedule_time"),
        "schedule_days": data.get("schedule_days"),
        "schedule_enabled": data.get("enabled", False),
        "schedule_timezone": data.get("timezone"),
    }

    # Convert steps: title → name
    legacy_steps = data.get("steps", [])
    unified_steps = []
    for i, s in enumerate(legacy_steps):
        unified_step: Dict[str, Any] = {
            "position": s.get("position", i),
            "name": s.get("title", s.get("name", "Step")),
            "instruction": s.get("instruction", ""),
            "verification": s.get("verification", ""),
            "step_type": s.get("step_type", "run_command"),
            "config": s.get("config"),
            "code": s.get("code", ""),
            "action_type": s.get("action_type", "agent_instruction"),
            "description": s.get("description", ""),
            "validation_type": s.get("validation_type", "none"),
            "validation_prompt": s.get("validation_prompt", ""),
            "routing_mode": s.get("routing_mode", "static"),
            "routing_prompt": s.get("routing_prompt", ""),
            "on_pass_goto_position": s.get("on_pass_goto_position"),
            "on_fail_goto_position": s.get("on_fail_goto_position"),
            "wait_before_next": s.get("wait_before_next", 0),
            "max_retries": s.get("max_retries", 0),
            "timeout_seconds": s.get("timeout_seconds", 300),
            "require_approval": s.get("require_approval", False),
            "recording_filename": s.get("recording_filename", ""),
            "screenshot_filename": s.get("screenshot_filename", ""),
            "linked_action": s.get("linked_action"),
            "validation_code": s.get("validation_code", ""),
            "linked_project_id": s.get("linked_project_id"),
            "wait_for_continue": s.get("wait_for_continue", False),
        }
        unified_steps.append(unified_step)

    unified["steps"] = unified_steps
    unified["variables"] = list(data.get("variables") or [])
    if "status" in data:
        old_st = data["status"]
        unified["status"] = _SESSION_STATUS_MAP.get(old_st, old_st)
    return unified


def _is_legacy_format(data: Dict[str, Any]) -> bool:
    """Return True if the import data is in legacy StepRunner format (no format_version or '1.0')."""
    fv = data.get("format_version")
    return fv is None or fv == "1.0"


# ── Import ──

def import_workflow(data: Dict[str, Any], recordings: Optional[Dict[str, bytes]] = None,
                    screenshots: Optional[Dict[str, bytes]] = None) -> int:
    """
    Import a workflow from a portable JSON dict. Optionally restores recording
    and screenshot files from provided binary data.

    Handles both unified format (format_version '2.0' or format 'decisionsai_workflow_v1')
    and legacy StepRunner session format (no format_version or '1.0') by converting
    legacy fields using the migration field mapping.

    Returns the new workflow ID.
    """
    from distr.core.paths import RECORDINGS_DIR, DB_DIR

    recordings = recordings or {}
    screenshots = screenshots or {}

    # Convert legacy format to unified before processing
    if _is_legacy_format(data):
        data = _convert_legacy_to_unified(data)

    # Validate workflow_type if present
    wf_type = data.get("workflow_type", "manual")
    if wf_type not in _VALID_WORKFLOW_TYPES:
        wf_type = "manual"

    with get_session() as db:
        wf = AutoWorkflow(
            name=data.get("name", "Imported Workflow"),
            description=data.get("description", ""),
            workflow_type=wf_type,
            context_rules=data.get("context_rules") or None,
            workflow_input=data.get("workflow_input") or None,
            start_step_position=data.get("start_step_position", 0),
        )
        db.add(wf)
        db.flush()

        position_to_step = {}
        pass_refs = {}
        fail_refs = {}
        for s_data in data.get("steps", []):
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=s_data.get("position", 0),
                name=s_data.get("name", "Step"),
                description=s_data.get("description", ""),
                action_type=s_data.get("action_type", "agent_instruction"),
                instruction=s_data.get("instruction", ""),
                step_type=s_data.get("step_type", "agent_instruction"),
                verification=s_data.get("verification") or None,
                config=json.dumps(s_data["config"]) if isinstance(s_data.get("config"), dict) else (s_data.get("config") or None),
                tool_used=s_data.get("tool_used") or None,
                validation_type=s_data.get("validation_type", "none"),
                validation_prompt=s_data.get("validation_prompt", ""),
                routing_mode=s_data.get("routing_mode", "static"),
                routing_prompt=s_data.get("routing_prompt", ""),
                wait_before_next=s_data.get("wait_before_next", 0),
                max_retries=s_data.get("max_retries", 0),
                timeout_seconds=s_data.get("timeout_seconds", 300),
                require_approval=s_data.get("require_approval", False),
                code=s_data.get("code", ""),
                validation_code=s_data.get("validation_code", ""),
                linked_project_id=s_data.get("linked_project_id"),
                wait_for_continue=s_data.get("wait_for_continue", False),
            )
            db.add(step)
            db.flush()
            position_to_step[step.position] = step
            pass_refs[step.id] = s_data.get("on_pass_goto_position")
            fail_refs[step.id] = s_data.get("on_fail_goto_position")

            # Restore recording file
            rec_name = s_data.get("recording_filename", "")
            if rec_name and rec_name in recordings:
                os.makedirs(RECORDINGS_DIR, exist_ok=True)
                orig_rec_name = rec_name
                rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                # Avoid overwriting existing recordings — add suffix if needed
                if os.path.exists(rec_path):
                    base, ext = os.path.splitext(rec_name)
                    rec_name = f"{base}_{wf.id}{ext}"
                    rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                with open(rec_path, "wb") as f:
                    f.write(recordings[orig_rec_name])
                step.recording_filename = rec_name

            elif rec_name:
                # Recording referenced but not in bundle — keep the name in case it exists locally
                rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                if os.path.isfile(rec_path):
                    step.recording_filename = rec_name

            # Recreate linked Action entity if present in export data
            linked_action_data = s_data.get("linked_action")
            if linked_action_data:
                try:
                    linked_rec = linked_action_data.get("recording_filename", "")
                    # Restore linked action's recording file if in bundle and different from step's
                    if linked_rec and linked_rec in recordings and linked_rec != rec_name:
                        os.makedirs(RECORDINGS_DIR, exist_ok=True)
                        orig_linked_rec = linked_rec
                        linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                        if os.path.exists(linked_rec_path):
                            base, ext = os.path.splitext(linked_rec)
                            linked_rec = f"{base}_{wf.id}{ext}"
                            linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                        with open(linked_rec_path, "wb") as f:
                            f.write(recordings[orig_linked_rec])
                    # Use step's recording_filename if linked action's matches the original
                    action_rec = linked_rec if linked_rec else (step.recording_filename or "")
                    new_action = Action(
                        title=linked_action_data.get("title", step.name),
                        description=linked_action_data.get("description", ""),
                        additional_trigger_words=linked_action_data.get("additional_trigger_words", "[]"),
                        is_instruction=linked_action_data.get("is_instruction", False),
                        instruction_text=linked_action_data.get("instruction_text", ""),
                        recording_filename=action_rec,
                    )
                    db.add(new_action)
                    db.flush()
                    step.action_id = new_action.id
                except Exception as e:
                    logger.warning(f"Could not recreate linked action for step {step.id}: {e}")

            # Restore screenshot file
            scr_name = s_data.get("screenshot_filename", "")
            if scr_name and scr_name in screenshots:
                scr_dir = os.path.join(DB_DIR, "workflow_screenshots")
                os.makedirs(scr_dir, exist_ok=True)
                # Rename to new step ID
                ext = os.path.splitext(scr_name)[1] or ".png"
                new_scr_name = f"step_{step.id}{ext}"
                scr_path = os.path.join(scr_dir, new_scr_name)
                with open(scr_path, "wb") as f:
                    f.write(screenshots[scr_name])
                step.screenshot_path = scr_path

        for step in position_to_step.values():
            step.on_pass_goto = _position_to_step_id(pass_refs.get(step.id), position_to_step)
            step.on_fail_goto = _position_to_step_id(fail_refs.get(step.id), position_to_step)

        for v in data.get("variables") or []:
            if not isinstance(v, dict):
                continue
            vn = v.get("name")
            if not vn:
                continue
            db.add(
                AutoWorkflowVariable(
                    workflow_id=wf.id,
                    name=str(vn),
                    default_value=v.get("default_value") or "",
                    description=v.get("description") or None,
                )
            )

        db.commit()
        return wf.id


def import_workflow_bundle(bundle_bytes: bytes) -> int:
    """
    Import a .dwf bundle (ZIP). Extracts workflow.json, recordings, and screenshots,
    then calls import_workflow with the extracted assets.
    """
    import zipfile
    import io

    buf = io.BytesIO(bundle_bytes)
    recordings = {}
    screenshots = {}

    with zipfile.ZipFile(buf, "r") as zf:
        # Read manifest
        data = json.loads(zf.read("workflow.json"))

        # Extract recordings
        for name in zf.namelist():
            if name.startswith("recordings/") and not name.endswith("/"):
                fname = os.path.basename(name)
                recordings[fname] = zf.read(name)
            elif name.startswith("screenshots/") and not name.endswith("/"):
                fname = os.path.basename(name)
                screenshots[fname] = zf.read(name)

    return import_workflow(data, recordings=recordings, screenshots=screenshots)


# ── Presets ──

def list_presets() -> List[Dict[str, str]]:
    """List available preset files (.dwf bundles and .json) from steprunner/presets/."""
    import zipfile
    import io
    presets_dir = _get_presets_dir()
    if not os.path.isdir(presets_dir):
        return []
    results = []
    for fname in sorted(os.listdir(presets_dir)):
        fpath = os.path.join(presets_dir, fname)
        if fname.endswith(".dwf"):
            try:
                with zipfile.ZipFile(fpath, "r") as zf:
                    data = json.loads(zf.read("workflow.json"))
                has_recordings = any(n.startswith("recordings/") for n in zf.namelist())
                has_screenshots = any(n.startswith("screenshots/") for n in zf.namelist())
                results.append({
                    "filename": fname,
                    "name": data.get("name", fname.replace(".dwf", "")),
                    "description": (data.get("description", "") or "")[:200],
                    "step_count": len(data.get("steps", [])),
                    "has_recordings": has_recordings,
                    "has_screenshots": has_screenshots,
                    "bundle": True,
                })
            except Exception:
                results.append({"filename": fname, "name": fname, "description": "Invalid bundle", "step_count": 0, "bundle": True})
        elif fname.endswith(".json"):
            try:
                with open(fpath, "r") as f:
                    data = json.load(f)
                results.append({
                    "filename": fname,
                    "name": data.get("name", fname.replace(".json", "")),
                    "description": (data.get("description", "") or "")[:200],
                    "step_count": len(data.get("steps", [])),
                    "bundle": False,
                })
            except Exception:
                results.append({"filename": fname, "name": fname, "description": "Invalid JSON", "step_count": 0, "bundle": False})
    return results


def load_preset(filename: str) -> Optional[int]:
    """Load a preset file (.dwf bundle or .json) and import it as a new workflow."""
    presets_dir = _get_presets_dir()
    fpath = os.path.join(presets_dir, filename)
    if not os.path.isfile(fpath):
        return None
    if filename.endswith(".dwf"):
        with open(fpath, "rb") as f:
            return import_workflow_bundle(f.read())
    else:
        with open(fpath, "r") as f:
            data = json.load(f)
        return import_workflow(data)


def save_preset(workflow_id: int, filename: Optional[str] = None) -> Optional[str]:
    """Export a workflow to a .dwf bundle preset file. Returns the filename."""
    import re

    # Check if workflow has any recordings or screenshots
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None

    bundle_bytes = export_workflow_bundle(workflow_id)
    if not bundle_bytes:
        return None

    if not filename:
        data = export_workflow(workflow_id)
        safe_name = re.sub(r'[^a-z0-9_]', '', (data.get("name", "workflow") or "workflow").lower().replace(" ", "_"))
        filename = f"{safe_name}.dwf"
    elif not filename.endswith(".dwf"):
        filename = filename.rsplit(".", 1)[0] + ".dwf"

    presets_dir = _get_presets_dir()
    os.makedirs(presets_dir, exist_ok=True)
    with open(os.path.join(presets_dir, filename), "wb") as f:
        f.write(bundle_bytes)
    return filename
