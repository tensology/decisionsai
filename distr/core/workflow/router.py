"""
StepRouter — step routing after completion.

Verify result → store result → determine next step → advance or end run.
Extracted from complete_step() in service.py and _advance_workflow_orchestration() in workflow.py.

**Validates: Requirements 3, 4, 7**
"""
import json
import hashlib
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from distr.core.db import get_session
from distr.core.db.time import utc_now_naive
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowRun,
    AutoWorkflowStep,
    AutoWorkflowStepResult,
)
from distr.core.workflow.tools import normalize_tool_list, tools_for_action
from distr.core.workflow.verification import (
    _ticket_acceptance_gate,
    _run_verification,
    build_validation_snapshot,
    recover_blocked_browser_validation,
    ticket_acceptance_findings,
)
from distr.core.workflow.runtime_contract import emit_step_activity, should_pause_after_step
from distr.core.kanban.result_packet import append_workflow_step_to_packet
from distr.core.kanban.ticket_audit import append_ticket_audit_entry
from distr.core.workflow.chat_trace import record_workflow_chat_event
from distr.gui.web.workflow_events import increment_workflow_updated
from distr.gui.web.kanban_events import increment_kanban_updated

logger = logging.getLogger(__name__)


def _apply_approved_provider_replacements(
    routes: dict[str, Any],
    replacements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep accepted provider swaps when a coordination plan is revised."""
    updated = dict(routes or {})
    for key, route in list(updated.items()):
        if not isinstance(route, dict):
            continue
        from distr.core.work_intake.execution_policy import (
            apply_approved_provider_replacements_to_route,
        )

        updated[key] = apply_approved_provider_replacements_to_route(
            route,
            replacements,
        )
    return updated

_EXPECTED_OUTPUT_ALIASES = {
    "unknowns": ("missing information", "open questions", "unresolved questions"),
    "ui_design_read_if_applicable": ("ui design read", "design direction"),
    # Coding CLIs use their standard completion headings even when the workflow
    # asks for equivalent snake_case fields. Preserve semantic contracts without
    # spending another model call solely to reformat a valid review.
    "review_findings": ("independent review findings", "summary"),
    "project_release_findings": (
        "project release finding",
        "ticket specific finding",
        "ticket specific findings",
        "security",
        "remaining risks",
    ),
    "browser_evidence": ("browser evidence", "ui assessment"),
    "visual_claim_verdicts": ("visual claim verdict", "ui assessment"),
    "check_results": ("tests run", "checks run"),
    "security_audit": ("security",),
    "final_fixes": ("final fixes", "self corrections", "self-corrections"),
    "final_check_results": ("tests run", "checks run", "test results"),
    "final_security_audit": ("security", "security audit"),
    # Standard coding-agent reports use these human headings. For a non-UI
    # ticket, an explicit UI assessment of N/A is still the required evidence;
    # demanding a second snake_case field caused valid audits to loop forever.
    "final_browser_evidence": ("browser evidence", "ui assessment"),
    "visual_quality_verdict": ("visual quality verdict", "ui assessment"),
}


def _has_expected_output(result: str, label: str) -> bool:
    normalized_label = " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())
    normalized_result = " ".join(
        re.sub(r"[^a-z0-9]+", " ", str(result or "").lower()).split()
    )
    if normalized_label and normalized_label in normalized_result:
        return True
    if label.lower() == "ship_verdict":
        # A normal CLI completion packet can express the verdict through its
        # terminal status plus an explicit blocker result instead of repeating
        # a second ship_verdict field. Do not infer PASS when blockers remain.
        terminal_completed = bool(re.search(r"(?im)^\s*status\s*:\s*completed\b", str(result or "")))
        blocker_free = bool(re.search(r"(?im)^\s*blockers?\s*:\s*(?:none|n\s*/?\s*a)\b", str(result or "")))
        if terminal_completed and blocker_free:
            return True
    aliases = _EXPECTED_OUTPUT_ALIASES.get(label.lower(), ())
    if not aliases:
        return False
    for line in str(result or "").splitlines():
        normalized_line = " ".join(
            re.sub(r"[^a-z0-9]+", " ", line.lower()).split()
        )
        if any(
            normalized_line == alias
            or normalized_line.startswith(alias + " ")
            or f" {alias} " in f" {normalized_line} "
            for alias in aliases
        ):
            return True
    return False


def _missing_expected_outputs(result: str, expected_outputs: list[Any]) -> list[str]:
    """Return named handoff fields absent from a worker's compact report."""
    missing: list[str] = []
    for raw in expected_outputs or []:
        label = str(raw or "").strip()
        if not label:
            continue
        if not _has_expected_output(result, label):
            missing.append(label)
    return missing


def _release_hold_findings(result: str, step_config: dict[str, Any]) -> list[str]:
    """Reject a final audit that names its fields but says the work cannot ship.

    Expected-output checks prove that a worker returned the requested headings;
    they do not prove the values under those headings are successful.  In
    particular, ``Ship verdict: HOLD`` must never advance into the reporting
    step merely because the words ``ship verdict`` are present.
    """
    if str(step_config.get("step_role") or "").strip().lower() not in {
        "review",
        "final_polish",
    }:
        return []
    text = str(result or "")
    plain = re.sub(r"[*_`]+", "", text)
    findings: list[str] = []
    verdict = re.search(
        r"(?im)\bship\s+verdict\s*(?::|-|\bis\b)?\s*"
        r"(hold|blocked|failed?|no[ -]?go|not\s+ready)\b",
        plain,
    )
    if verdict:
        findings.append(f"Final ship verdict is {verdict.group(1).upper()}.")
    terminal = re.search(
        r"(?im)^\s*status\s*:\s*(needs[_ -]?input|blocked|failed?|incomplete)\b",
        plain,
    )
    if terminal:
        findings.append(f"Final audit status is {terminal.group(1)}.")
    for match in re.finditer(r"(?im)^\s*(?:[-*]\s*)?blockers?\s*:\s*(.+)$", plain):
        blocker = match.group(1).strip()
        if re.match(
            r"(?i)^(?:none|n\s*/?\s*a|no\s+blockers?|not\s+applicable)\s*(?:[.;]|$)",
            blocker,
        ):
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", blocker.lower()).strip()
        if normalized and normalized not in {"none", "n a", "no blockers", "not applicable"}:
            findings.append(f"Final audit reports a blocker: {blocker[:300]}")
    # Keep this deterministic and conservative. A UI audit that explicitly
    # says its browser proof was blocked/not executed is not release evidence.
    if re.search(
        r"(?is)\b(?:browser|playwright|chromium)\b.{0,160}"
        r"\b(?:blocked|not\s+(?:run|executed)|could\s+not\s+(?:run|launch)|"
        r"unable\s+to\s+(?:run|launch)|stale|predate[sd]?)\b",
        plain,
    ):
        findings.append("Fresh browser evidence was not produced.")
    return list(dict.fromkeys(findings))


_DESTRUCTIVE_SCOPE_RE = re.compile(
    r"\b(?:delete|deleting|remove|removing|move|moving|modify|modifying|"
    r"quarantine|quarantining|neutralize|neutralise|rename|renaming)\b",
    re.IGNORECASE,
)
_PROTECTED_SCOPE_RE = re.compile(
    r"\b(?:do not|don't|must not|never|without)\b[^.\n;]{0,120}"
    r"\b(?:delete|deleting|remove|removing|move|moving|modify|modifying|"
    r"quarantine|quarantining|neutralize|neutralise|rename|renaming)\b",
    re.IGNORECASE,
)
_PATH_TOKEN_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*")


def _protected_scope_conflicts(ticket_context: str, result: str) -> list[dict[str, str]]:
    """Detect a worker proposing destructive work the ticket explicitly forbids.

    LLM validation is useful for judgment but must not approve an execution
    contract that reverses an explicit preserve/do-not-delete instruction.
    Path-shaped targets keep this deterministic and avoid guessing about prose.
    """
    protected: set[str] = set()
    for line in str(ticket_context or "").splitlines():
        if not _PROTECTED_SCOPE_RE.search(line):
            continue
        for match in _PATH_TOKEN_RE.finditer(line):
            token = match.group(0).strip("`'\"").lower()
            if token:
                protected.add(token)
                parts = [part for part in token.split("/") if part]
                if parts:
                    protected.add(parts[-1] + "/")

    conflicts: list[dict[str, str]] = []
    for clause in re.split(r"[.;\n]+", str(result or "")):
        lowered = clause.lower()
        if not _DESTRUCTIVE_SCOPE_RE.search(lowered):
            continue
        for target in sorted(protected, key=len, reverse=True):
            if target not in lowered:
                continue
            escaped = re.escape(target)
            explicitly_safe = bool(
                re.search(rf"\b(?:do not|don't|must not|never)\b[^.;]{{0,100}}{escaped}", lowered)
                or re.search(rf"{escaped}[^.;]{{0,100}}\b(?:without|must not|do not|don't|never)\b", lowered)
                or re.search(rf"\b(?:preserve|retain|inventory only)\b\s+(?:the\s+)?{escaped}", lowered)
            )
            if explicitly_safe:
                continue
            conflicts.append({"target": target, "proposal": clause.strip()[:500]})
            break
    return conflicts[:10]


def _inspection_budget_violation(
    step_config: dict[str, Any], observed_tool_calls: int, *, complexity: str = "medium"
) -> dict[str, Any]:
    """Return deterministic evidence when a bounded inspection over-searches."""
    from distr.core.workflow.control_policy import resolve_inspection_budget

    budget = resolve_inspection_budget(
        step_config.get("inspection_budget"),
        complexity=complexity,
    )
    maximum = int(budget.get("max_tool_calls") or 0)
    enforcement = str(budget.get("enforcement") or "hard")
    enforced_maximum = (
        int(budget.get("hard_max_tool_calls") or maximum)
        if enforcement == "soft"
        else maximum
    )
    observed = max(0, int(observed_tool_calls or 0))
    if not enforced_maximum or observed <= enforced_maximum:
        return {}
    evidence: dict[str, Any] = {
        "observed_tool_calls": observed,
        "max_tool_calls": maximum,
    }
    if enforcement == "soft":
        evidence.update({
            "hard_max_tool_calls": enforced_maximum,
            "enforcement": enforcement,
        })
    return evidence


def _effective_wait_prompt(
    run_data: dict[str, Any], *, default_prompt: str, result: str
) -> str:
    """Keep actionable provider/route questions instead of generic wait copy."""
    kind = str(run_data.get("waiting_kind") or "").strip().lower()
    if kind == "provider_preflight":
        return str(
            run_data.get("provider_preflight_prompt") or result or default_prompt
        ).strip()
    if kind == "route_approval":
        return str(run_data.get("route_approval_prompt") or result or default_prompt).strip()
    return str(default_prompt or result or "Workflow is waiting for input.").strip()


class StepRouter:
    """Decide what happens after a step completes.

    Two public entry points:

    - ``route(step_id, result, passed, run_id)`` — verify → store → route.
    - ``resume_from_feedback(step_id, run_id, feedback)`` — resume a waiting step.
    """

    # ── Public API ──────────────────────────────────────────────────

    def route(
        self,
        step_id: int,
        result: str,
        passed: bool,
        run_id: int,
        *,
        skip_wait: bool = False,
        skip_approval: bool = False,
    ) -> Dict[str, Any]:
        """After a step completes: verify → store result → determine next step.

        Returns one of:
            {"action": "next_step", "step_id": <id>}
            {"action": "end_run", "status": "completed"}
            {"action": "waiting", "notify_main_agent": True}
            {"action": "correction_retry", "step_id": <id>}

        Set ``skip_wait=True`` when resuming from feedback to avoid re-entering
        the wait state for a ``wait_for_continue`` step that has already waited.
        Set ``skip_approval=True`` when resuming after a human approval gate.
        """
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == step_id,
            ).first()
            if not step:
                return {"action": "end_run", "status": "failed", "error": "Step not found"}

            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.id == run_id,
            ).first()
            if run:
                try:
                    run_data = json.loads(run.run_data or "{}")
                except Exception:
                    run_data = {}
                correction_context = run_data.get("last_validation_correction")
                correction_context = (
                    correction_context if isinstance(correction_context, dict) else {}
                )
                prior_result = str(correction_context.get("prior_result") or "").strip()
                if (
                    prior_result
                    and int(correction_context.get("step_id") or 0) == int(step_id)
                    and prior_result not in str(result or "")
                ):
                    result = (
                        prior_result
                        + "\n\n[VALIDATION CORRECTION SUPPLEMENT]\n"
                        + str(result or "").strip()
                    ).strip()
                if run_data.pop("ide_handoff_pending", False):
                    run_data["waiting_kind"] = "ide_handoff"
                    run.run_data = json.dumps(run_data)
                    db.flush()
                    return self._enter_wait_state(db, step, run_id, result, passed)
                if run_data.pop("power_budget_pending", False):
                    interrupt = (
                        run_data.get("interrupt_context")
                        if isinstance(run_data.get("interrupt_context"), dict)
                        else {}
                    )
                    question = str(
                        interrupt.get("question")
                        or run_data.get("waiting_prompt")
                        or "The run power budget was exhausted. Raise the budget, change approach, or stop?"
                    )
                    recommendation = str(
                        interrupt.get("recommendation")
                        or "Raise the budget only if acceptance criteria still justify the spend."
                    )
                    run_data["waiting_kind"] = "control_interrupt"
                    run_data["waiting_prompt"] = (
                        f"{question} I recommend: {recommendation} "
                        "Reply with a choice, steer the run, or stop it."
                    )
                    run_data["interrupt_context"] = {
                        "should_interrupt": True,
                        "reason": str(interrupt.get("reason") or "The run power budget was exhausted."),
                        "question": question,
                        "recommendation": recommendation,
                        "options": list(interrupt.get("options") or ["Raise budget", "Change approach", "Stop"]),
                        "can_continue_default": False,
                    }
                    run_data["human_intervention_state"] = "needs_human_input"
                    run.run_data = json.dumps(run_data)
                    db.flush()
                    return self._enter_wait_state(db, step, run_id, run_data["waiting_prompt"], passed)
                if run_data.pop("route_approval_pending", False):
                    from distr.core.workflow.control_policy import decide_interruption

                    pending = (
                        run_data.get("pending_route_approval")
                        if isinstance(run_data.get("pending_route_approval"), dict)
                        else {}
                    )
                    provider = str(
                        pending.get("model_provider")
                        or pending.get("provider")
                        or ""
                    ).strip().lower()
                    paid = provider not in {"", "ollama", "local"}
                    run_data["paid_escalation_pending"] = paid
                    interruption = decide_interruption(
                        paid_escalation=True,
                        question=str(
                            run_data.get("route_approval_prompt")
                            or f"Approve route to {pending.get('backend') or 'the recommended worker'}?"
                        ),
                    )
                    run_data["waiting_kind"] = "route_approval"
                    run_data["waiting_prompt"] = interruption.question
                    run_data["interrupt_context"] = interruption.to_dict()
                    run_data["human_intervention_state"] = "needs_human_input"
                    run.run_data = json.dumps(run_data)
                    db.flush()
                    return self._enter_wait_state(db, step, run_id, interruption.question, passed)
                if run_data.pop("provider_preflight_pending", False):
                    from distr.core.workflow.control_policy import decide_interruption

                    interruption = decide_interruption(
                        paid_escalation=bool(run_data.get("paid_escalation_pending")),
                        question=str(
                            run_data.get("provider_preflight_prompt")
                            or run_data.get("waiting_prompt")
                            or result
                            or ""
                        ),
                    )
                    run_data["waiting_kind"] = "provider_preflight"
                    if interruption.should_interrupt:
                        run_data["waiting_prompt"] = interruption.question
                        run_data["interrupt_context"] = interruption.to_dict()
                    run.run_data = json.dumps(run_data)
                    db.flush()
                    return self._enter_wait_state(db, step, run_id, result, passed)

            # ── wait_for_continue gate ──
            # Skip when resuming from feedback (the step has already waited)
            if should_pause_after_step(
                run_id=run_id,
                step_wait_for_continue=bool(step.wait_for_continue),
                skip_wait=skip_wait,
            ):
                return self._enter_wait_state(db, step, run_id, result, passed)

            # ── verify ──
            verify_project_id = None
            validation_routes: list[dict[str, Any]] = []
            if run:
                try:
                    verify_run_data = json.loads(run.run_data or "{}") or {}
                    raw_project_id = verify_run_data.get("project_id")
                    verify_project_id = int(raw_project_id) if raw_project_id not in (None, "") else None
                    coordination = verify_run_data.get("coordination_plan") or {}
                    assignments = coordination.get("assignments") if isinstance(coordination, dict) else {}
                    assignment = assignments.get(str(step_id)) if isinstance(assignments, dict) else {}
                    validation_routes = [
                        dict(item) for item in (assignment.get("evaluation_routes") or [])
                        if isinstance(item, dict)
                    ] if isinstance(assignment, dict) else []
                except Exception:
                    verify_project_id = None
                    validation_routes = []
            ticket_context = ""
            standards_context = ""
            include_ui_standards = True
            try:
                from distr.core.db.kanban import KanbanTicket
                from distr.core.workflow.standards_memory import build_standards_context

                if run and getattr(run, "ticket_id", None):
                    ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
                    if ticket:
                        ticket_context = f"{getattr(ticket, 'title', '')}\n{getattr(ticket, 'description', '')}".strip()
                if run:
                    try:
                        brief_data = json.loads(run.run_data or "{}") or {}
                        execution_profile = brief_data.get("ticket_execution_profile") or {}
                        if isinstance(execution_profile, dict):
                            include_ui_standards = bool(
                                execution_profile.get("ui_evidence_required")
                            )
                        raw_brief = brief_data.get("ticket_workflow_brief") or ""
                        if isinstance(raw_brief, dict):
                            brief = json.dumps(raw_brief, ensure_ascii=False)
                        else:
                            brief = str(raw_brief).strip()
                        if brief:
                            ticket_context = (
                                f"{ticket_context}\n\n{brief}".strip()
                                if ticket_context
                                else brief
                            )
                    except Exception:
                        pass
                standards_context = build_standards_context(
                    getattr(run.workflow, "context_rules", None) if run and run.workflow else None,
                    board_id=getattr(run, "board_id", None) if run else None,
                    include_ui_standards=include_ui_standards,
                )
            except Exception:
                logger.debug("Could not build verification context", exc_info=True)

            verified_passed = _run_verification(
                step,
                result,
                passed,
                project_id=verify_project_id,
                ticket_context=ticket_context,
                standards_context=standards_context,
                validation_routes=validation_routes,
            )
            objective_acceptance_passed = bool(
                verified_passed
                and _ticket_acceptance_gate(
                    step,
                    result,
                    ticket_context,
                    project_id=verify_project_id,
                ) is True
            )
            read_only_evidence: dict[str, Any] = {}
            raw_step_config = getattr(step, "config", None)
            if isinstance(raw_step_config, dict):
                step_config = raw_step_config
            elif isinstance(raw_step_config, str):
                try:
                    step_config = json.loads(raw_step_config or "{}") or {}
                except Exception:
                    step_config = {}
            else:
                step_config = {}
            latest_execution = None
            inspection_budget_evidence: dict[str, Any] = {}
            if bool(step_config.get("read_only")) or isinstance(step_config.get("inspection_budget"), dict):
                try:
                    from sqlalchemy import func
                    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession

                    latest_execution = (
                        db.query(ProjectExecutionSession)
                        .filter(ProjectExecutionSession.run_id == int(run_id))
                        .filter(ProjectExecutionSession.step_id == int(step_id))
                        .order_by(ProjectExecutionSession.started_at.desc(), ProjectExecutionSession.id.desc())
                        .first()
                    )
                    if latest_execution and bool(step_config.get("read_only")):
                        execution_output = json.loads(latest_execution.output_packet or "{}") or {}
                        if bool(execution_output.get("read_only_violation")):
                            read_only_evidence = dict(execution_output.get("workspace_state_delta") or {})
                    if latest_execution and isinstance(step_config.get("inspection_budget"), dict):
                        observed_calls = int(
                            db.query(func.count(ProjectExecutionEvent.id))
                            .filter(ProjectExecutionEvent.session_id == int(latest_execution.id))
                            .filter(ProjectExecutionEvent.event_type == "tool_execution_start")
                            .scalar()
                            or 0
                        )
                        inspection_budget_evidence = _inspection_budget_violation(
                            step_config,
                            observed_calls,
                            complexity=str(latest_execution.complexity or "medium"),
                        )
                        if inspection_budget_evidence:
                            inspection_budget_evidence["execution_session_id"] = int(latest_execution.id)
                except Exception:
                    logger.debug("Workflow evidence gates could not inspect execution evidence", exc_info=True)
            missing_expected_outputs = _missing_expected_outputs(
                result,
                list(step_config.get("expected_outputs") or []),
            )
            acceptance_findings = ticket_acceptance_findings(
                step,
                result,
                ticket_context,
                project_id=verify_project_id,
            )
            protected_scope_conflicts = _protected_scope_conflicts(ticket_context, result)
            release_hold_findings = _release_hold_findings(result, step_config)
            host_browser_validation: dict[str, Any] = {}
            if release_hold_findings:
                recovery_context = result
                # A constrained reviewer may accurately report that its Node
                # command is blocked while abbreviating the filename. Reuse
                # exact commands already reported by earlier workers in this
                # same run; the recovery helper still enforces a project-local
                # test path, no shell, a clean exit, and fresh viewport media.
                try:
                    from distr.core.db.kanban import ProjectExecutionSession

                    prior_sessions = (
                        db.query(ProjectExecutionSession)
                        .filter(ProjectExecutionSession.run_id == int(run_id))
                        .filter(ProjectExecutionSession.step_id != int(step_id))
                        .order_by(ProjectExecutionSession.id.desc())
                        .limit(5)
                        .all()
                    )
                    prior_reports: list[str] = []
                    for session in prior_sessions:
                        packet = json.loads(session.output_packet or "{}") or {}
                        report = str(packet.get("output") or packet.get("summary") or "").strip()
                        if report:
                            prior_reports.append(report)
                    if prior_reports:
                        recovery_context += "\n\nEarlier same-run execution evidence:\n" + "\n".join(prior_reports)
                except Exception:
                    logger.debug("Could not load same-run browser command evidence", exc_info=True)
                host_browser_validation = recover_blocked_browser_validation(
                    recovery_context,
                    project_id=verify_project_id,
                )
                if host_browser_validation.get("passed"):
                    command = " ".join(host_browser_validation.get("command") or [])
                    media = ", ".join(host_browser_validation.get("fresh_media") or [])
                    result = (
                        result.rstrip()
                        + "\n\nAuthoritative Decisions host browser validation: PASSED."
                        + f"\nHost command: {command}."
                        + f"\nFresh desktop/mobile evidence: {media}."
                        + "\nFinal ship verdict override: SHIP.\nFinal blockers override: None."
                    )
                    release_hold_findings = []
            orchestrator_overlay = None
            if not objective_acceptance_passed:
                try:
                    from distr.core.orchestrator_validator import apply_orchestrator_validator_overlay
                    orchestrator_overlay = apply_orchestrator_validator_overlay(
                        step=step,
                        result=result,
                        caller_passed=passed,
                        mechanical_passed=verified_passed,
                        standards_context=standards_context,
                        ticket_context=ticket_context,
                        validation_routes=validation_routes,
                    )
                    if orchestrator_overlay is not None:
                        verified_passed = bool(orchestrator_overlay.get("passed"))
                except Exception:
                    logger.debug("Orchestrator validator overlay skipped", exc_info=True)

            # These are deterministic contract gates. An LLM validator may add
            # useful judgment but cannot overrule file-system evidence or omit
            # fields that downstream steps explicitly require.
            if (
                read_only_evidence
                or inspection_budget_evidence
                or missing_expected_outputs
                or acceptance_findings
                or protected_scope_conflicts
                or release_hold_findings
            ):
                verified_passed = False

            validation_snapshot = build_validation_snapshot(
                step, result, passed, verified_passed, project_id=verify_project_id
            )
            if read_only_evidence:
                validation_snapshot["read_only_violation"] = read_only_evidence
                validation_snapshot["correction_hint"] = (
                    "This step is read-only but changed workspace files. Revert only the files listed in the read-only evidence, "
                    "then rerun the step without writing project artifacts."
                )
            if inspection_budget_evidence:
                validation_snapshot["inspection_budget_violation"] = inspection_budget_evidence
                validation_snapshot["correction_hint"] = (
                    "The model inspected too broadly. Reuse the existing context packet and inspect only "
                    f"the files needed for this step within {inspection_budget_evidence['max_tool_calls']} tool calls."
                )
            if missing_expected_outputs:
                validation_snapshot["missing_expected_outputs"] = missing_expected_outputs
                validation_snapshot["correction_hint"] = (
                    "Return the missing named handoff fields without repeating full logs: "
                    + ", ".join(missing_expected_outputs)
                    + ". Use N/A with a ticket-specific reason when a conditional field does not apply."
                )
            if acceptance_findings:
                validation_snapshot["ticket_acceptance_findings"] = acceptance_findings
                validation_snapshot["correction_hint"] = " ".join(
                    item["correction_hint"] for item in acceptance_findings if item.get("correction_hint")
                )
            if protected_scope_conflicts:
                validation_snapshot["protected_scope_conflicts"] = protected_scope_conflicts
                validation_snapshot["correction_hint"] = (
                    "The proposed handoff conflicts with an explicit ticket preservation constraint. "
                    "Remove the destructive action and preserve/inventory these targets only: "
                    + ", ".join(sorted({item["target"] for item in protected_scope_conflicts}))
                    + "."
                )
            if release_hold_findings:
                validation_snapshot["release_hold_findings"] = release_hold_findings
                validation_snapshot["correction_hint"] = (
                    "Do not report this ticket complete. Resolve the release hold, run the missing "
                    "checks in a capable environment, and return fresh evidence before issuing SHIP. "
                    + " ".join(release_hold_findings)
                )
            if host_browser_validation:
                validation_snapshot["host_browser_validation"] = host_browser_validation
            if orchestrator_overlay:
                validation_snapshot["orchestrator_validator"] = orchestrator_overlay
                if not verified_passed:
                    hint = (
                        orchestrator_overlay.get("correction_hint")
                        or orchestrator_overlay.get("explanation")
                        or ""
                    ).strip()
                    if hint:
                        validation_snapshot["correction_hint"] = hint
            if standards_context:
                validation_snapshot["standards_context"] = standards_context

            # ── approval gate (after verify, before marking passed) ──
            if step.require_approval and verified_passed and not skip_approval:
                return self._enter_approval_state(db, step, run_id, result, verified_passed)

            status = "passed" if verified_passed else "failed"

            self._record_validation_progress(
                run,
                step,
                caller_passed=bool(passed),
                verified_passed=bool(verified_passed),
                validation_snapshot=validation_snapshot,
            )

            # ── store result ──
            step.status = status
            step.result = result
            step_result_row = AutoWorkflowStepResult(
                step_id=step_id,
                run_id=run_id,
                agent_response=result,
                status=status,
            )
            db.add(step_result_row)
            db.flush()

            interrupt = self._maybe_enter_control_interrupt(
                db,
                run=run,
                step=step,
                run_id=run_id,
                result=result,
                verified_passed=bool(verified_passed),
            )
            if interrupt:
                return interrupt

            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.id == run_id,
            ).first()
            if not run:
                db.commit()
                increment_workflow_updated()
                return {"action": "end_run", "status": status}

            # ── determine next step ──
            decision = self._determine_next(
                db,
                step,
                run,
                verified_passed,
                result,
                validation_snapshot=validation_snapshot,
            )
            emit_step_activity(
                run_id=run_id,
                step_id=step_id,
                event_type="workflow_step_route_decided",
                status="running" if decision.get("action") == "next_step" else decision.get("status", status),
                summary=(
                    f"Next step: {decision.get('step_id')}"
                    if decision.get("action") == "next_step"
                    else f"Run decision: {decision.get('status', status)}"
                ),
                payload={"decision": decision, "passed": bool(verified_passed)},
            )
            # ── update canonical result packet in run_data ──
            try:
                run_data = json.loads(run.run_data or "{}")
            except Exception:
                run_data = {}
            coordination_revision = None
            coordination_plan = run_data.get("coordination_plan")
            if isinstance(coordination_plan, dict) and coordination_plan:
                try:
                    from distr.core.settings import load_settings_from_db
                    from distr.core.workflow.coordination_plan import (
                        coordination_plan_routes,
                        revise_plan_after_step,
                    )

                    coordination_plan, coordination_revision = revise_plan_after_step(
                        coordination_plan,
                        completed_step_id=int(step_id),
                        next_step_id=(
                            int(decision.get("step_id"))
                            if decision.get("action") == "next_step" and decision.get("step_id")
                            else None
                        ),
                        passed=bool(verified_passed),
                        reason=(
                            validation_snapshot.get("correction_hint")
                            or f"{step.name or f'Step {step_id}'} failed validation; use a different viable worker for correction."
                        ),
                        settings=load_settings_from_db(),
                        actual_route=(
                            dict(run_data.get("execution_route") or {})
                            if isinstance(run_data.get("execution_route"), dict)
                            else {}
                        ),
                    )
                    run_data["coordination_plan"] = coordination_plan
                    planned_step_routes, planned_role_routes = coordination_plan_routes(coordination_plan)
                    approved_replacements = list(
                        run_data.get("approved_provider_replacements") or []
                    )
                    planned_step_routes = _apply_approved_provider_replacements(
                        planned_step_routes,
                        approved_replacements,
                    )
                    planned_role_routes = _apply_approved_provider_replacements(
                        planned_role_routes,
                        approved_replacements,
                    )
                    run_data["step_routes"] = planned_step_routes
                    run_data["step_role_routes"] = planned_role_routes
                except Exception:
                    logger.debug("Could not revise run coordination plan", exc_info=True)
            packet = run_data.get("result_packet") or {}
            packet = append_workflow_step_to_packet(
                packet,
                step_name=step.name or f"Step {step.id}",
                step_status=status,
                step_result=result or "",
                run_status=(
                    decision.get("status")
                    if decision.get("action") == "end_run"
                    else "running"
                ),
                validation_snapshot=validation_snapshot,
            )
            run_data["result_packet"] = packet
            run.run_data = json.dumps(run_data)
            # The orchestrator is the cross-cutting ledger and writes through its own
            # session. Persist the canonical step result/run packet first so
            # Orchestrator validation and event rows cannot be blocked by this write
            # transaction, especially on SQLite-backed local installs.
            db.commit()
            if coordination_revision:
                try:
                    from distr.core.orchestration_events import emit_orchestration_event

                    emit_orchestration_event(
                        source="orchestrator",
                        event_type="coordination_plan_revised",
                        status="ready",
                        workflow_id=run.workflow_id,
                        run_id=run_id,
                        step_id=step_id,
                        ticket_id=getattr(run, "ticket_id", None),
                        board_id=getattr(run, "board_id", None),
                        project_id=run_data.get("project_id"),
                        summary=(
                            f"Reallocated step #{coordination_revision.get('target_step_id')} after validation evidence changed."
                        ),
                        payload={"revision": coordination_revision},
                    )
                except Exception:
                    logger.debug("Could not emit coordination plan revision", exc_info=True)
            validation_record_id = None
            correction_attempt_id = None
            correction_packet: dict[str, Any] = {}
            try:
                from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
                from distr.core.orchestrator import (
                    build_correction_packet,
                    create_correction_attempt,
                    record_validation,
                )

                latest_execution = (
                    db.query(ProjectExecutionSession)
                    .filter(ProjectExecutionSession.run_id == int(run_id))
                    .filter(ProjectExecutionSession.step_id == int(step_id))
                    .order_by(ProjectExecutionSession.started_at.desc(), ProjectExecutionSession.id.desc())
                    .first()
                )
                validation_record_id = record_validation(
                    workflow_id=run.workflow_id,
                    run_id=run_id,
                    step_id=step_id,
                    step_result_id=getattr(step_result_row, "id", None),
                    ticket_id=getattr(run, "ticket_id", None),
                    board_id=getattr(run, "board_id", None),
                    project_id=(
                        getattr(latest_execution, "project_id", None)
                        or run_data.get("project_id")
                    ),
                    execution_session_id=getattr(latest_execution, "id", None),
                    validation_snapshot=validation_snapshot,
                    standards_context=standards_context,
                    payload={
                        "step_name": step.name or f"Step {step_id}",
                        "action_type": step.action_type,
                        "decision": decision,
                    },
                )
                if validation_record_id:
                    validation_snapshot["validation_record_id"] = validation_record_id
                if validation_record_id and not verified_passed:
                    ticket = None
                    if getattr(run, "ticket_id", None):
                        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(run.ticket_id)).first()
                    execution_input = {}
                    execution_output = {}
                    runtime_snapshot = {}
                    executor_output = ""
                    target_backend = ""
                    target_model = ""
                    if latest_execution:
                        try:
                            execution_input = json.loads(latest_execution.input_packet or "{}")
                        except Exception:
                            execution_input = {}
                        try:
                            execution_output = json.loads(latest_execution.output_packet or "{}")
                        except Exception:
                            execution_output = {}
                        runtime_snapshot = (
                            execution_input.get("runtime_snapshot")
                            or execution_output.get("runtime_snapshot")
                            or {}
                        )
                        executor_output = (
                            execution_output.get("output")
                            or execution_output.get("error")
                            or ""
                        )
                        target_backend = latest_execution.route_backend or execution_output.get("backend_id") or ""
                        target_model = latest_execution.selected_model or execution_input.get("model") or execution_output.get("model") or ""
                    correction_packet = build_correction_packet(
                        validation_record={
                            "id": validation_record_id,
                            "workflow_id": run.workflow_id,
                            "run_id": run_id,
                            "step_id": step_id,
                            "ticket_id": getattr(run, "ticket_id", None),
                            "validation_type": validation_snapshot.get("validation_type"),
                            "expected": validation_snapshot.get("expected"),
                            "observed": validation_snapshot.get("observed"),
                            "verdict": validation_snapshot.get("verdict"),
                            "correction_hint": validation_snapshot.get("correction_hint"),
                            "payload": {"snapshot": validation_snapshot},
                        },
                        ticket_title=getattr(ticket, "title", None) or run_data.get("ticket_title") or "",
                        step_name=step.name or f"Step {step_id}",
                        runtime_snapshot=runtime_snapshot,
                        executor_output=executor_output,
                    )
                    correction_attempt_id = create_correction_attempt(
                        validation_record_id=validation_record_id,
                        target_backend=target_backend,
                        target_model=target_model,
                        correction_packet=correction_packet,
                        status="queued",
                    )
                    if correction_attempt_id:
                        validation_snapshot["correction_attempt_id"] = correction_attempt_id
            except Exception:
                logger.debug("Could not record orchestrator validation record", exc_info=True)

            if getattr(run, "ticket_id", None):
                append_ticket_audit_entry(
                    db,
                    ticket_id=int(run.ticket_id),
                    run_id=run_id,
                    step_id=step_id,
                    step_result_id=getattr(step_result_row, "id", None),
                    execution_lane="workflow",
                    status=status,
                    final_verdict=((packet.get("audit") or {}).get("final_verdict")),
                    summary=f"{step.name or f'Step {step_id}'}: {status}",
                    details=(result or "")[:3000],
                )
            step_visibility = self._step_visibility_payload(
                step,
                extra_context=[
                    "ticket_workflow_brief",
                    "prior_step_result",
                    "result_packet_summary",
                    "route_decision",
                    "validation_output",
                ],
            )
            try:
                from distr.core.orchestrator import emit_event

                emit_event(
                    source="workflow",
                    event_type="workflow_step_completed",
                    status=status,
                    workflow_id=run.workflow_id,
                    run_id=run_id,
                    step_id=step_id,
                    ticket_id=getattr(run, "ticket_id", None),
                    board_id=getattr(run, "board_id", None),
                    summary=f"{step.name or f'Step {step_id}'}: {status}",
                    payload={
                        "step_name": step.name or f"Step {step_id}",
                        "action_type": step.action_type,
                        "validation_type": step.validation_type,
                        "decision": decision,
                        "validation_record_id": validation_record_id,
                        "correction_attempt_id": correction_attempt_id,
                        **step_visibility,
                    },
                    evidence={
                        "result_preview": (result or "")[:3000],
                        "validation": validation_snapshot,
                    },
                )
            except Exception:
                logger.debug("Could not emit orchestrator workflow_step_completed event", exc_info=True)

            try:
                from distr.core.workspace_memory.lifecycle import handoff_workflow_step

                handoff_workflow_step(
                    run_id=run_id,
                    ticket_id=getattr(run, "ticket_id", None),
                    project_id=(run_data or {}).get("project_id") if isinstance(run_data, dict) else None,
                    step_name=step.name or f"Step {step_id}",
                    summary=(result or "")[:2000],
                    status=status,
                )
            except Exception:
                logger.debug("workflow_step handoff failed", exc_info=True)

            if getattr(run, "ticket_id", None):
                try:
                    from distr.core.kanban.ticket_workflow_engagement import (
                        notify_ticket_workflow_step_finished,
                    )

                    notify_ticket_workflow_step_finished(
                        run_id=run_id,
                        step_id=step_id,
                        passed=bool(verified_passed),
                        result_text=result or "",
                        requires_attention=bool(
                            not verified_passed
                            and decision.get("action") in {"end_run", "waiting"}
                        ),
                    )
                except Exception:
                    logger.debug("Could not send ticket workflow step engagement", exc_info=True)

            correction_decision = self._maybe_auto_dispatch_correction(
                db,
                run=run,
                step=step,
                run_id=run_id,
                verified_passed=verified_passed,
                correction_attempt_id=correction_attempt_id,
                correction_packet=correction_packet,
                run_data=run_data,
            )
            if correction_decision:
                run.run_data = json.dumps(run_data)
                db.commit()
                increment_workflow_updated()
                return correction_decision

            run.run_data = json.dumps(run_data)
            db.commit()

        increment_workflow_updated()
        return decision

    def resume_from_feedback(
        self,
        step_id: int,
        run_id: int,
        feedback: str,
    ) -> Dict[str, Any]:
        """Resume a waiting step with user/main-agent feedback."""
        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.id == run_id,
            ).first()
            if not run:
                return {"action": "end_run", "status": "failed", "error": "Run not found"}
            if run.status != "waiting":
                return {"action": "end_run", "status": "failed",
                        "error": f"Run is not waiting (status: {run.status})"}

            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == step_id,
            ).first()
            if not step or step.status != "waiting":
                return {"action": "end_run", "status": "failed",
                        "error": "No waiting step found"}

            # Recover stored result/passed from run_data
            run_data = json.loads(run.run_data or "{}")
            stored_result = run_data.get("waiting_result", "")
            stored_passed = run_data.get("waiting_passed", True)

            # Append feedback to the result
            if feedback.strip():
                stored_result = f"{stored_result}\n\n[FEEDBACK]: {feedback.strip()}"
                # Persist feedback in run_data for downstream steps
                run_data["feedback"] = feedback.strip()
                run.run_data = json.dumps(run_data)

            # Transition back to running
            run.status = "running"
            step.status = "running"
            try:
                from distr.core.orchestrator import emit_event

                emit_event(
                    source="workflow",
                    event_type="workflow_step_feedback_received",
                    status="running",
                    workflow_id=run.workflow_id,
                    run_id=run_id,
                    step_id=step_id,
                    ticket_id=getattr(run, "ticket_id", None),
                    board_id=getattr(run, "board_id", None),
                    summary=f"{step.name or f'Step {step_id}'} received feedback.",
                    payload={"feedback": feedback.strip()},
                )
            except Exception:
                logger.debug("Could not emit orchestrator feedback event", exc_info=True)
            if getattr(run, "ticket_id", None):
                append_ticket_audit_entry(
                    db,
                    ticket_id=int(run.ticket_id),
                    run_id=run_id,
                    step_id=step_id,
                    step_result_id=None,
                    execution_lane="workflow",
                    status="running",
                    final_verdict="cannot_determine",
                    summary=f"{step.name or f'Step {step_id}'} resumed",
                    details=(feedback or "").strip()[:3000] or "Run resumed after waiting state.",
                )
                increment_kanban_updated(
                    board_id=getattr(run, "board_id", None),
                    event_type="ticket_workflow_status",
                    payload={
                        "ticket_id": int(run.ticket_id),
                        "run_id": run_id,
                        "status": "running",
                        "step_id": step_id,
                    },
                )
            db.commit()

        increment_workflow_updated()

        # Re-route with the enriched result, skipping the wait gate
        # since the step has already waited for and received feedback.
        return self.route(
            step_id,
            stored_result,
            stored_passed,
            run_id,
            skip_wait=True,
            skip_approval=True,
        )

    def _maybe_auto_dispatch_correction(
        self,
        db,
        *,
        run: AutoWorkflowRun,
        step: AutoWorkflowStep,
        run_id: int,
        verified_passed: bool,
        correction_attempt_id: int | None,
        correction_packet: dict[str, Any],
        run_data: dict[str, Any],
    ) -> Dict[str, Any] | None:
        """Auto-dispatch corrections were removed from workflow run policy."""
        return None

    # ── Internal: determine next step ───────────────────────────────

    def _determine_next(
        self,
        db,
        step: AutoWorkflowStep,
        run: AutoWorkflowRun,
        verified_passed: bool,
        result: str,
        validation_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Pick the next step based on routing_mode. Mutates run in-place."""
        retry_decision = self._bounded_validation_retry(
            step,
            run,
            verified_passed=verified_passed,
            validation_snapshot=validation_snapshot or {},
            result=result,
        )
        if retry_decision:
            return retry_decision
        routing_mode = (step.routing_mode or "static").strip().lower()

        if routing_mode == "agent_decision":
            next_step_id = self._agent_route(db, step, result, verified_passed)
        else:
            next_step_id = self._static_route(db, step, verified_passed)

        next_step_id = self._apply_ticket_contract_routing(
            db,
            run,
            step,
            next_step_id,
            verified_passed=verified_passed,
            result=result,
        )

        next_step_id = self._apply_loop_iteration_routing(
            db, run, step, next_step_id, verified_passed,
        )

        # null / -1 → end run
        if next_step_id is None or next_step_id == -1:
            return self._end_run(run, status="completed" if verified_passed else "failed")

        next_step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == next_step_id,
        ).first()
        if not next_step:
            return self._end_run(run, status="completed" if verified_passed else "failed")

        # Self-routing guard
        bounded_self_retry = False
        if next_step.id == step.id and not verified_passed:
            try:
                retry_data = json.loads(run.run_data or "{}") or {}
            except Exception:
                retry_data = {}
            retry_contract = retry_data.get("loop_contract") or {}
            bounded_self_retry = bool(
                retry_contract.get("max_iterations")
                and int(retry_data.get("loop_iteration") or 0) > 0
            )
        if next_step.id == step.id and not bounded_self_retry:
            logger.warning(
                "Step %d routes to itself — ending run to prevent infinite loop.",
                step.id,
            )
            return self._end_run(run, status="failed", warning="Infinite loop prevented")

        # Advance
        run.current_step_id = next_step.id
        return {
            "action": "next_step",
            "step_id": next_step.id,
            "wait_before_next": step.wait_before_next or 0,
        }

    @staticmethod
    def _bounded_validation_retry(
        step: AutoWorkflowStep,
        run: AutoWorkflowRun,
        *,
        verified_passed: bool,
        validation_snapshot: Dict[str, Any],
        result: str,
    ) -> Optional[Dict[str, Any]]:
        """Retry a correctable step with its compact validator finding.

        This is deliberately bounded by the step's explicit ``max_retries``.
        It is not a general autonomous correction loop and cannot silently
        turn one rejected result into unlimited provider spend.
        """
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        retry_counts = dict(run_data.get("step_retry_counts") or {})
        key = str(step.id)
        if verified_passed:
            if key in retry_counts:
                retry_counts.pop(key, None)
                run_data["step_retry_counts"] = retry_counts
                correction = run_data.get("last_validation_correction")
                if (
                    isinstance(correction, dict)
                    and int(correction.get("step_id") or 0) == int(step.id)
                ):
                    run_data.pop("last_validation_correction", None)
                run.run_data = json.dumps(run_data)
            return None
        max_retries = max(0, int(getattr(step, "max_retries", 0) or 0))
        used = max(0, int(retry_counts.get(key) or 0))
        correction_hint = str(validation_snapshot.get("correction_hint") or "").strip()
        if not correction_hint or used >= max_retries:
            return None
        retry_counts[key] = used + 1
        run_data["step_retry_counts"] = retry_counts
        run_data["feedback"] = (
            "The previous result failed deterministic validation. Preserve every valid field from "
            "the prior result, correct only this finding, and return a complete replacement report; "
            "do not repeat the repository scan: "
            + correction_hint
            + "\n\nPrior rejected result (reuse its valid fields):\n"
            + str(result or "").strip()[:6000]
        )
        run_data["last_validation_correction"] = {
            "step_id": int(step.id),
            "attempt": used + 1,
            "max_retries": max_retries,
            "correction_hint": correction_hint,
            "prior_result": str(result or "").strip()[:12000],
        }
        current_route = run_data.get("execution_route")
        if (
            isinstance(current_route, dict)
            and str(current_route.get("source") or "") == "orchestrator_override"
        ):
            run_data["approved_route_override"] = dict(current_route)
        run.run_data = json.dumps(run_data)
        run.current_step_id = int(step.id)
        return {
            "action": "next_step",
            "step_id": int(step.id),
            "wait_before_next": 0,
            "validation_retry": True,
            "retry_attempt": used + 1,
            "max_retries": max_retries,
        }

    @staticmethod
    def _run_ticket_text(db, run: AutoWorkflowRun) -> str:
        parts: list[str] = []
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        for key in ("ticket_title", "ticket_workflow_brief"):
            if run_data.get(key):
                parts.append(str(run_data[key]))
        return "\n\n".join(part for part in parts if part.strip())

    @staticmethod
    def _report_step(db, workflow_id: int) -> AutoWorkflowStep | None:
        steps = (
            db.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowStep.position.desc())
            .all()
        )
        return next(
            (
                item for item in steps
                if any(word in str(item.name or "").lower() for word in ("report", "handoff", "compact memory"))
            ),
            steps[0] if steps else None,
        )

    def _apply_ticket_contract_routing(
        self,
        db,
        run: AutoWorkflowRun,
        step: AutoWorkflowStep,
        next_step_id: int | None,
        *,
        verified_passed: bool,
        result: str,
    ) -> int | None:
        """Skip inapplicable phases and stop repeating non-actionable failures."""
        from distr.core.workflow.ticket_contract import (
            classify_ticket_execution,
            existing_work_satisfies_contract,
        )

        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        if (
            not verified_passed
            and int(run_data.get("validation_stalled_step_id") or 0) == int(step.id)
            and str(run_data.get("waiting_kind") or "") == "control_interrupt"
        ):
            # Control interrupt already paused the run for a human decision.
            return next_step_id

        if (
            not verified_passed
            and int(run_data.get("validation_stalled_step_id") or 0) == int(step.id)
        ):
            run_data["forced_terminal_status"] = "failed"
            run_data["terminal_warning"] = (
                "Validation repeated without a new actionable finding; the run stopped instead of looping again."
            )
            run.run_data = json.dumps(run_data)
            # The canonical result packet and audit ledger already contain the
            # failed attempts. Do not spend another model call asking a report
            # worker to narrate a failure it cannot repair.
            return -1

        ticket_text = self._run_ticket_text(db, run)
        profile = classify_ticket_execution(ticket_text)
        run_data["ticket_execution_profile"] = profile
        if (
            profile.get("research_only")
            and int(step.position or 0) == 0
            and verified_passed
            and existing_work_satisfies_contract(ticket_text, result)
        ):
            report_step = self._report_step(db, int(run.workflow_id))
            run_data["already_satisfied_short_circuit"] = True
            run_data["short_circuit_reason"] = "Existing ticket artifacts satisfy the explicit acceptance contract."
            run.run_data = json.dumps(run_data)
            return report_step.id if report_step and report_step.id != step.id else next_step_id

        if profile.get("research_only") and next_step_id not in (None, -1):
            target = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(next_step_id)).first()
            if target and any(word in str(target.name or "").lower() for word in ("production polish", "ship audit")):
                report_step = self._report_step(db, int(run.workflow_id))
                run_data["skipped_inapplicable_steps"] = list(
                    dict.fromkeys([*(run_data.get("skipped_inapplicable_steps") or []), int(target.id)])
                )
                run.run_data = json.dumps(run_data)
                return report_step.id if report_step and report_step.id != step.id else next_step_id
        run.run_data = json.dumps(run_data)
        return next_step_id

    @staticmethod
    def _record_validation_progress(
        run: AutoWorkflowRun | None,
        step: AutoWorkflowStep,
        *,
        caller_passed: bool,
        verified_passed: bool,
        validation_snapshot: dict[str, Any],
    ) -> None:
        """Detect repeated validator disagreement before it burns every loop pass."""
        if not run:
            return
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        state = dict(run_data.get("validation_progress") or {})
        key = str(step.id)
        if verified_passed:
            state.pop(key, None)
            if int(run_data.get("validation_stalled_step_id") or 0) == int(step.id):
                run_data.pop("validation_stalled_step_id", None)
        elif caller_passed:
            expected = " ".join(str(validation_snapshot.get("expected") or "").lower().split())
            correction = " ".join(
                str(validation_snapshot.get("correction_hint") or "").lower().split()
            )
            finding_codes = ",".join(
                sorted(
                    str(item.get("code") or "")
                    for item in (validation_snapshot.get("ticket_acceptance_findings") or [])
                    if isinstance(item, dict) and item.get("code")
                )
            )
            signature_basis = "\n".join((expected, correction, finding_codes))
            signature = hashlib.sha256(signature_basis.encode("utf-8")).hexdigest()[:16]
            previous = dict(state.get(key) or {})
            count = int(previous.get("count") or 0) + 1 if previous.get("signature") == signature else 1
            state[key] = {"signature": signature, "count": count}
            if count >= 2:
                run_data["validation_stalled_step_id"] = int(step.id)
                run_data["validation_stall_count"] = count
                run_data["consecutive_step_failures"] = max(
                    int(run_data.get("consecutive_step_failures") or 0),
                    count,
                )
        else:
            state.pop(key, None)
        if verified_passed:
            run_data["consecutive_step_failures"] = 0
        elif not caller_passed:
            run_data["consecutive_step_failures"] = int(run_data.get("consecutive_step_failures") or 0) + 1
        run_data["validation_progress"] = state
        run.run_data = json.dumps(run_data)

    def _maybe_enter_control_interrupt(
        self,
        db,
        *,
        run: AutoWorkflowRun | None,
        step: AutoWorkflowStep,
        run_id: int,
        result: str,
        verified_passed: bool,
    ) -> Dict[str, Any] | None:
        """Pause for the operator when automatic retries would only waste another loop."""
        if not run or verified_passed:
            return None
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        repeated = max(
            int(run_data.get("consecutive_step_failures") or 0),
            int(run_data.get("validation_stall_count") or 0),
        )
        stalled = int(run_data.get("validation_stalled_step_id") or 0) == int(step.id)
        if repeated < 2 and not stalled:
            return None
        from distr.core.workflow.control_policy import decide_interruption

        decision = decide_interruption(repeated_failures=max(repeated, 2 if stalled else repeated))
        if not decision.should_interrupt:
            return None
        prompt = decision.question
        if decision.recommendation:
            prompt = (
                f"{decision.question} I recommend: {decision.recommendation} "
                "Reply with a choice, steer the run, or stop it."
            ).strip()
        run_data["waiting_kind"] = "control_interrupt"
        run_data["waiting_prompt"] = prompt
        run_data["waiting_result"] = result or ""
        run_data["waiting_passed"] = False
        run_data["interrupt_context"] = decision.to_dict()
        run_data["human_intervention_state"] = "needs_human_input"
        run_data["next_action"] = "needs_human_input"
        run.run_data = json.dumps(run_data)
        # Persist the wait state before any notification code opens its own
        # transaction.  SQLite cannot service a second writer while this
        # session still owns the write lock, which previously dropped the
        # Telegram interaction and left only a vague spoken message.
        wait_result = self._enter_wait_state(db, step, run_id, prompt, False)
        try:
            from distr.core.orchestration_events import emit_orchestration_event

            emit_orchestration_event(
                source="workflow",
                event_type="needs_input",
                status="waiting",
                workflow_id=run.workflow_id,
                run_id=run_id,
                step_id=int(step.id),
                ticket_id=getattr(run, "ticket_id", None),
                board_id=getattr(run, "board_id", None),
                summary=decision.question,
                payload={"interruption": decision.to_dict()},
            )
        except Exception:
            logger.debug("Could not emit control interrupt event", exc_info=True)
        return wait_result

    # ── Static routing ──────────────────────────────────────────────

    def _apply_loop_iteration_routing(
        self,
        db,
        run: Optional[AutoWorkflowRun],
        step: AutoWorkflowStep,
        next_step_id: Optional[int],
        verified_passed: bool,
    ) -> Optional[int]:
        """Track loop iterations when routing back to an earlier step."""
        if not run or next_step_id is None:
            return next_step_id
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            return next_step_id
        loop_contract = run_data.get("loop_contract") or {}
        max_iterations = loop_contract.get("max_iterations")
        if max_iterations is None:
            return next_step_id
        target = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == next_step_id).first()
        # Any backward route is another loop iteration. This includes a
        # successful correction returning to independent review; counting only
        # failed steps allows review -> correction -> review to recurse forever.
        # Only forward routes are outside the loop counter.
        if not target or target.position > step.position:
            return next_step_id
        iteration = int(run_data.get("loop_iteration") or 0) + 1
        run_data["loop_iteration"] = iteration
        run.run_data = json.dumps(run_data)
        db.flush()
        if iteration >= int(max_iterations):
            logger.info(
                "Loop max iterations (%s) reached at step %s — ending run",
                max_iterations,
                step.id,
            )
            report_step = (
                db.query(AutoWorkflowStep)
                .filter(AutoWorkflowStep.workflow_id == step.workflow_id)
                .order_by(AutoWorkflowStep.position.desc())
                .first()
            )
            if report_step and report_step.id != step.id:
                return report_step.id
            return -1
        target_visibility = self._step_visibility_payload(
            target,
            extra_context=[
                "ticket_workflow_brief",
                "prior_step_result",
                "result_packet_summary",
                "route_decision",
                "validation_output",
            ],
        )
        payload = {
            "iteration": iteration,
            "max_iterations": max_iterations,
            "from_step_id": step.id,
            "from_step_name": step.name or f"Step {step.id}",
            "to_step_id": target.id,
            "to_step_name": target.name or f"Step {target.id}",
            **target_visibility,
        }
        loop_event = OrchestratorEvent(
            event_uid=uuid.uuid4().hex,
            source="workflow",
            event_type="loop_iteration",
            status="running",
            workflow_id=step.workflow_id,
            run_id=run.id,
            step_id=step.id,
            ticket_id=getattr(run, "ticket_id", None),
            board_id=getattr(run, "board_id", None),
            project_id=run_data.get("project_id"),
            summary=f"Loop iteration {iteration} of {max_iterations}",
            payload=json.dumps(payload, default=str),
            evidence=json.dumps({
                "prior_step_result": (getattr(step, "result", None) or "")[:3000],
                "next_step_context": target_visibility.get("context", []),
            }, default=str),
        )
        db.add(loop_event)
        db.flush()
        try:
            from distr.core.events import ORCHESTRATION_EVENT, get_event_bus
            from distr.core.orchestrator import serialize_event

            get_event_bus().publish(ORCHESTRATION_EVENT, serialize_event(loop_event))
        except Exception:
            logger.debug("Could not publish loop_iteration event to EventBus", exc_info=True)
        return next_step_id

    @staticmethod
    def _step_config_dict(step: AutoWorkflowStep) -> Dict[str, Any]:
        try:
            config = json.loads(step.config or "{}")
        except Exception:
            config = {}
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _step_tools_for_action(action_type: str) -> List[str]:
        return tools_for_action(action_type)

    @classmethod
    def _step_visibility_payload(
        cls,
        step: AutoWorkflowStep,
        *,
        extra_context: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        config = cls._step_config_dict(step)
        tools = config.get("tools") if isinstance(config.get("tools"), list) else []
        skills = config.get("skills") if isinstance(config.get("skills"), list) else []
        context = config.get("context") if isinstance(config.get("context"), list) else []
        clean_tools = normalize_tool_list(tools)
        clean_skills = [str(item).strip() for item in skills if str(item or "").strip()]
        clean_context = [str(item).strip() for item in context if str(item or "").strip()]
        for label in extra_context or []:
            label = str(label or "").strip()
            if label and label not in clean_context:
                clean_context.append(label)
        return {
            "skills": clean_skills,
            "tools": clean_tools or cls._step_tools_for_action(step.action_type or step.step_type or ""),
            "context": clean_context,
        }

    @staticmethod
    def _static_route(db, step: AutoWorkflowStep, passed: bool) -> Optional[int]:
        """Follow explicit goto, otherwise advance on pass or end on fail."""
        goto = step.on_pass_goto if passed else step.on_fail_goto
        if goto is not None:
            return goto

        if not passed:
            return -1

        next_step = (
            db.query(AutoWorkflowStep)
            .filter(
                AutoWorkflowStep.workflow_id == step.workflow_id,
                AutoWorkflowStep.position > step.position,
            )
            .order_by(AutoWorkflowStep.position.asc())
            .first()
        )
        if next_step:
            return next_step.id
        return None

    # ── Agent routing ───────────────────────────────────────────────

    def _agent_route(
        self,
        db,
        step: AutoWorkflowStep,
        result: str,
        passed: bool,
    ) -> Optional[int]:
        """Ask the LLM which step to go to next."""
        wf = db.query(AutoWorkflow).filter(
            AutoWorkflow.id == step.workflow_id,
        ).first()
        all_steps = sorted(wf.steps, key=lambda s: s.position) if wf else []
        return self._agent_route_decision(step, result, passed, all_steps)

    @staticmethod
    def _agent_route_decision(
        step: AutoWorkflowStep,
        result: str,
        passed: bool,
        all_steps: List[AutoWorkflowStep],
    ) -> Optional[int]:
        """Build an LLM prompt and parse the response into a step ID or None."""
        step_descriptions = []
        for s in all_steps:
            if s.id == step.id:
                continue
            desc = f'  - Step ID {s.id}: "{s.name}" (position #{s.position})'
            if s.description:
                desc += f" — {s.description}"
            step_descriptions.append(desc)

        if not step_descriptions:
            return None

        steps_list = "\n".join(step_descriptions)
        routing_prompt = (step.routing_prompt or "").strip()
        status_word = "PASSED" if passed else "FAILED"

        prompt = (
            "You are a workflow routing agent. A step just completed and you "
            "need to decide what happens next.\n\n"
            f'COMPLETED STEP: "{step.name}" (ID {step.id})\n'
            f"STATUS: {status_word}\n"
            f"RESULT:\n{result}\n\n"
        )
        if routing_prompt:
            prompt += f"ROUTING INSTRUCTIONS:\n{routing_prompt}\n\n"
        prompt += (
            f"AVAILABLE NEXT STEPS:\n{steps_list}\n\n"
            'Respond with ONLY one of the following:\n'
            '- A step ID number (e.g. "42") to go to that step\n'
            '- "END" to finish the workflow\n\n'
            "Your decision:"
        )

        try:
            from distr.core.agent.services.llm.shared import get_shared_llm_response
            response = get_shared_llm_response(prompt)
            if response:
                return StepRouter._parse_routing_response(response, all_steps, step.id)
        except ImportError:
            pass
        except Exception as e:
            logger.error("Agent routing decision failed: %s", e, exc_info=True)

        logger.warning("Agent routing: no LLM available, defaulting to END")
        return None

    @staticmethod
    def _parse_routing_response(
        response: str,
        all_steps: List[AutoWorkflowStep],
        current_step_id: int,
    ) -> Optional[int]:
        """Parse the LLM routing response into a step ID or None (end)."""
        text = response.strip().upper()
        if text == "END" or text.startswith("END"):
            return None

        numbers = re.findall(r"\d+", text)
        if numbers:
            candidate_id = int(numbers[0])
            valid_ids = {s.id for s in all_steps if s.id != current_step_id}
            if candidate_id in valid_ids:
                return candidate_id
            # Try matching by position
            for s in all_steps:
                if s.position == candidate_id and s.id != current_step_id:
                    return s.id

        logger.warning(
            "Could not parse agent routing response: '%s', defaulting to END",
            response,
        )
        return None

    # ── Wait state ──────────────────────────────────────────────────

    def _enter_wait_state(
        self,
        db,
        step: AutoWorkflowStep,
        run_id: int,
        result: str,
        passed: bool,
    ) -> Dict[str, Any]:
        """Put step + run into waiting state and notify the main agent."""
        step.status = "waiting"
        step_id = step.id
        step_name = step.name
        workflow_id = step.workflow_id
        handoff = self._build_wait_handoff_text(step_name=step_name, result_text=result, run_id=run_id)

        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.id == run_id,
        ).first()
        if run:
            run.status = "waiting"
            run_data = json.loads(run.run_data or "{}")
            run_data["waiting_result"] = result
            run_data["waiting_passed"] = passed
            if not run_data.get("waiting_kind"):
                run_data["waiting_kind"] = ""
            run_data["waiting_prompt"] = _effective_wait_prompt(
                run_data,
                default_prompt=handoff["prompt"],
                result=result,
            )
            waiting_kind = str(run_data.get("waiting_kind") or "").strip().lower()
            if waiting_kind in {
                "control_interrupt",
                "route_approval",
                "approval",
                "provider_preflight",
                "ide_handoff",
            }:
                try:
                    from distr.core.workflow.blueprint_adherence import update_drift_metrics

                    run_data = update_drift_metrics(run_data, human_takeover=True)
                except Exception:
                    logger.debug("Could not update drift metrics on wait", exc_info=True)
            run.run_data = json.dumps(run_data)
        db.add(AutoWorkflowStepResult(
            step_id=step_id,
            run_id=run_id,
            agent_response=handoff["history_entry"],
            status="waiting",
        ))
        if run and getattr(run, "ticket_id", None):
            try:
                from distr.core.db.kanban import KanbanTicket

                waiting_ticket = db.query(KanbanTicket).filter(
                    KanbanTicket.id == int(run.ticket_id)
                ).first()
                if waiting_ticket:
                    waiting_ticket.workflow_status = "waiting"
            except Exception:
                logger.debug("Could not align ticket waiting state", exc_info=True)
            append_ticket_audit_entry(
                db,
                ticket_id=int(run.ticket_id),
                run_id=run_id,
                step_id=step_id,
                step_result_id=None,
                execution_lane="workflow",
                status="waiting",
                final_verdict="cannot_determine",
                summary=f"{step_name or f'Step {step_id}'} waiting for input",
                details=(result or "")[:3000],
            )
            increment_kanban_updated(
                board_id=getattr(run, "board_id", None),
                event_type="ticket_workflow_status",
                payload={
                    "ticket_id": int(run.ticket_id),
                    "run_id": run_id,
                    "status": "waiting",
                    "step_id": step_id,
                },
            )
        db.commit()

        increment_workflow_updated()
        record_workflow_chat_event(
            run_id,
            "waiting",
            status="waiting",
            step_id=step_id,
            step_name=step_name,
            summary=result or "Workflow is waiting for input.",
        )
        waiting_kind = ""
        if run:
            try:
                waiting_kind = str((json.loads(run.run_data or "{}") or {}).get("waiting_kind") or "")
            except Exception:
                waiting_kind = ""
        # Interactive decisions use the exact stored question below. Emitting
        # the generic wait signal first caused TTS to say only "needs your
        # input" and hid the actual question behind a second message.
        interactive_kinds = {
            "control_interrupt",
            "route_approval",
            "approval",
            "provider_preflight",
            "pre_execution_approval",
            "run_briefing",
            "step_review",
            "worker_needs_input",
            "restart_recovery",
        }
        if waiting_kind not in interactive_kinds:
            self._emit_waiting_for_feedback(step_id, workflow_id, run_id, result)
            self._notify_main_agent(workflow_id, run_id, handoff, result_text=result)
        if run:
            try:
                latest_data = json.loads(run.run_data or "{}") or {}
                latest_kind = str(latest_data.get("waiting_kind") or "").strip().lower()
                if latest_kind in interactive_kinds:
                    from distr.core.kanban.ticket_workflow_engagement import notify_ticket_workflow_progress

                    question = str(
                        latest_data.get("provider_preflight_prompt")
                        or latest_data.get("waiting_prompt")
                        or result
                        or ""
                    ).strip()
                    notify_ticket_workflow_progress(
                        run_id=run_id,
                        step_id=step_id,
                        body=question,
                        voice_body=question,
                        state_fingerprint=f"workflow-decision:{latest_kind}:{run_id}:{step_id}",
                        priority="high",
                        requires_response=True,
                    )
            except Exception:
                logger.warning("Could not send workflow decision to Telegram", exc_info=True)

        return {"action": "waiting", "notify_main_agent": True, "run_id": run_id}

    def _enter_approval_state(
        self,
        db,
        step: AutoWorkflowStep,
        run_id: int,
        result: str,
        passed: bool,
    ) -> Dict[str, Any]:
        """Hold a verified step until a human approves it."""
        step.status = "waiting"
        step_id = step.id
        step_name = step.name
        workflow_id = step.workflow_id
        handoff = self._build_approval_handoff_text(step_name=step_name, result_text=result, run_id=run_id)

        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.id == run_id,
        ).first()
        if run:
            run.status = "waiting"
            run_data = json.loads(run.run_data or "{}")
            run_data["waiting_result"] = result
            run_data["waiting_passed"] = passed
            run_data["waiting_prompt"] = handoff["prompt"]
            run_data["waiting_kind"] = "approval"
            run.run_data = json.dumps(run_data)
        db.add(AutoWorkflowStepResult(
            step_id=step_id,
            run_id=run_id,
            agent_response=handoff["history_entry"],
            status="waiting",
        ))
        if run and getattr(run, "ticket_id", None):
            append_ticket_audit_entry(
                db,
                ticket_id=int(run.ticket_id),
                run_id=run_id,
                step_id=step_id,
                step_result_id=None,
                execution_lane="workflow",
                status="waiting",
                final_verdict="cannot_determine",
                summary=f"{step_name or f'Step {step_id}'} waiting for approval",
                details=(result or "")[:3000],
            )
            increment_kanban_updated(
                board_id=getattr(run, "board_id", None),
                event_type="ticket_workflow_status",
                payload={
                    "ticket_id": int(run.ticket_id),
                    "run_id": run_id,
                    "status": "waiting",
                    "step_id": step_id,
                    "waiting_kind": "approval",
                },
            )
        db.commit()

        increment_workflow_updated()
        record_workflow_chat_event(
            run_id,
            "waiting",
            status="waiting",
            step_id=step_id,
            step_name=step_name,
            summary=result or "Workflow step is waiting for approval.",
        )
        try:
            from distr.core.orchestrator import emit_approval_event

            emit_approval_event(
                event_type="approval_requested",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=getattr(run, "ticket_id", None) if run else None,
                board_id=getattr(run, "board_id", None) if run else None,
                summary=f"{step_name or f'Step {step_id}'} requires manual approval.",
                payload={"step_name": step_name or "", "result_preview": (result or "")[:1500]},
            )
        except Exception:
            logger.debug("Could not emit approval_requested event", exc_info=True)
        self._emit_waiting_for_feedback(step_id, workflow_id, run_id, result)
        self._notify_main_agent(workflow_id, run_id, handoff, result_text=result)

        return {"action": "waiting", "notify_main_agent": True, "run_id": run_id, "waiting_kind": "approval"}

    @staticmethod
    def _build_approval_handoff_text(step_name: str, result_text: str, run_id: Optional[int]) -> Dict[str, str]:
        from distr.core.workflow.approval_decision import (
            build_step_approval_decision,
            format_approval_decision_text,
            format_approval_decision_voice,
        )

        clean_result = (result_text or "").strip() or "Step completed with no detailed output."
        decision = build_step_approval_decision(step_name=step_name, result_summary=clean_result)
        prompt = format_approval_decision_text(decision)
        tts = format_approval_decision_voice(decision)
        summary = clean_result[:280]
        if len(clean_result) > 280:
            summary += "..."
        step_label = step_name or "workflow step"
        report = (
            f"[WORKFLOW_APPROVAL_REQUIRED]\n"
            f"step_name: {step_label}\n"
            f"run_id: __RUN_ID__\n"
            f"status: waiting_for_approval\n"
            f"step_result_summary: {summary}\n"
            f"step_result_full: {clean_result[:1500]}\n\n"
            "Orchestrator instructions:\n"
            "1) Present the approval decision card — one decision, plain English.\n"
            "2) Accept yes/no/steer, not 'approve step N'.\n"
            "3) After approval, call continue_workflow with the user's words."
        )
        history_entry = f"{clean_result}\n\n[APPROVAL REQUIRED]\n{prompt}"
        if run_id is not None:
            history_entry = f"{history_entry}\nRun ID: {run_id}"
        return {
            "prompt": prompt,
            "tts": tts,
            "report": report,
            "history_entry": history_entry,
        }

    # ── Event emission ─────────────────────────────────────────────────

    @staticmethod
    def _emit_waiting_for_feedback(
        step_id: int,
        workflow_id: int,
        run_id: int,
        result: str,
    ) -> None:
        """Emit ``step_waiting_for_feedback`` signal so the main agent can react."""
        try:
            from distr.core.signals import signal_manager
            signal_manager.step_waiting_for_feedback.emit(
                step_id, workflow_id, run_id, result,
            )
        except Exception as e:
            logger.debug("Could not emit step_waiting_for_feedback: %s", e)

    # ── End run helper ──────────────────────────────────────────────

    @staticmethod
    def _end_run(
        run: AutoWorkflowRun,
        status: str = "completed",
        warning: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark run as completed and return end_run decision."""
        run.status = status
        run.completed_at = utc_now_naive()
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        try:
            from distr.core.workflow.blueprint_adherence import update_drift_metrics

            run_data = update_drift_metrics(
                run_data,
                completed=(str(status or "").lower() == "completed"),
            )
        except Exception:
            logger.debug("Could not update drift metrics on end_run", exc_info=True)
        if warning:
            run_data["terminal_warning"] = warning
        run.run_data = json.dumps(run_data)
        decision: Dict[str, Any] = {
            "action": "end_run",
            "status": status,
            "run_id": run.id,
        }
        if warning:
            decision["warning"] = warning
        return decision

    # ── Notifications ───────────────────────────────────────────────

    @staticmethod
    def _notify_main_agent(
        workflow_id: int,
        run_id: Optional[int],
        handoff: Dict[str, str],
        *,
        result_text: str = "",
    ) -> None:
        """Speak result via TTS and queue a report for the main agent."""
        from distr.core.workflow.wait_handoff import is_ide_handoff_result

        # IDE handoffs also notify via ticket_workflow_engagement — avoid double speech.
        if not is_ide_handoff_result(result_text):
            try:
                from distr.core.signals import speak_text_directly_event_queue
                from distr.core.kanban.ticket_workflow_engagement import prepare_workflow_voice_text

                speak_text_directly_event_queue(prepare_workflow_voice_text(handoff["tts"]))
            except Exception as e:
                logger.debug("Could not speak wait notification: %s", e)

        # Queue report for the main agent
        try:
            from distr.core.workflow_engine.agent_bridge import WorkflowAgentBridge
            WorkflowAgentBridge().queue_report_to_agent(
                workflow_id,
                handoff["report"].replace("__RUN_ID__", str(run_id)),
            )
        except Exception as e:
            logger.debug("Could not queue wait report: %s", e)

    @staticmethod
    def _build_wait_handoff_text(step_name: str, result_text: str, run_id: Optional[int]) -> Dict[str, str]:
        from distr.core.workflow.wait_handoff import build_wait_handoff_text

        return build_wait_handoff_text(step_name, result_text, run_id)
