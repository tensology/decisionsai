"""IDE work-packet handoff for Cursor and Codex plugin sessions."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import ProjectTask

IDE_BACKEND_IDS = frozenset({"cursor_ide", "codex_ide"})


def is_ide_backend(backend_id: str | None) -> bool:
    raw = (backend_id or "").strip().lower().replace("-", "_").replace(" ", "_")
    return raw in IDE_BACKEND_IDS


def plugin_source_for(backend_id: str) -> str:
    """IDE bridge / plugin source id (cursor or codex)."""
    if (backend_id or "").strip().lower().startswith("codex"):
        return "codex"
    return "cursor"


def model_backend_for(backend_id: str) -> str:
    """Map IDE backend to its CLI sibling for model lists."""
    if backend_id == "codex_ide":
        return "codex"
    if backend_id == "cursor_ide":
        return "cursor"
    return backend_id


def _reporter_path(backend_id: str) -> str:
    if backend_id == "codex_ide":
        return os.environ.get(
            "DECISIONS_CODEX_REPORTER",
            os.path.expanduser("~/plugins/decisions-codex/scripts/report_decisions_event.py"),
        )
    return os.environ.get(
        "DECISIONS_CURSOR_REPORTER",
        os.path.expanduser("~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py"),
    )


def _callback_block(backend_id: str, meta: dict[str, Any]) -> str:
    label = "CODEX" if backend_id == "codex_ide" else "CURSOR"
    prefix = "codex" if backend_id == "codex_ide" else "cursor"
    reporter = meta.get("reporter") or _reporter_path(backend_id)
    bridge_url = meta.get("bridge_url") or ""
    event_started = f"{prefix}_started"
    event_prompt = f"{prefix}_prompt_submitted"
    return (
        f"[DECISIONS {label} CALLBACK]\n"
        f"{json.dumps(meta, ensure_ascii=False, separators=(',', ':'))}\n"
        "When this work is opened, prompted, steered, paused, interrupted, blocked, completed, "
        "or materially updated, report the event back to DecisionsAI if DecisionsAI is reachable. "
        "Prefer the reporter script when available (meta is auto-discovered from this packet):\n"
        f"python3 {json.dumps(reporter)} --cwd {json.dumps(meta.get('project_folder') or '')} "
        f'--turn-output "Status: completed\\nSummary: <what changed>"\n'
        f"Use event_type values: {event_started}, {event_prompt}, user_steer, {prefix}_waiting, "
        f"{prefix}_interrupted, {prefix}_progress, {prefix}_completed, {prefix}_failed, {prefix}_needs_input.\n"
        "Do not wait until the final answer if the human submits another prompt, changes direction, "
        "or adds constraints mid-run.\n"
        f"[/DECISIONS {label} CALLBACK]\n\n"
    )


def build_ide_callback_meta(task: ProjectTask, *, backend_id: str, handoff_event_id: int | None = None) -> dict[str, Any]:
    from .registry import _decisions_api_base, _handoff_callback_metadata, _with_internal_token

    api_base = _decisions_api_base()
    callback = _handoff_callback_metadata(task)
    continue_url = callback.get("continue_url") or ""
    bridge_url = callback.get("bridge_url") or ""
    if not continue_url and task.workflow_id and task.run_id:
        continue_url = _with_internal_token(
            f"{api_base}/api/workflows/{int(task.workflow_id)}/runs/{int(task.run_id)}/continue"
        )
    if not bridge_url and task.workflow_id and task.run_id:
        bridge_url = _with_internal_token(
            f"{api_base}/api/workflows/{int(task.workflow_id)}/runs/{int(task.run_id)}/codex-events"
        )
    reporter = _reporter_path(backend_id)
    return {
        "project_id": task.project_id,
        "project_name": task.project_name,
        "project_folder": task.folder,
        "backend": backend_id,
        "origin": task.origin,
        "run_id": task.run_id,
        "workflow_id": task.workflow_id,
        "step_id": task.step_id,
        "ticket_id": task.ticket_id,
        "execution_session_id": task.execution_session_id,
        "handoff_event_id": handoff_event_id,
        "api_base": api_base,
        "callback_url": continue_url,
        "continue_url": continue_url,
        "bridge_url": bridge_url,
        "callback_payload_type": "workflow_continue",
        "reporter": reporter,
    }


def write_ide_work_packet(
    task: ProjectTask,
    *,
    backend_id: str,
    meta: dict[str, Any],
    loop_context_summary: str = "",
) -> str:
    """Write a DecisionsAI work packet under the project .tickets folder."""
    folder = (task.folder or "").strip()
    if not folder:
        raise ValueError("Project folder is required for IDE handoff.")

    tickets_dir = os.path.join(folder, ".tickets")
    os.makedirs(tickets_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    step_part = int(task.step_id or 0)
    filename = f"decisionsai_{backend_id}_{stamp}_{step_part}.md"
    path = os.path.join(tickets_dir, filename)

    backend_label = "Codex IDE" if backend_id == "codex_ide" else "Cursor IDE"
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    body_parts = [
        f"<!-- decisions-meta: {meta_json} -->",
        f"<!-- decisions-ide-meta: {meta_json} -->",
        "---",
        "mode: append",
        "auto_continue_on_pickup: false",
        "callback_payload_type: workflow_continue",
        "---",
        "",
        "# DecisionsAI Work Packet",
        "",
        f"Project: {task.project_name} ({task.project_id})",
        f"Backend: {backend_label}",
        "",
    ]
    if loop_context_summary.strip():
        body_parts.extend(["## Loop Context", "", loop_context_summary.strip(), ""])
    body_parts.extend(
        [
            "## Instruction",
            "",
            task.instruction.strip(),
            "",
            _callback_block(backend_id, meta),
            "## Return Contract",
            "",
            "When finished, report back to DecisionsAI with:",
            "- Status: completed | failed | needs_input",
            "- Summary",
            "- Files changed",
            "- Tests run",
            "- Blockers or next step",
            "",
            "## Callback",
            "",
            "The workflow stays waiting until you report completion.",
            f"- Reporter: python3 {json.dumps(meta.get('reporter') or _reporter_path(backend_id))} "
            '--turn-output "Status: completed\\nSummary: ..."',
        ]
    )
    if meta.get("continue_url"):
        body_parts.append(f"- Resume workflow: POST {meta['continue_url']}")
    if meta.get("bridge_url"):
        body_parts.append(f"- Bridge events: POST {meta['bridge_url']}")

    path_obj = Path(path)
    path_obj.write_text("\n".join(body_parts) + "\n", encoding="utf-8")
    return str(path_obj.resolve())


def _ide_open_command() -> str | None:
    from .registry import _first_executable

    return _first_executable(["cursor", "code"])


def open_ide_project(folder: str, packet_path: str = "") -> bool:
    """Open the project folder (and optionally the work packet) in Cursor/VS Code."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    folder = (folder or "").strip()
    if not folder or not os.path.isdir(folder):
        return False
    command = _ide_open_command()
    if not command:
        return False
    target = packet_path if packet_path and os.path.isfile(packet_path) else folder
    try:
        subprocess.Popen(
            [command, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False
