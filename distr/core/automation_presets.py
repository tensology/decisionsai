"""Built-in automation presets for the Automations hub."""

from __future__ import annotations

from typing import Any


AUTOMATION_PRESETS: dict[str, dict[str, Any]] = {
    "daily_plan": {
        "preset_id": "daily_plan",
        "name": "Daily plan",
        "description": "Morning brief from boards, email, WhatsApp, and project context.",
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
    "whatsapp_to_tickets": {
        "preset_id": "whatsapp_to_tickets",
        "name": "WhatsApp → tickets",
        "description": "Sync WhatsApp, find work messages, and snapshot them into ticket boards.",
        "automation_type": "scheduled_instruction",
        "instruction": (
            "Run a WhatsApp work-intake pass:\n"
            "1. Sync WhatsApp relay messages into Decisions.\n"
            "2. List recent work-related WhatsApp messages that are not yet ticketed.\n"
            "3. For each clear action item, create a ticket on the best-matching board "
            "(use board/project links when available).\n"
            "4. Leave reply drafts in WhatsApp for anything that needs a human response.\n"
            "5. Summarize what you ticketed, skipped, and what still needs approval."
        ),
        "action_config": {},
        "schedule": {"kind": "interval", "interval": 120, "interval_unit": "minutes"},
    },
    "morning_inbox_scan": {
        "preset_id": "morning_inbox_scan",
        "name": "Morning inbox scan",
        "description": "Scan Gmail and WhatsApp for urgent items before the day starts.",
        "automation_type": "tool_action",
        "instruction": "Scan connected inboxes for urgent actionable work.",
        "action_config": {
            "tool": "proactive_orchestrator",
            "args": {"action": "scan", "source": "gmail", "limit": 15, "format": "summary"},
        },
        "schedule": {"kind": "daily", "time": "08:00"},
    },
    "whatsapp_work_pulse": {
        "preset_id": "whatsapp_work_pulse",
        "name": "WhatsApp work pulse",
        "description": "Quick scan of WhatsApp for messages that need tickets or replies.",
        "automation_type": "tool_action",
        "instruction": "Check WhatsApp for work-related messages needing follow-up.",
        "action_config": {
            "tool": "proactive_orchestrator",
            "args": {"action": "scan", "source": "whatsapp", "limit": 20, "format": "summary"},
        },
        "schedule": {"kind": "interval", "interval": 180, "interval_unit": "minutes"},
    },
    "timesheet_export_25th": {
        "preset_id": "timesheet_export_25th",
        "name": "Monthly timesheet export",
        "description": "On the 25th, export calendar time blocks per project to Downloads as Excel.",
        "automation_type": "scheduled_instruction",
        "instruction": (
            "Export my schedule timesheet for the current calendar month:\n"
            "1. Use the Automations calendar time blocks for this month (all boards/projects).\n"
            "2. Build the timesheet export for the month-to-date period.\n"
            "3. Save the Excel file to my Downloads folder with a clear filename "
            "(include month and year).\n"
            "4. Tell me the file path and a one-line summary of total hours per project."
        ),
        "action_config": {},
        "schedule": {"kind": "monthly", "time": "06:00", "days": "25"},
    },
    "end_of_day_wrap": {
        "preset_id": "end_of_day_wrap",
        "name": "End-of-day wrap",
        "description": "Summarize what moved today and what is still open for tomorrow.",
        "automation_type": "scheduled_instruction",
        "instruction": (
            "Run an end-of-day wrap-up:\n"
            "1. Review ticket boards for items completed, moved, or still blocked today.\n"
            "2. Check calendar time blocks logged today.\n"
            "3. List open loops from email, WhatsApp, or chat that need tomorrow.\n"
            "4. Give a short spoken summary plus a bullet list I can scan."
        ),
        "action_config": {},
        "schedule": {"kind": "daily", "time": "17:30"},
    },
    "weekly_board_review": {
        "preset_id": "weekly_board_review",
        "name": "Weekly board review",
        "description": "Monday review of stale, blocked, and overdue tickets across boards.",
        "automation_type": "scheduled_instruction",
        "instruction": (
            "Run a weekly ticket board review:\n"
            "1. List tickets that look stale, blocked, or overdue on active boards.\n"
            "2. Flag items with no recent activity or missing owners.\n"
            "3. Suggest the top five moves for this week (close, delegate, or escalate).\n"
            "4. Keep it scannable — no workflow jargon."
        ),
        "action_config": {},
        "schedule": {"kind": "weekly", "time": "09:00", "days": "1"},
    },
    "email_action_items": {
        "preset_id": "email_action_items",
        "name": "Email action items",
        "description": "Turn unread Gmail threads into tickets or a short action list.",
        "automation_type": "scheduled_instruction",
        "instruction": (
            "Review Gmail for actionable work:\n"
            "1. Find unread or recent threads that need a decision or task.\n"
            "2. Create tickets on the right board when the work is clear.\n"
            "3. Otherwise produce a numbered action list with sender and subject.\n"
            "4. Do not send email — only triage and ticket."
        ),
        "action_config": {},
        "schedule": {"kind": "daily", "time": "10:00"},
    },
    "jira_morning_intake": {
        "preset_id": "jira_morning_intake",
        "name": "Jira morning intake",
        "description": "Collate overnight Jira notification emails into one local ticket batch and Telegram digest.",
        "automation_type": "scheduled_instruction",
        "instruction": (
            "Run Jira morning intake as ONE batch, never email-by-email:\n"
            "1. Scan Gmail for Jira/Atlassian notification emails since the last check.\n"
            "2. Extract all issue keys, dedupe, and fetch live issue details from Jira.\n"
            "3. Stage new issues onto the active/linked local Ticket Board with create/intake batch "
            "(source_provider=jira, external_id=issue key). Skip keys already ticketed.\n"
            "4. Send ONE Telegram digest listing all new tickets with Run all / Prioritize / Ignore.\n"
            "5. Do NOT start workflows, comment on Jira, log time to Jira, or send email replies "
            "until I approve from Telegram after work completes.\n"
            "6. Summarize keys staged, skipped, and whether the digest was sent."
        ),
        "action_config": {},
        "schedule": {"kind": "daily", "time": "08:00"},
    },
    "friday_time_summary": {
        "preset_id": "friday_time_summary",
        "name": "Friday time summary",
        "description": "Weekly hours by project from calendar time blocks.",
        "automation_type": "scheduled_instruction",
        "instruction": (
            "Summarize this week's logged time from schedule calendar blocks:\n"
            "1. Total hours per project/board for the current week.\n"
            "2. Call out the largest blocks and any unticketed time.\n"
            "3. Note gaps where I should have logged time but did not.\n"
            "4. Keep the summary short enough to read in under a minute."
        ),
        "action_config": {},
        "schedule": {"kind": "weekly", "time": "16:00", "days": "5"},
    },
    "proactive_work_scan": {
        "preset_id": "proactive_work_scan",
        "name": "Afternoon work scan",
        "description": "Cross-source scan of boards, email, and chat for important work.",
        "automation_type": "tool_action",
        "instruction": "Scan all connected work sources for important actionable items.",
        "action_config": {
            "tool": "proactive_orchestrator",
            "args": {"action": "scan", "limit": 12, "format": "summary"},
        },
        "schedule": {"kind": "daily", "time": "14:00"},
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
