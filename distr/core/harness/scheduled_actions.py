"""Scheduled desktop action normalization helpers."""

from __future__ import annotations

from typing import Any


VALID_SCHEDULE_KINDS = {"once", "daily", "weekdays", "weekly"}
VALID_ACTION_TYPES = {"keypress", "type_text", "open_app", "play_recording"}
WEEKDAY_TO_CRON_DAY = {
    "monday": "1",
    "tuesday": "2",
    "wednesday": "3",
    "thursday": "4",
    "friday": "5",
    "saturday": "6",
    "sunday": "0",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_scheduled_action(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalize a simple scheduled desktop action spec."""
    data = dict(spec or {})
    title = _clean_text(data.get("title")) or "Scheduled action"
    schedule = dict(data.get("schedule") or {})
    action = dict(data.get("action") or {})
    safety = dict(data.get("safety") or {})
    target = dict(data.get("target_context") or {})

    kind = _clean_text(schedule.get("kind")).lower()
    if kind not in VALID_SCHEDULE_KINDS:
        raise ValueError(f"Unsupported schedule kind: {kind}")
    action_type = _clean_text(action.get("type")).lower()
    if action_type not in VALID_ACTION_TYPES:
        raise ValueError(f"Unsupported action type: {action_type}")

    normalized_action: dict[str, Any] = {"type": action_type}
    if action_type == "keypress":
        key = _clean_text(action.get("key")).lower()
        if not key:
            raise ValueError("keypress action requires key")
        normalized_action["key"] = key
    elif action_type == "type_text":
        text = _clean_text(action.get("text"))
        if not text:
            raise ValueError("type_text action requires text")
        normalized_action["text"] = text
        normalized_action["press_enter"] = bool(action.get("press_enter", False))
    elif action_type == "open_app":
        app_name = _clean_text(action.get("app_name") or action.get("name"))
        if not app_name:
            raise ValueError("open_app action requires app_name")
        normalized_action["app_name"] = app_name
    elif action_type == "play_recording":
        recording_name = _clean_text(action.get("recording_name"))
        if not recording_name:
            raise ValueError("play_recording action requires recording_name")
        normalized_action["recording_name"] = recording_name

    normalized_schedule = {"kind": kind}
    normalized_schedule.update({k: v for k, v in schedule.items() if k != "kind"})

    return {
        "title": title,
        "schedule": normalized_schedule,
        "action": normalized_action,
        "target_context": {
            "app_name": _clean_text(target.get("app_name")),
            "window_title_hint": _clean_text(target.get("window_title_hint")),
        },
        "safety": {
            "require_app_in_foreground": bool(safety.get("require_app_in_foreground", False)),
            "bring_app_to_front": bool(safety.get("bring_app_to_front", False)),
        },
    }


def preview_scheduled_action(spec: dict[str, Any]) -> str:
    """Return a short human confirmation preview for a scheduled action."""
    normalized = normalize_scheduled_action(spec)
    action = normalized["action"]
    schedule = normalized["schedule"]
    target = normalized.get("target_context") or {}
    safety = normalized.get("safety") or {}
    if action["type"] == "keypress":
        action_text = f"Press {action['key']}"
    elif action["type"] == "type_text":
        suffix = " and press Enter" if action.get("press_enter") else ""
        action_text = f"Type {action['text']!r}{suffix}"
    elif action["type"] == "open_app":
        action_text = f"Open {action['app_name']}"
    else:
        action_text = f"Play recording {action['recording_name']}"

    kind = schedule.get("kind")
    timezone = _clean_text(schedule.get("timezone"))
    if kind == "once":
        schedule_text = f"once at {_clean_text(schedule.get('run_at')) or 'the requested time'}"
    elif kind == "daily":
        schedule_text = f"daily at {_clean_text(schedule.get('time')) or 'the requested time'}"
    elif kind == "weekdays":
        schedule_text = f"weekdays at {_clean_text(schedule.get('time')) or 'the requested time'}"
    else:
        weekday = _clean_text(schedule.get("weekday")) or "weekly"
        schedule_text = f"{weekday} at {_clean_text(schedule.get('time')) or 'the requested time'}"
    if timezone:
        schedule_text += f" {timezone}"

    details: list[str] = []
    if target.get("app_name"):
        target_text = f"Target app: {target['app_name']}"
        if target.get("window_title_hint"):
            target_text += f", window: {target['window_title_hint']}"
        details.append(target_text)
    if safety.get("bring_app_to_front"):
        details.append("bring app to front first")
    if safety.get("require_app_in_foreground"):
        details.append("requires target app foreground")

    suffix = f" ({'; '.join(details)})" if details else ""
    return f"{action_text} on {schedule_text} schedule{suffix}."


def _workflow_schedule_fields(schedule: dict[str, Any]) -> dict[str, Any]:
    kind = str(schedule.get("kind") or "").strip().lower()
    timezone = _clean_text(schedule.get("timezone"))
    time_value = _clean_text(schedule.get("time"))
    if kind == "once":
        return {
            "schedule_enabled": True,
            "schedule_preset": "once",
            "schedule_time": _clean_text(schedule.get("run_at")),
            "schedule_days": "",
            "schedule_timezone": timezone,
        }
    if kind == "daily":
        return {
            "schedule_enabled": True,
            "schedule_preset": "daily",
            "schedule_time": time_value,
            "schedule_days": "",
            "schedule_timezone": timezone,
        }
    if kind == "weekdays":
        return {
            "schedule_enabled": True,
            "schedule_preset": "weekly",
            "schedule_time": time_value,
            "schedule_days": "1,2,3,4,5",
            "schedule_timezone": timezone,
        }
    weekday = _clean_text(schedule.get("weekday")).lower()
    return {
        "schedule_enabled": True,
        "schedule_preset": "weekly",
        "schedule_time": time_value,
        "schedule_days": WEEKDAY_TO_CRON_DAY.get(weekday, weekday or "1"),
        "schedule_timezone": timezone,
    }


def _instruction_for_action(action: dict[str, Any], safety: dict[str, Any]) -> str:
    prefix = ""
    if safety.get("bring_app_to_front"):
        prefix = "Bring the target app to the front first. "
    if safety.get("require_app_in_foreground"):
        prefix += "Only run if the target app is already foreground. "
    if action["type"] == "keypress":
        return f"{prefix}Press {action['key']}."
    if action["type"] == "type_text":
        suffix = " Then press Enter." if action.get("press_enter") else ""
        return f"{prefix}Type {action['text']!r}.{suffix}"
    if action["type"] == "open_app":
        return f"{prefix}Open {action['app_name']}."
    return f"{prefix}Play recording {action['recording_name']}."


def _step_for_action(spec: dict[str, Any]) -> dict[str, Any]:
    action = spec["action"]
    safety = spec.get("safety") or {}
    instruction = _instruction_for_action(action, safety)
    if action["type"] == "play_recording":
        return {
            "name": spec["title"],
            "position": 0,
            "action_type": "play_recording",
            "step_type": "play_recording",
            "instruction": instruction,
            "recording_filename": action["recording_name"],
            "config": {"recording_name": action["recording_name"]},
            "validation_type": "none",
        }
    return {
        "name": spec["title"],
        "position": 0,
        "action_type": "computer_use",
        "step_type": "computer_use",
        "instruction": instruction,
        "config": {
            "goal": instruction,
            "scheduled_action": action,
            "target_context": spec.get("target_context") or {},
            "safety": safety,
        },
        "validation_type": "none",
    }


def compile_scheduled_action_workflow(spec: dict[str, Any]) -> dict[str, Any]:
    """Compile a simple scheduled action into workflow-compatible payloads."""
    normalized = normalize_scheduled_action(spec)
    schedule_fields = _workflow_schedule_fields(normalized["schedule"])
    workflow = {
        "name": normalized["title"],
        "description": preview_scheduled_action(normalized),
        "workflow_type": "scheduled",
        "status": "active",
        **schedule_fields,
    }
    return {
        "workflow": workflow,
        "steps": [_step_for_action(normalized)],
        "scheduled_action": normalized,
        "preview": preview_scheduled_action(normalized),
    }
