"""IDE work-packet handoff for Cursor and Codex plugin sessions."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import ProjectTask

logger = logging.getLogger(__name__)

WORK_PACKET_GLOB_PATTERNS = ("ticket_*.md", "decisionsai_*.md")

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


def _slugify_ticket_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return text[:48] or "work"


def _ticket_title_for_task(task: ProjectTask) -> str:
    title = str(getattr(task, "ticket_title", "") or "").strip()
    if title:
        return title
    ticket_id = getattr(task, "ticket_id", None)
    if not ticket_id:
        return ""
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanTicket

        with get_session() as db:
            row = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
            if row and row.title:
                return str(row.title).strip()
    except Exception:
        logger.debug("Could not load ticket title for work packet", exc_info=True)
    return f"Ticket #{ticket_id}"


def work_packet_filename(task: ProjectTask, *, backend_id: str) -> str:
    """Name work packets after the ticket, not the harness backend."""
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    step_part = int(task.step_id or 0)
    ticket_id = getattr(task, "ticket_id", None)
    if ticket_id:
        slug = _slugify_ticket_name(_ticket_title_for_task(task))
        return f"ticket_{int(ticket_id)}_{slug}_{stamp}_s{step_part}.md"
    return f"decisionsai_{backend_id}_{stamp}_{step_part}.md"


def write_ide_work_packet(
    task: ProjectTask,
    *,
    backend_id: str,
    meta: dict[str, Any],
    loop_context_summary: str = "",
    step_meta: dict[str, Any] | None = None,
) -> str:
    """Write a workflow work packet under the project .tickets folder."""
    from distr.core.workflow.step_iteration import (
        HARNESS_REPORT_TEMPLATE,
        build_step_iteration_protocol,
        load_step_handoff_meta,
    )

    folder = (task.folder or "").strip()
    if not folder:
        raise ValueError("Project folder is required for IDE handoff.")

    tickets_dir = os.path.join(folder, ".tickets")
    os.makedirs(tickets_dir, exist_ok=True)
    filename = work_packet_filename(task, backend_id=backend_id)
    path = os.path.join(tickets_dir, filename)

    ticket_title = _ticket_title_for_task(task) or (task.project_name or "Workflow step")
    companion_root_path = ""
    pickup_brief = ""
    if task.ticket_id:
        try:
            from distr.core.workspace_memory.lifecycle import hook_ensure_workspace
            from distr.core.workspace_memory.paths import companion_root
            from distr.core.workspace_memory.pickup_handoff import build_pickup_brief, load_decisions_json

            hook_ensure_workspace("tickets", int(task.ticket_id), reason="ide_work_packet")
            companion_root_path = str(companion_root("tickets", int(task.ticket_id)))
            pickup_brief = build_pickup_brief(
                entity_type="tickets",
                entity_id=int(task.ticket_id),
                decisions=load_decisions_json("tickets", int(task.ticket_id)),
                title=ticket_title,
            )
        except Exception:
            logger.debug("work packet: ticket companion failed", exc_info=True)
    if not companion_root_path and task.project_id:
        try:
            from distr.core.workspace_memory.lifecycle import hook_ensure_workspace
            from distr.core.workspace_memory.paths import companion_root
            from distr.core.workspace_memory.pickup_handoff import build_pickup_brief, load_decisions_json

            hook_ensure_workspace("projects", int(task.project_id), reason="ide_work_packet")
            companion_root_path = str(companion_root("projects", int(task.project_id)))
            if not pickup_brief:
                pickup_brief = build_pickup_brief(
                    entity_type="projects",
                    entity_id=int(task.project_id),
                    decisions=load_decisions_json("projects", int(task.project_id)),
                    title=ticket_title,
                )
        except Exception:
            logger.debug("work packet: project companion failed", exc_info=True)
    if companion_root_path:
        meta["companion_root"] = companion_root_path
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    companion_handoff = ""
    if task.project_id:
        try:
            from distr.core.workspace_memory.paths import companion_memory_file

            companion_handoff = str(companion_memory_file("projects", int(task.project_id), "handoff.md"))
            meta["companion_handoff"] = companion_handoff
            meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            logger.debug("work packet: companion handoff path failed", exc_info=True)
    handoff_meta = step_meta if isinstance(step_meta, dict) and step_meta else load_step_handoff_meta(
        getattr(task, "step_id", None)
    )
    iteration_protocol = build_step_iteration_protocol(handoff_meta)
    body_parts = [
        f"<!-- decisions-meta: {meta_json} -->",
        f"<!-- decisions-ide-meta: {meta_json} -->",
        "---",
        "mode: append",
        "auto_continue_on_pickup: true",
        "callback_payload_type: workflow_continue",
        "---",
        "",
        f"# {ticket_title}",
        "",
        f"Project: {task.project_name} ({task.project_id})",
        "",
        iteration_protocol,
        "",
    ]
    if loop_context_summary.strip():
        body_parts.extend(["## Loop context (summary)", "", loop_context_summary.strip(), ""])
    if pickup_brief.strip():
        body_parts.extend(["## Pick up brief", "", pickup_brief.strip(), ""])
    if companion_root_path:
        body_parts.extend(
            [
                "## Agent map",
                "",
                f"Companion root: `{companion_root_path}`",
                f"Read `{folder}/.decisions/agents.md` (or repo `AGENTS.md`) then follow `router.md`.",
                "",
            ]
        )
    elif companion_handoff:
        body_parts.extend(["## Workspace memory", "", f"Companion handoff: `{companion_handoff}`", ""])
    body_parts.extend(
        [
            "## Instruction",
            "",
            task.instruction.strip(),
            "",
            _callback_block(backend_id, meta),
            "## Return contract",
            "",
            "Report to the orchestrator using this exact shape (one field per line):",
            "",
            HARNESS_REPORT_TEMPLATE,
            "",
            "The workflow records this on the run. The human reviews via the orchestrator, not in-editor micro-management.",
            "",
            f"Reporter: python3 {json.dumps(meta.get('reporter') or _reporter_path(backend_id))} "
            '--turn-output "<paste the Return contract block>"',
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
    """Open the project folder in Cursor/VS Code (harness starts separately)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    folder = (folder or "").strip()
    if not folder or not os.path.isdir(folder):
        return False
    command = _ide_open_command()
    if not command:
        return False
    try:
        subprocess.Popen(
            [command, folder],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _cursor_harness_prompt(task: ProjectTask, packet_path: str) -> str:
    folder = (task.folder or "").strip()
    rel_packet = os.path.relpath(packet_path, folder) if folder else packet_path
    steering = str(getattr(task, "run_briefing_steering", "") or "").strip()
    parts = [
        f"Open and execute the workflow work packet at `{rel_packet}`.",
        "Follow the Instruction section exactly for this step.",
        "Use the decisions-cursor-worker skill and report progress through the reporter in the packet.",
    ]
    if steering:
        parts.append(f"Human steering for this run: {steering}")
    return " ".join(parts)


def start_cursor_harness_agent(task: ProjectTask, packet_path: str) -> dict[str, Any]:
    """Start cursor-agent in the project folder using the work packet as source of truth."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"started": False, "reason": "test_mode"}
    from .registry import _cursor_api_key, _cursor_auth_ready, _first_executable

    agent = _first_executable(["cursor-agent"])
    if not agent:
        return {"started": False, "reason": "cursor-agent missing"}
    if not _cursor_auth_ready(agent):
        return {"started": False, "reason": "cursor-agent not authenticated"}

    folder = (task.folder or "").strip()
    if not folder or not os.path.isdir(folder):
        return {"started": False, "reason": "project folder missing"}

    prompt = _cursor_harness_prompt(task, packet_path)
    env = {**os.environ, "TERM": "dumb"}
    api_key = _cursor_api_key()
    if api_key:
        env["CURSOR_API_KEY"] = api_key

    try:
        subprocess.Popen(
            [agent, "--trust", "-p", prompt],
            cwd=folder,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception as exc:
        logger.warning("cursor-agent harness failed to start: %s", exc)
        return {"started": False, "reason": str(exc)}

    reporter = _reporter_path("cursor_ide")
    try:
        subprocess.Popen(
            [
                "python3",
                reporter,
                "--cwd",
                folder,
                "--event-type",
                "cursor_started",
                "--status",
                "observed",
                "--message",
                "Harness started for workflow work packet.",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.debug("cursor_started reporter failed", exc_info=True)

    return {"started": True, "agent": agent, "prompt": prompt}
