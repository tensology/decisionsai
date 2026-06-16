"""In-step iteration protocol for harness handoffs (develop → test → assess → correct)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

HARNESS_REPORT_FIELDS = (
    "status",
    "summary",
    "tests_run",
    "drift_check",
    "security",
    "ui_assessment",
    "self_corrections",
    "files_changed",
    "blockers",
)

_FIELD_ALIASES = {
    "status": ("status",),
    "summary": ("summary",),
    "tests_run": ("tests run", "tests_run", "tests"),
    "drift_check": ("drift check", "drift_check", "drift"),
    "security": ("security",),
    "ui_assessment": ("ui assessment", "ui_assessment", "ui"),
    "self_corrections": ("self-corrections", "self corrections", "self_corrections", "corrections"),
    "files_changed": ("files changed", "files_changed", "files"),
    "blockers": ("blockers", "blocker"),
}

HARNESS_REPORT_TEMPLATE = """Status: completed | failed | needs_input
Summary: <plain English — what you did this iteration>
Tests run: <commands + pass/fail; or N/A for non-code step>
Drift check: <scope/UI/logic drift vs ticket, or none>
Security: <findings or none>
UI assessment: <browser checks + illogical elements, or N/A>
Self-corrections: <what you fixed before reporting>
Files changed: <paths or none>
Blockers: <none or what stops progress>"""


def build_step_iteration_protocol(step_meta: dict[str, Any] | None) -> str:
    """Markdown block telling the harness how to iterate inside one workflow step."""
    meta = step_meta if isinstance(step_meta, dict) else {}
    parts = [
        "## Iteration protocol",
        "",
        "You are executing **one workflow step** managed by the DecisionsAI orchestrator.",
        "Do not wait for the human to micro-manage sub-tasks in the editor.",
        "Loop until this step passes validation or you report `needs_input`:",
        "",
        "1. **Do** — only what this step asks; stay on linked ticket scope.",
        "2. **Test** — run the project's real lint/test/build commands from repo config.",
        "   For UI work, exercise changed flows in the browser and note console errors.",
        "3. **Self-assess** — drift vs ticket/plan, security basics, illogical UI, regression risk.",
        "4. **Correct** — fix failures before reporting complete; never weaken tests or exit criteria.",
        "5. **Report** — use the Return Contract; the orchestrator stores it on the workflow run.",
        "",
    ]
    skills = [str(s).strip() for s in (meta.get("skills") or []) if str(s).strip()]
    if skills:
        parts.extend(["### Use these skills when relevant", "", ", ".join(skills), ""])
    validation = str(meta.get("validation_prompt") or "").strip()
    if validation:
        parts.extend(["### Pass when", "", validation, ""])
    checklist = meta.get("failure_checklist") or []
    if isinstance(checklist, list) and checklist:
        parts.append("### Fail if")
        parts.append("")
        for item in checklist:
            text = str(item).strip()
            if text:
                parts.append(f"- {text}")
        parts.append("")
    guardrail = str(meta.get("guardrail") or "").strip()
    if guardrail:
        parts.extend(["### Guardrails", "", guardrail, ""])
    return "\n".join(parts).strip()


def load_step_handoff_meta(step_id: int | None) -> dict[str, Any]:
    """Load step skills, validation, and guardrails for IDE work packets."""
    if not step_id:
        return {}
    try:
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflowStep

        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
            if not step:
                return {}
            cfg: dict[str, Any] = {}
            if step.config:
                try:
                    cfg = json.loads(step.config) or {}
                except Exception:
                    cfg = {}
            return {
                "step_name": (step.name or "").strip(),
                "action_type": (step.action_type or step.step_type or "").strip(),
                "validation_prompt": (step.validation_prompt or cfg.get("validation_prompt") or "").strip(),
                "validation_type": (step.validation_type or cfg.get("validation_type") or "").strip(),
                "skills": list(cfg.get("skills") or []),
                "failure_checklist": list(cfg.get("failure_checklist") or []),
                "guardrail": str(cfg.get("guardrail") or "").strip(),
            }
    except Exception:
        logger.debug("load_step_handoff_meta failed", exc_info=True)
        return {}


def build_ide_step_instruction(
    step_data: dict[str, Any],
    run_id: int | None,
    *,
    config: dict[str, Any] | None = None,
) -> str:
    """Slim instruction for IDE harness: step task + ticket context + steering only."""
    cfg = config if isinstance(config, dict) else {}
    step_name = (step_data.get("name") or cfg.get("name") or "Workflow step").strip()
    step_instruction = (
        (cfg.get("instruction") or step_data.get("instruction") or "").strip()
    )
    parts = [f"## Step: {step_name}", "", step_instruction or "Complete this workflow step."]
    ticket_line = ""
    steering = ""
    if run_id is not None:
        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import KanbanTicket
            from distr.core.db.workflow import AutoWorkflowRun
            from distr.core.workflow.run_briefing import gather_run_briefing_context

            ctx = gather_run_briefing_context(int(run_id))
            if ctx and ctx.ticket_title:
                ticket_line = f"Linked ticket: {ctx.ticket_title}"
                if ctx.ticket_summary:
                    ticket_line += f" — {ctx.ticket_summary}"
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                if run and run.ticket_id:
                    ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
                    if ticket and not ticket_line:
                        ticket_line = f"Linked ticket: {(ticket.title or '').strip()}"
                if run and run.run_data:
                    run_data = json.loads(run.run_data or "{}") or {}
                    steering = (run_data.get("run_briefing_steering") or "").strip()
        except Exception:
            logger.debug("build_ide_step_instruction context failed", exc_info=True)
    if ticket_line:
        parts.extend(["", ticket_line])
    if steering:
        parts.extend(["", f"Human steering for this run: {steering}"])
    return "\n".join(parts).strip()


def parse_harness_step_report(text: str) -> dict[str, str]:
    """Parse structured harness completion text into orchestrator fields."""
    raw = (text or "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = _normalize_report_key(label)
        if not key:
            continue
        cleaned = value.strip()
        if cleaned:
            out[key] = cleaned
    if not out.get("summary") and raw and not out:
        out["summary"] = raw[:2000]
    return out


def _normalize_report_key(label: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", (label or "").strip().lower()).strip()
    for canonical, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if norm == re.sub(r"[^a-z0-9]+", " ", alias.lower()).strip():
                return canonical
    return ""


def record_harness_step_report(
    *,
    run_id: int,
    step_id: int | None,
    report_text: str,
    source: str = "harness",
    event_type: str = "cursor_completed",
) -> dict[str, Any]:
    """Persist harness iteration report on the workflow run for orchestrator review."""
    parsed = parse_harness_step_report(report_text)
    entry = {
        "step_id": step_id,
        "source": source,
        "event_type": event_type,
        "fields": parsed,
        "raw_excerpt": (report_text or "")[:4000],
    }
    try:
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
        from distr.core.kanban.result_packet import append_workflow_step_to_packet

        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run:
                return {"recorded": False, "reason": "run_not_found"}
            try:
                run_data = json.loads(run.run_data or "{}") or {}
            except Exception:
                run_data = {}
            history = list(run_data.get("step_reports") or [])
            history.append(entry)
            run_data["step_reports"] = history[-30:]
            run_data["latest_step_report"] = entry
            step_name = ""
            if step_id:
                step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
                if step:
                    step_name = (step.name or "").strip()
            summary = parsed.get("summary") or parsed.get("status") or "Harness step report"
            packet = run_data.get("result_packet") or {}
            improvements = list(packet.get("step_improvements") or [])
            improvements.append(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "summary": summary,
                    "tests_run": parsed.get("tests_run", ""),
                    "drift_check": parsed.get("drift_check", ""),
                    "security": parsed.get("security", ""),
                    "ui_assessment": parsed.get("ui_assessment", ""),
                    "self_corrections": parsed.get("self_corrections", ""),
                    "blockers": parsed.get("blockers", ""),
                    "source": source,
                }
            )
            packet["step_improvements"] = improvements[-25:]
            packet = append_workflow_step_to_packet(
                packet,
                step_name=step_name or f"Step {step_id or '?'}",
                step_status=parsed.get("status") or "reported",
                step_result=summary,
                run_status=run.status or "running",
            )
            run_data["result_packet"] = packet
            run.run_data = json.dumps(run_data)
            db.commit()
    except Exception as exc:
        logger.warning("record_harness_step_report failed: %s", exc)
        return {"recorded": False, "reason": str(exc)}

    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source=source,
            event_type="harness_step_report",
            status=parsed.get("status") or "reported",
            run_id=run_id,
            step_id=step_id,
            summary=(parsed.get("summary") or "")[:500],
            payload={"fields": parsed, "bridge_event_type": event_type},
        )
    except Exception:
        logger.debug("harness_step_report event failed", exc_info=True)

    return {"recorded": True, "fields": parsed}
