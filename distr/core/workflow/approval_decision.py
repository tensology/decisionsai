"""One decision at a time — plain-English approval cards for workflow gates."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_CHECKPOINT_LOOP_THRESHOLD = 3


@dataclass(frozen=True)
class ApprovalDecision:
    """Single human decision with context — not 'approve step 7'."""

    title: str
    about_to_do: str
    why_it_matters: str
    what_could_go_wrong: str
    fallback: str
    recommendation: str
    reply_hints: str = "Say **yes, go ahead** to take the recommended path, **no, stop** to cancel, or steer in plain English."

    def to_dict(self) -> dict[str, str]:
        return {k: str(v or "").strip() for k, v in asdict(self).items()}


def format_approval_decision_text(decision: ApprovalDecision) -> str:
    """Full card for chat, Telegram, and Workflows UI."""
    d = decision
    return (
        f"**{d.title}**\n\n"
        f"**About to do:** {d.about_to_do}\n"
        f"**Why it matters:** {d.why_it_matters}\n"
        f"**What could go wrong:** {d.what_could_go_wrong}\n"
        f"**If we stop:** {d.fallback}\n"
        f"**Recommendation:** {d.recommendation}\n\n"
        f"{d.reply_hints}"
    )


def format_approval_decision_voice(decision: ApprovalDecision) -> str:
    """Short spoken summary — actionable, no step numbers."""
    title = re.sub(r"\*+", "", decision.title).strip()
    rec = re.sub(r"\*+", "", decision.recommendation).strip()
    return (
        f"{title}. {rec} "
        "Say yes go ahead to proceed, no stop to cancel, or tell me what to change."
    )


def build_run_briefing_decision(ctx: Any) -> ApprovalDecision:
    """Pre-run: one decision to start the ticket workflow."""
    ticket = getattr(ctx, "ticket_title", "") or "this ticket"
    project = getattr(ctx, "project_name", "") or "the linked project"
    first = getattr(ctx, "first_step_name", "") or "the first step"
    goal = getattr(ctx, "loop_goal", "") or "finish the ticket with evidence"
    return ApprovalDecision(
        title=f"Start work on {ticket}?",
        about_to_do=(
            f"Run the workflow on {project}, beginning with {first}. "
            f"The loop goal is: {goal}."
        ),
        why_it_matters=(
            f"This ticket ({ticket}) is queued and ready; starting now keeps the loop moving "
            "instead of leaving work half-scoped."
        ),
        what_could_go_wrong=(
            "The harness may pick up stale context, drift from acceptance criteria, "
            "or burn API quota on the wrong slice if the plan is off."
        ),
        fallback=(
            "We stay paused. You can steer the plan, link a different project, "
            "or cancel the run from Workflows → Active Runs."
        ),
        recommendation="Yes — start with the first step using the current ticket brief and project route.",
    )


def build_step_review_decision(
    *,
    ticket_title: str,
    step_name: str,
    step_index: int | None,
    passed: bool,
    result_summary: str,
    next_step_name: str = "",
) -> ApprovalDecision:
    """Between steps: one decision to continue or steer."""
    label = step_name.strip() or "the last step"
    if step_index and step_index > 0:
        label = f"{step_name.strip() or 'step'} (step {step_index})"
    status = "completed successfully" if passed else "did not pass cleanly"
    summary = (result_summary or "").strip() or "No detailed output was captured."
    if len(summary) > 220:
        summary = summary[:217].rstrip() + "..."
    next_bit = (
        f"Next I would run: {next_step_name}."
        if next_step_name
        else "Next I would continue to the following workflow step."
    )
    if passed:
        rec = f"Yes — continue. {next_bit}"
        risk = "The next step might surface a regression the last check missed."
    else:
        rec = "Stop and steer unless you want me to retry with your correction."
        risk = "Continuing without a fix may repeat the same failure or mark the run done too early."
    return ApprovalDecision(
        title=f"Continue after {label}?",
        about_to_do=next_bit,
        why_it_matters=(
            f"{label} {status}. Summary: {summary}"
        ),
        what_could_go_wrong=risk,
        fallback=(
            "Say no or stop to cancel the run, or tell me what to change before we continue."
        ),
        recommendation=rec,
    )


def build_step_approval_decision(
    *,
    step_name: str,
    result_summary: str,
) -> ApprovalDecision:
    """Step passed validation but requires explicit human sign-off."""
    label = step_name.strip() or "this step"
    summary = (result_summary or "").strip() or "Step completed with no detailed output."
    if len(summary) > 220:
        summary = summary[:217].rstrip() + "..."
    return ApprovalDecision(
        title=f"Sign off on {label}?",
        about_to_do=f"Mark {label} as approved and continue the workflow.",
        why_it_matters=f"Validation passed. Outcome: {summary}",
        what_could_go_wrong=(
            "Approving now commits the run to downstream steps even if the evidence "
            "was thin or the UI journey was not fully exercised."
        ),
        fallback="Reject or steer — I will hold the run until you say what to fix.",
        recommendation="Approve only if the outcome matches what you expected on the ticket.",
        reply_hints="Say **yes, approve** to continue, **no, stop** to cancel, or describe what to fix.",
    )


def increment_checkpoint_counter(run_data: dict[str, Any], *, gate: str) -> dict[str, Any]:
    """Track how often we pause on the same gate without finishing the run."""
    updated = dict(run_data or {})
    counts = dict(updated.get("checkpoint_counts") or {})
    key = (gate or "unknown").strip().lower()
    counts[key] = int(counts.get(key) or 0) + 1
    updated["checkpoint_counts"] = counts
    updated["last_checkpoint_gate"] = key
    return updated


def approval_loop_diagnostics(
    run_data: dict[str, Any] | None,
    *,
    waiting_kind: str = "",
    threshold: int = _CHECKPOINT_LOOP_THRESHOLD,
) -> str | None:
    """When gates loop without progress, switch from permission spam to diagnostics."""
    data = run_data if isinstance(run_data, dict) else {}
    kind = (waiting_kind or data.get("waiting_kind") or data.get("last_checkpoint_gate") or "").strip().lower()
    if not kind:
        return None
    count = int((data.get("checkpoint_counts") or {}).get(kind) or 0)
    if count < threshold:
        return None
    missing: list[str] = []
    packet = data.get("result_packet") or {}
    if isinstance(packet, dict):
        artifacts = packet.get("artifacts") or {}
        if not (artifacts.get("screenshots") or packet.get("ui_quality")):
            missing.append("screenshot or UI evidence in the result packet")
        report = packet.get("harness_report") or packet.get("iteration_report")
        if not report:
            missing.append("harness return contract")
    step_result = (data.get("step_review_result") or data.get("run_briefing_text") or "").strip()
    if not step_result:
        missing.append("step outcome summary")
    missing_line = ", ".join(missing) if missing else "unknown — inspect Active Runs → Executor"
    return (
        f"**Workflow stuck — diagnostics (not another approval)**\n\n"
        f"I have paused {count} times on `{kind}` without finishing this run.\n\n"
        f"**What is waiting:** human checkpoint `{kind}`\n"
        f"**Likely missing:** {missing_line}\n"
        f"**What I need from you:** one plain-English steer — continue, stop, or what to fix.\n"
        f"**Or:** open Workflows → Active Runs and cancel this run if it is orphaned test work.\n\n"
        "Reply **stop** to cancel, or describe the fix you want."
    )
