"""Canonical workflow tool capabilities shared by workflow execution surfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowTool:
    id: str
    label: str
    description: str


WORKFLOW_TOOL_REGISTRY: tuple[WorkflowTool, ...] = (
    WorkflowTool("agent", "Agent", "General agent reasoning and instruction execution."),
    WorkflowTool("playwright", "Playwright", "Browser automation with Playwright."),
    WorkflowTool("browser_use", "Browser use", "Browser-use agent/tooling for web flows."),
    WorkflowTool("computer_use", "Computer use", "Visual desktop or screen control."),
    WorkflowTool("cli", "CLI", "Project CLI execution through Pi, Codex, Cursor, Claude Code, or another configured CLI."),
    WorkflowTool("python", "Python", "Python script execution for data, files, or computation."),
    WorkflowTool("shell", "Shell", "Shell command execution."),
    WorkflowTool("http", "HTTP", "HTTP request execution."),
    WorkflowTool("macro", "Macro", "Replay a recorded workflow macro."),
    WorkflowTool("ytdlp", "yt-dlp", "Media download or extraction via yt-dlp."),
)

WORKFLOW_TOOL_IDS = tuple(tool.id for tool in WORKFLOW_TOOL_REGISTRY)

_ACTION_TOOLS: dict[str, tuple[str, ...]] = {
    "agent_instruction": ("agent",),
    "playwright": ("playwright", "browser_use"),
    "browser_use": ("browser_use",),
    "computer_use": ("computer_use",),
    "execute_code": ("python",),
    "run_command": ("shell",),
    "http_request": ("http",),
    "play_recording": ("macro",),
    "send_to_project_cli": ("cli",),
    "ytdlp": ("ytdlp", "cli"),
}

_ALIASES = {
    "browser": "browser_use",
    "project_cli": "cli",
    "ide": "cli",
    "cursor": "cli",
    "codex": "cli",
    "sidecar": "computer_use",
    "vision": "computer_use",
    "workflow_agent": "agent",
    "orchestrator": "agent",
    "other": "agent",
    "command": "shell",
    "terminal": "shell",
    "script": "python",
    "python_script": "python",
    "request": "http",
    "recording": "macro",
}


def normalize_tool_id(tool_id: str) -> str:
    raw = str(tool_id or "").strip().lower()
    if raw in WORKFLOW_TOOL_IDS:
        return raw
    return _ALIASES.get(raw, "")


def normalize_tool_list(tools: object) -> list[str]:
    if not isinstance(tools, list):
        return []
    normalized: list[str] = []
    for item in tools:
        tool_id = normalize_tool_id(str(item or ""))
        if tool_id and tool_id not in normalized:
            normalized.append(tool_id)
    return normalized


def tools_for_action(action_type: str) -> list[str]:
    action = str(action_type or "").strip()
    return list(_ACTION_TOOLS.get(action, ()))
