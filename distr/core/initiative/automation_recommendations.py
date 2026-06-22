"""Recommend Automations presets from Initiative work observations."""

from __future__ import annotations

from typing import Any

from distr.core.automation_resolver import find_automations_by_preset
from distr.core.automation_presets import get_automation_preset
from distr.core.initiative.proposed_action import ProposedAction


def _messages(scan: dict[str, Any], source: str) -> list[dict[str, Any]]:
    messages = scan.get("messages") if isinstance(scan, dict) else {}
    rows = messages.get(source) if isinstance(messages, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _proposals(scan: dict[str, Any]) -> list[dict[str, Any]]:
    rows = scan.get("proposals") if isinstance(scan, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _proposal_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _active_preset_exists(preset_id: str) -> bool:
    return bool(find_automations_by_preset(preset_id, active_only=True))


def _action_for_preset(preset_id: str, reason: str) -> ProposedAction | None:
    if _active_preset_exists(preset_id):
        return None
    preset = get_automation_preset(preset_id)
    if not preset:
        return None
    name = str(preset.get("name") or preset_id)
    description = f"{reason} I can add the {name} automation to check this on a schedule."
    return ProposedAction(
        action_type="automation_recommendation",
        description=description,
        payload={
            "preset_id": preset_id,
            "preset_name": name,
            "source": "initiative_automation_recommendation",
            "confidence": 0.74,
            "risk_level": "low",
        },
        draft=description,
        telegram_message=description,
        requires_confirmation=True,
    )


def recommend_automation_from_work_scan(scan: dict[str, Any]) -> ProposedAction | None:
    """Return one approval-only automation recommendation for a work scan."""
    scan = scan if isinstance(scan, dict) else {}
    proposals = _proposals(scan)

    whatsapp_rows = _messages(scan, "whatsapp")
    whatsapp_proposals = [
        p for p in proposals if str(_proposal_payload(p).get("source") or "").lower() == "whatsapp"
    ]
    if whatsapp_rows or whatsapp_proposals:
        linked = any(
            bool(row.get("linked_board_id") or row.get("auto_snapshot"))
            for row in whatsapp_rows
        ) or any(
            bool(_proposal_payload(row).get("linked_board_id") or _proposal_payload(row).get("auto_snapshot"))
            for row in whatsapp_proposals
        )
        preset_id = "whatsapp_to_tickets" if linked else "whatsapp_work_pulse"
        return _action_for_preset(
            preset_id,
            "I'm seeing WhatsApp messages that look like work intake.",
        )

    email_rows = _messages(scan, "email")
    email_proposals = [
        p for p in proposals if str(_proposal_payload(p).get("source") or "").lower() == "email"
    ]
    if email_rows or email_proposals:
        return _action_for_preset(
            "email_action_items",
            "I'm seeing email that looks actionable.",
        )

    board_actions = {"board_triage", "ticket_lane_move", "workflow_start", "project_cli_task"}
    if any(str(row.get("action_type") or "") in board_actions for row in proposals):
        return _action_for_preset(
            "proactive_work_scan",
            "I'm seeing board or project work that may benefit from a recurring scan.",
        )

    triage = scan.get("orchestrator_triage") if isinstance(scan.get("orchestrator_triage"), dict) else {}
    if triage and (triage.get("candidates") or triage.get("summary")):
        return _action_for_preset(
            "daily_plan",
            "I'm seeing enough cross-source work context to build a daily plan.",
        )

    return None


def maybe_suggest_automation_from_initiative(service: Any, settings: dict[str, Any]) -> None:
    """Queue one automation preset recommendation through Initiative approvals."""
    level = service._get_level(settings)
    if level == "observe":
        return
    try:
        bundle = service._context_assembler.build(settings)
    except Exception:
        return
    action = recommend_automation_from_work_scan(getattr(bundle, "work_scan", {}) or {})
    if action is None:
        return
    try:
        from distr.core.initiative.tiers import PermissionTier

        service._draft_and_ask(action, settings, tier=PermissionTier.APPROVE)
    except Exception:
        return
