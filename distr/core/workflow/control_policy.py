"""Conservative control policy for workflow learning, steering, and interruption.

The policy is deliberately pure.  Callers may persist its decisions, but a
single ticket instruction must never silently become a permanent project rule.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Literal


LearningDisposition = Literal["run_only", "candidate", "promote"]
SteeringImpact = Literal["local", "plan", "route", "stop"]


def resolve_inspection_budget(
    value: Any,
    *,
    complexity: str = "medium",
    ticket_context: str = "",
    step_role: str = "",
) -> dict[str, Any]:
    """Resolve a step's inspection ceiling for the current ticket complexity.

    A single hard ceiling makes small tickets wasteful and complex recovery work
    fail one call before it has enough evidence.  Presets may keep a conservative
    default while declaring explicit per-complexity ceilings.
    """
    budget = dict(value) if isinstance(value, dict) else {}
    configured = budget.get("max_tool_calls_by_complexity")
    configured = configured if isinstance(configured, dict) else {}
    level = str(complexity or "medium").strip().lower()
    if level not in {"low", "medium", "high"}:
        level = "medium"
    selected = configured.get(level, budget.get("max_tool_calls"))
    try:
        maximum = max(0, int(selected or 0))
    except (TypeError, ValueError):
        maximum = 0
    budget["max_tool_calls"] = maximum
    budget["resolved_for_complexity"] = level
    enforcement = str(budget.get("enforcement") or "hard").strip().lower()
    if enforcement not in {"hard", "soft"}:
        enforcement = "hard"
    budget["enforcement"] = enforcement
    raw_hard_maximum = budget.get("hard_max_tool_calls")
    if enforcement == "soft" and raw_hard_maximum in (None, ""):
        raw_hard_maximum = max(maximum + 2, math.ceil(maximum * 1.5)) if maximum else 0
    try:
        hard_maximum = max(maximum, int(raw_hard_maximum or maximum or 0))
    except (TypeError, ValueError):
        hard_maximum = maximum

    # A research/documentation ticket using the implementation worker should
    # not inherit the exploratory budget of a high-complexity code change. The
    # ticket may still be high consequence, but its execution surface is small:
    # consume the contract, reuse named evidence, and update the documentary
    # artifact. Keep this generic and derived from the ticket contract.
    role = str(step_role or "").strip().lower()
    if role in {"implementation", "correction"} and str(ticket_context or "").strip():
        try:
            from distr.core.workflow.ticket_contract import classify_ticket_execution

            profile = classify_ticket_execution(ticket_context)
        except Exception:
            profile = {}
        if profile.get("research_only") or profile.get("explicit_no_code"):
            target_cap = 10
            hard_cap = 14
            maximum = min(maximum, target_cap) if maximum else target_cap
            hard_maximum = min(hard_maximum, hard_cap) if hard_maximum else hard_cap
            hard_maximum = max(maximum, hard_maximum)
            budget["enforcement"] = "soft"
            budget["ticket_profile"] = "research_or_no_code"
    budget["hard_max_tool_calls"] = hard_maximum
    budget["max_tool_calls"] = maximum
    return budget


@dataclass(frozen=True)
class LearningDecision:
    disposition: LearningDisposition
    promote_after: int
    reason: str

    @property
    def should_record(self) -> bool:
        return self.disposition != "run_only"

    @property
    def enabled(self) -> bool:
        return self.disposition == "promote"


@dataclass(frozen=True)
class SteeringDecision:
    impact: SteeringImpact
    reason: str
    route_preference: str = ""


@dataclass(frozen=True)
class InterruptionDecision:
    should_interrupt: bool
    reason: str
    question: str = ""
    recommendation: str = ""
    options: tuple[str, ...] = ()
    can_continue_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_interrupt": self.should_interrupt,
            "reason": self.reason,
            "question": self.question,
            "recommendation": self.recommendation,
            "options": list(self.options),
            "can_continue_default": self.can_continue_default,
        }


_EXPLICIT_DURABLE_RE = re.compile(
    r"\b(always|never|every\s+time|from\s+now\s+on|for\s+future\s+(?:runs|tickets)|"
    r"remember\s+(?:this|that)|make\s+this\s+(?:a\s+)?(?:rule|standard))\b",
    re.IGNORECASE,
)
_REUSABLE_QUALITY_RE = re.compile(
    r"\b(validate|validation|test|tests|review|quality|accessib|security|baseline|"
    r"acceptance\s+criteria|before\s+marking|must\s+pass|do\s+not\s+ship)\b",
    re.IGNORECASE,
)
_ONE_RUN_RE = re.compile(
    r"\b(for\s+this\s+(?:run|ticket|step)|this\s+time|right\s+now|only\s+this|"
    r"just\s+this|on\s+this\s+one)\b",
    re.IGNORECASE,
)


def classify_learning_signal(
    message: str,
    *,
    event_type: str = "",
    trusted_failure: bool = False,
) -> LearningDecision:
    """Decide whether feedback is transient, a candidate, or durable now."""
    text = " ".join(str(message or "").split()).strip()
    event = str(event_type or "").strip().lower().replace("-", "_")
    if not text:
        return LearningDecision("run_only", 0, "There is no reusable feedback to learn.")
    if _ONE_RUN_RE.search(text) and not _EXPLICIT_DURABLE_RE.search(text):
        return LearningDecision("run_only", 0, "The instruction is explicitly scoped to this run.")
    if _EXPLICIT_DURABLE_RE.search(text):
        return LearningDecision("promote", 1, "The user explicitly requested a durable rule.")
    if trusted_failure or event in {
        "validation_failed",
        "manual_fix",
        "changes_requested",
        "manual_fix_applied",
    }:
        return LearningDecision("candidate", 2, "A verified correction should recur before promotion.")
    if _REUSABLE_QUALITY_RE.search(text):
        return LearningDecision("candidate", 2, "This may be reusable quality guidance, pending repeat evidence.")
    return LearningDecision("run_only", 0, "This is ordinary run steering, not durable policy.")


_STOP_RE = re.compile(
    r"^(?:please\s+)?(?:stop|cancel|abort|pause|hold\s+off)(?:\s+(?:the\s+)?(?:run|workflow|execution|everything))?[.!\s]*$",
    re.IGNORECASE,
)
_ROUTE_RE = re.compile(
    r"\b(codex|cursor|claude(?:\s+code)?|ornith|ollama|openrouter|local\s+model|"
    r"free\s+model|provider|swap\s+(?:the\s+)?model|change\s+(?:the\s+)?model)\b",
    re.IGNORECASE,
)
_PLAN_RE = re.compile(
    r"\b(skip|drop|remove|add\s+(?:a\s+)?step|instead|change\s+(?:the\s+)?plan|"
    r"focus\s+on|acceptance\s+criteria|scope|do\s+not\s+touch|only\s+work\s+on|"
    r"stop\s+(?:refactoring|changing|editing|touching))\b",
    re.IGNORECASE,
)


def classify_steering(message: str) -> SteeringDecision:
    """Classify how far a steer should affect the active run overlay."""
    text = " ".join(str(message or "").split()).strip()
    if _STOP_RE.search(text):
        return SteeringDecision("stop", "The instruction requests that execution stop or pause.")
    route_match = _ROUTE_RE.search(text)
    if route_match:
        token = route_match.group(1).lower()
        preference = (
            "claude_code" if token.startswith("claude") else
            "local" if token in {"ornith", "ollama", "local model"} else
            "free" if token == "free model" else
            token.replace(" ", "_")
        )
        return SteeringDecision("route", "The instruction changes worker or model selection.", preference)
    if _PLAN_RE.search(text):
        return SteeringDecision("plan", "The instruction changes scope or future plan execution.")
    return SteeringDecision("local", "The instruction can be handled by the active worker.")


def decide_interruption(
    *,
    worker_status: str = "",
    question: str = "",
    blockers: str = "",
    confidence: float | None = None,
    repeated_failures: int = 0,
    paid_escalation: bool = False,
    irreversible: bool = False,
) -> InterruptionDecision:
    """Return the one human-interruption decision used by every workflow path."""
    status = str(worker_status or "").strip().lower().replace("-", "_")
    blocker_text = " ".join(str(blockers or "").split()).strip()
    prompt = " ".join(str(question or "").split()).strip()
    if irreversible:
        return InterruptionDecision(
            True,
            "The next action is irreversible or affects an external system.",
            prompt or "This action cannot be safely undone. Would you like me to proceed?",
            "Review the impact before approving it.",
            ("Approve", "Stop"),
        )
    if paid_escalation:
        return InterruptionDecision(
            True,
            "The next route uses a paid or materially more expensive model.",
            prompt or "The preferred worker is unavailable. Should I use the recommended paid fallback?",
            "Use the recommended fallback only for the blocked step.",
            ("Use recommended fallback", "Choose another model", "Stop"),
        )
    if status in {
        "needs_input",
        "worker_needs_input",
        "codex_needs_input",
        "cursor_needs_input",
        "codex_waiting",
        "cursor_waiting",
    }:
        detail = blocker_text if blocker_text and blocker_text.lower() != "none" else ""
        return InterruptionDecision(
            True,
            detail or "The worker is waiting for information it cannot safely infer.",
            prompt or (f"The worker is blocked by: {detail}. What should it use?" if detail else "What information should the worker use to continue?"),
            "Reply with the missing information, steer the run, or stop it.",
            ("Reply with details", "Steer", "Stop"),
        )
    if repeated_failures >= 2:
        return InterruptionDecision(
            True,
            "The same step failed repeatedly and automatic correction is no longer productive.",
            prompt or "The step has failed twice. Should I change approach, change worker, or stop?",
            "Change the approach or worker before retrying.",
            ("Change approach", "Change worker", "Stop"),
        )
    if confidence is not None and confidence < 0.55:
        return InterruptionDecision(
            True,
            "The decision would materially change the outcome and confidence is low.",
            prompt or "I cannot safely choose between the available paths. Which outcome do you want?",
            "Choose the option that best preserves the ticket acceptance criteria.",
            ("Reply with a choice", "Stop"),
        )
    return InterruptionDecision(False, "The work is reversible or can be validated automatically.", can_continue_default=True)
