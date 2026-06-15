"""Built-in automation presets for the Automations hub."""

from __future__ import annotations

from typing import Any


AUTOMATION_PRESETS: dict[str, dict[str, Any]] = {
    "daily_plan": {
        "preset_id": "daily_plan",
        "name": "Daily plan",
        "description": (
            "Build today's practical work plan from boards, projects, email, "
            "IDE context, and orchestrator triage. Runs the proactive orchestrator "
            "daily-plan tool on your schedule."
        ),
        "automation_type": "tool_action",
        "instruction": (
            "Build today's practical work plan from connected Decisions intelligence."
        ),
        "action_config": {
            "tool": "proactive_orchestrator",
            "args": {"action": "daily_plan", "format": "summary"},
        },
        "schedule": {"kind": "daily", "time": "09:00"},
    },
}


def list_automation_presets() -> list[dict[str, Any]]:
    """Return preset definitions for the Automations UI."""
    rows: list[dict[str, Any]] = []
    for preset in AUTOMATION_PRESETS.values():
        rows.append(
            {
                "preset_id": preset["preset_id"],
                "name": preset["name"],
                "description": preset.get("description") or "",
                "automation_type": preset.get("automation_type") or "scheduled_instruction",
                "instruction": preset.get("instruction") or "",
                "action_config": dict(preset.get("action_config") or {}),
                "schedule": dict(preset.get("schedule") or {"kind": "daily", "time": "09:00"}),
            }
        )
    return rows


def get_automation_preset(preset_id: str) -> dict[str, Any] | None:
    key = str(preset_id or "").strip().lower()
    if not key:
        return None
    preset = AUTOMATION_PRESETS.get(key)
    if not preset:
        return None
    return {
        "preset_id": preset["preset_id"],
        "name": preset["name"],
        "description": preset.get("description") or "",
        "automation_type": preset.get("automation_type") or "scheduled_instruction",
        "instruction": preset.get("instruction") or "",
        "action_config": dict(preset.get("action_config") or {}),
        "schedule": dict(preset.get("schedule") or {"kind": "daily", "time": "09:00"}),
    }
