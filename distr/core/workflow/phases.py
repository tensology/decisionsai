"""Phase-based workflow composition.

A workflow is a small composition of reusable *phases*. The old ticket pipeline
dispatched a fresh CLI agent per step, so every step cold-started, re-read the
repo and re-loaded the ticket. A "single session" workflow instead asks one
agent process to move through its phases in order and keep its own context.

This module is deliberately pure: it composes instruction text and classifies
work. Nothing here touches the database except ``create_phased_workflow``, which
delegates to the existing workflow service.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Marks a workflow as owned by the phase engine, so the router can find the
# managed workflow for each weight class without guessing at names.
MANAGED_BY = "phase_engine"

# Present in a composed instruction when the reply phase should be enriched at
# run time with the ticket's real channel and provenance.
REPLY_PHASE_MARKER = "[REPLY PHASE ENABLED]"


# ---------------------------------------------------------------------------
# Phase atoms
# ---------------------------------------------------------------------------

PHASE_ORDER: tuple[str, ...] = (
    "understand",
    "reproduce",
    "research",
    "implement",
    "test",
    "fix_defects",
    "review",
    "draft",
    "send",
    "deploy",
    "verify",
    "report",
    "reply",
)

PHASES: dict[str, dict[str, str]] = {
    "understand": {
        "label": "Understand",
        "instruction": (
            "Read the ticket, its acceptance criteria and its source. Load only the "
            "context this change needs: the files you will touch, the project's real "
            "commands, and any project rules that apply. State the smallest shippable "
            "slice before you touch anything."
        ),
    },
    "reproduce": {
        "label": "Reproduce",
        "instruction": (
            "Prove the problem exists first. Run the failing case, capture the real "
            "error, and write down the exact command you used. Do not start editing "
            "until you can make it fail on demand."
        ),
    },
    "research": {
        "label": "Research",
        "instruction": (
            "Gather evidence from the real system, docs or the web. Prefer primary "
            "sources. Record what you checked and what you concluded, including the "
            "options you rejected and why."
        ),
    },
    "implement": {
        "label": "Implement",
        "instruction": (
            "Make the smallest change that satisfies the acceptance criteria. Match "
            "the surrounding code. Do not refactor unrelated code, do not add "
            "speculative abstractions, and do not leave debug scaffolding behind."
        ),
    },
    "test": {
        "label": "Test",
        "instruction": (
            "Run the project's real checks for this change: its test command, its "
            "linter, and a manual or browser check where the change is visual. Paste "
            "the exact commands and their results."
        ),
    },
    "fix_defects": {
        "label": "Fix defects",
        "instruction": (
            "Fix only the defects the previous checks actually found, then re-run the "
            "same checks. If a fix fails twice, stop and report needs_input instead of "
            "guessing. Do not widen scope to make a check pass."
        ),
    },
    "review": {
        "label": "Independent review",
        "instruction": (
            "Review the diff as someone who did not write it. Look for correctness, "
            "regressions, security, dead code and gaps against the acceptance "
            "criteria. Report findings; do not implement fixes."
        ),
    },
    "draft": {
        "label": "Draft",
        "instruction": (
            "Write the artefact the ticket asks for: the reply, the brief, the copy or "
            "the proposal. Plain language, no filler, no em dashes. State what is "
            "known, what is assumed and what is still open."
        ),
    },
    "send": {
        "label": "Send",
        "instruction": (
            "Deliver the drafted message on the ticket's own channel, using the "
            "recorded provenance. Confirm the recipient and the thread before sending, "
            "and send once only."
        ),
    },
    "deploy": {
        "label": "Deploy",
        "instruction": (
            "Run the maintained deploy command for the approved commit. Confirm the "
            "target before you run it, and report the exact commit that went out."
        ),
    },
    "verify": {
        "label": "Verify",
        "instruction": (
            "After the change is live, check it actually works: health endpoint, smoke "
            "test, or the restored user path. Report evidence, not intent."
        ),
    },
    "report": {
        "label": "Report",
        "instruction": (
            "Return a compact result packet: summary, files changed, commands run, "
            "evidence, what is verified and what is not, and any follow-up ticket."
        ),
    },
    "reply": {
        "label": "Reply",
        "instruction": (
            "Close the loop with the person or channel that raised this: what was "
            "asked, what was done, and what they should expect next. Human, specific, "
            "no corporate padding."
        ),
    },
}

# Phases that change a repository and therefore need a linked project.
REPO_PHASES = frozenset({"reproduce", "implement", "test", "fix_defects", "review", "verify"})


# ---------------------------------------------------------------------------
# Weight classes: budget and gates, not steps
# ---------------------------------------------------------------------------

WORKFLOW_CLASSES: dict[str, dict[str, Any]] = {
    "light": {
        "label": "Light",
        "phases": ["understand", "draft", "reply"],
        "max_attempts": 1,
        "independent_review": False,
        "human_gate": False,
        "may_deploy": False,
    },
    "standard": {
        "label": "Standard",
        "phases": ["understand", "implement", "test", "fix_defects", "report", "reply"],
        "max_attempts": 2,
        "independent_review": False,
        "human_gate": False,
        "may_deploy": False,
    },
    "heavy": {
        "label": "Heavy",
        "phases": [
            "understand",
            "implement",
            "test",
            "fix_defects",
            "review",
            "report",
            "reply",
        ],
        "max_attempts": 2,
        "independent_review": True,
        "human_gate": True,
        "may_deploy": False,
    },
    "critical": {
        "label": "Critical",
        "phases": [
            "understand",
            "reproduce",
            "implement",
            "test",
            "fix_defects",
            "review",
            "report",
            "reply",
        ],
        "max_attempts": 2,
        "independent_review": True,
        "human_gate": True,
        "may_deploy": True,
    },
}

# Words that can only ever *escalate* the class. Never used to downgrade.
RISK_KEYWORDS: tuple[str, ...] = (
    "migration",
    "migrate",
    "schema",
    "database change",
    "auth",
    "authentication",
    "authorisation",
    "authorization",
    "password",
    "credential",
    "secret",
    "token",
    "payment",
    "billing",
    "invoice",
    "refund",
    "dns",
    "ssl",
    "certificate",
    "deploy",
    "production",
    "prod ",
    "rollback",
    "drop table",
    "delete data",
)

# Words that suggest no repository work at all.
QUESTION_MARKERS: tuple[str, ...] = (
    "?",
    "how do i",
    "how do we",
    "what is",
    "what's",
    "where is",
    "can you tell",
    "status of",
    "explain",
    "any news",
)


def classify(
    text: str,
    *,
    has_repo_target: bool = True,
    requested: Optional[str] = None,
) -> str:
    """Return the weight class for a ticket.

    ``requested`` is an explicit override and wins, except that risk keywords can
    still force an escalation. The classifier never quietly downgrades a risky
    ticket, and never upgrades a plain question into repository work.
    """
    lowered = str(text or "").lower()
    risky = any(keyword in lowered for keyword in RISK_KEYWORDS)

    if requested and requested in WORKFLOW_CLASSES:
        chosen = str(requested)
    elif not has_repo_target and any(marker in lowered for marker in QUESTION_MARKERS):
        chosen = "light"
    elif risky:
        chosen = "critical"
    elif not has_repo_target:
        chosen = "light"
    else:
        chosen = "standard"

    if risky:
        # Risk only escalates: light -> heavy, standard -> critical.
        chosen = "critical" if chosen in {"standard", "critical"} else "heavy"
    return chosen


def class_policy(class_key: str) -> dict[str, Any]:
    """Return the budget and gate policy for a weight class."""
    return dict(WORKFLOW_CLASSES.get(class_key) or WORKFLOW_CLASSES["standard"])


def phases_for_class(class_key: str, *, extra: Optional[Iterable[str]] = None) -> list[str]:
    """Ordered phase list for a class, plus any explicitly requested extras."""
    chosen = list(class_policy(class_key).get("phases") or [])
    for phase in extra or []:
        if phase in PHASES and phase not in chosen:
            chosen.append(phase)
    return [phase for phase in PHASE_ORDER if phase in chosen]


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def compose_instruction(
    phases: Iterable[str],
    *,
    goal: str = "",
    done_when: str = "",
    context: str = "",
    needs_human_gate: bool = False,
    may_deploy: bool = False,
) -> str:
    """Compose several phases into ONE instruction for ONE agent session.

    This is the heart of the cheaper pipeline: the agent keeps its own context
    across the phases instead of every phase rebooting a fresh process.
    """
    ordered = [phase for phase in PHASE_ORDER if phase in set(phases)]
    if not ordered:
        ordered = ["understand", "implement", "test", "report"]

    lines: list[str] = ["[WORKFLOW PHASES - one continuous session]"]
    if goal.strip():
        lines.append(f"GOAL\n{goal.strip()}")
    if context.strip():
        lines.append(f"CONTEXT\n{context.strip()}")
    lines.append(
        "Work through the phases below in order, in this same session. Do not "
        "restart the task between phases. Keep going until the work is done or you "
        "are genuinely blocked."
    )

    for index, phase in enumerate(ordered, start=1):
        meta = PHASES[phase]
        lines.append(f"{index}. {meta['label'].upper()}\n{meta['instruction']}")

    contract: list[str] = []
    if done_when.strip():
        contract.append(f"Done when: {done_when.strip()}")
    contract.append(
        "Finish with: status (done, needs_input or failed), what changed, the exact "
        "commands you ran, the evidence they produced, and anything still unverified."
    )
    if "reply" in ordered:
        # Delivery is deterministic and reads this exact field. A missing field
        # means nothing is sent, so the contract must name it.
        contract.append(
            "Return the message for the person who raised this as a `reply_text:` field, "
            "written for their channel. Keep internal detail, file paths and logs out of it."
        )
    if needs_human_gate:
        contract.append(
            "Stop and report needs_input before anything irreversible: schema changes, "
            "credential or auth changes, deleting data, or deploying."
        )
    if may_deploy:
        contract.append(
            "Deploy only the exact commit you verified, and report the commit that went out."
        )
    else:
        contract.append("Do not deploy. Leave the change ready for a separate release decision.")
    lines.append("COMPLETION CONTRACT\n" + "\n".join(f"- {item}" for item in contract))

    lines.append(
        "BUDGET\n"
        "- Do not repeat a failed attempt three times. Two tries, then report needs_input.\n"
        "- Do not explore the repository beyond what this change needs.\n"
        "- Do not re-run the whole test suite when only one check is relevant."
    )
    if "reply" in ordered:
        # The channel-aware reply rules need the ticket, so they are appended at
        # run time. The marker is how the dispatcher knows to do that.
        lines.append(REPLY_PHASE_MARKER)
    return "\n\n".join(lines)


def build_workflow_definition(
    name: str,
    class_key: str,
    *,
    goal: str = "",
    done_when: str = "",
    description: str = "",
    phases: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Return the step definitions for a single-session workflow.

    One working step carries every phase; an independent review, when the policy
    asks for it, is a second step because independence is the point.
    """
    policy = class_policy(class_key)
    if phases is None:
        phases = phases_for_class(class_key)
    phase_list = [phase for phase in PHASE_ORDER if phase in set(phases)]

    review_phases = [phase for phase in phase_list if phase == "review"]
    working_phases = [phase for phase in phase_list if phase != "review"]
    if not working_phases:
        working_phases = ["understand", "report"]

    steps: list[dict[str, Any]] = [
        {
            "name": f"{policy['label']} session",
            "action_type": "send_to_project_cli",
            "instruction": compose_instruction(
                working_phases,
                goal=goal,
                done_when=done_when,
                needs_human_gate=bool(policy.get("human_gate")),
                may_deploy=bool(policy.get("may_deploy")),
            ),
            "config": {
                "step_role": "implementation",
                "max_retries": int(policy.get("max_attempts") or 2),
                "allow_provider_failover": True,
            },
        }
    ]

    if policy.get("independent_review") and review_phases:
        steps.append(
            {
                "name": "Independent review",
                "action_type": "send_to_project_cli",
                "instruction": compose_instruction(
                    review_phases,
                    goal=goal,
                    done_when="The diff has been reviewed against the acceptance criteria.",
                ),
                "config": {
                    "step_role": "review",
                    "read_only_expected": True,
                    "max_retries": 1,
                },
            }
        )

    return {
        "name": name or f"{policy['label']} session",
        "description": description or goal,
        "workflow_type": "manual",
        "class": class_key,
        "steps": steps,
    }


CLASS_WORKFLOW_DESCRIPTIONS: dict[str, str] = {
    "light": "Questions, lookups and status replies. No repository work, no deploy.",
    "standard": "A normal ticket: understand, implement, test, fix defects, report, reply in one session.",
    "heavy": "A bigger change: the standard session plus an independent review and a human gate.",
    "critical": "Risky work: reproduce first, independent review, human gates, deploy allowed only on approval.",
}


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
        return loaded if isinstance(loaded, dict) else {}
    except (TypeError, ValueError):
        return {}


def workflow_weight_class(run_settings: Any) -> Optional[str]:
    """Return the managed weight class for a workflow, or None if unmanaged."""
    data = _json_dict(run_settings)
    if str(data.get("managed_by") or "").strip().lower() != MANAGED_BY:
        return None
    class_key = str(data.get("weight_class") or "").strip().lower()
    return class_key if class_key in WORKFLOW_CLASSES else None


def class_workflow_name(class_key: str) -> str:
    label = str(class_policy(class_key).get("label") or class_key)
    return f"Ticket: {label.lower()} (one session)"


def _mark_managed(workflow_id: int, class_key: str) -> None:
    """Tag and activate a workflow so it shows as runnable managed policy."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow

    with get_session() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not workflow:
            return
        settings = _json_dict(workflow.run_settings)
        settings.update({
            "managed_by": MANAGED_BY,
            "weight_class": class_key,
            "max_attempts": int(class_policy(class_key).get("max_attempts") or 2),
            "human_gate": bool(class_policy(class_key).get("human_gate")),
            "may_deploy": bool(class_policy(class_key).get("may_deploy")),
        })
        workflow.run_settings = json.dumps(settings)
        workflow.status = "active"
        db.commit()


def find_class_workflow_id(class_key: str) -> Optional[int]:
    """Return the managed workflow id for a weight class, if one exists."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow

    wanted = str(class_key or "").strip().lower()
    if wanted not in WORKFLOW_CLASSES:
        return None
    with get_session() as db:
        rows = (
            db.query(AutoWorkflow)
            .filter(AutoWorkflow.status != "archived")
            .all()
        )
        for row in rows:
            if workflow_weight_class(row.run_settings) == wanted:
                return int(row.id)
    return None


def ensure_class_workflows() -> dict[str, int]:
    """Create, tag and activate the managed workflow for every weight class."""
    created: dict[str, int] = {}
    for class_key in WORKFLOW_CLASSES:
        workflow_id = find_class_workflow_id(class_key)
        if workflow_id is None:
            workflow_id = create_phased_workflow(
                class_workflow_name(class_key),
                class_key,
                goal="Complete the linked ticket using this workflow's policy.",
                done_when="The ticket is done, evidence is returned, and the source gets a reply.",
                description=CLASS_WORKFLOW_DESCRIPTIONS.get(class_key, ""),
            )
        if workflow_id:
            _mark_managed(int(workflow_id), class_key)
            created[class_key] = int(workflow_id)
    return created


def rebuild_class_workflow(
    workflow_id: int,
    class_key: str,
    *,
    goal: str = "Complete the linked ticket using this workflow's policy.",
    done_when: str = "The ticket is done, evidence is returned, and the source gets a reply.",
) -> int:
    """Replace a managed workflow's steps from the current phase definition."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflowStep
    from distr.core.workflow.service import add_step

    definition = build_workflow_definition(
        class_workflow_name(class_key),
        class_key,
        goal=goal,
        done_when=done_when,
        description=CLASS_WORKFLOW_DESCRIPTIONS.get(class_key, ""),
    )
    with get_session() as db:
        db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.workflow_id == int(workflow_id)
        ).delete(synchronize_session=False)
        db.commit()
    for position, step in enumerate(definition["steps"]):
        add_step(
            int(workflow_id),
            name=str(step["name"]),
            action_type=str(step["action_type"]),
            position=position,
            instruction=str(step["instruction"]),
            config=dict(step.get("config") or {}),
        )
    return int(workflow_id)


def refresh_managed_workflow_instructions() -> dict[str, int]:
    """Rewrite managed workflows' step instructions from the current phase definition.

    A workflow persists the instruction composed when it was created, so improving
    the phase contract (e.g. requiring ``reply_text:``) never reached workflows that
    already existed. This updates in place, keeping step ids and run history.
    """
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflowStep

    updated: dict[str, int] = {}
    for class_key in WORKFLOW_CLASSES:
        workflow_id = find_class_workflow_id(class_key)
        if not workflow_id:
            continue
        definition = build_workflow_definition(
            class_workflow_name(class_key),
            class_key,
            goal="Complete the linked ticket using this workflow's policy.",
            done_when="The ticket is done, evidence is returned, and the source gets a reply.",
            description=CLASS_WORKFLOW_DESCRIPTIONS.get(class_key, ""),
        )
        with get_session() as db:
            steps = (
                db.query(AutoWorkflowStep)
                .filter(AutoWorkflowStep.workflow_id == int(workflow_id))
                .order_by(AutoWorkflowStep.position)
                .all()
            )
            for step, spec in zip(steps, definition["steps"]):
                step.name = str(spec["name"])
                step.instruction = str(spec["instruction"])
                if spec.get("config"):
                    step.config = json.dumps(spec["config"])
            db.commit()
            updated[class_key] = len(steps)
    return updated


def route_ticket(
    text: str,
    *,
    has_repo_target: bool = True,
    requested: Optional[str] = None,
    ticket_id: Optional[int] = None,
) -> dict[str, Any]:
    """Decide the weight class for a ticket and explain the choice.

    Returns the class, its policy, the matching managed workflow (when it exists)
    and a human sentence that can be shown or spoken. The classifier can only
    escalate on risk, so a risky ticket never routes cheap by accident.
    """
    class_key = classify(text, has_repo_target=has_repo_target, requested=requested)
    reason = route_reason(
        text,
        class_key=class_key,
        requested=requested,
        has_repo_target=has_repo_target,
    )
    return {
        "class": class_key,
        "label": str(class_policy(class_key).get("label") or class_key),
        "reason": reason,
        "policy": class_policy(class_key),
        "workflow_id": find_class_workflow_id(class_key),
        "announcement": format_route_announcement(class_key, reason, ticket_id=ticket_id),
        "overridable": True,
    }


def route_reason(
    text: str,
    *,
    class_key: str,
    requested: Optional[str] = None,
    has_repo_target: bool = True,
) -> str:
    """Plain-language reason for a routing choice. Pure, so any caller can explain itself."""
    if requested and requested in WORKFLOW_CLASSES and requested == class_key:
        return "you asked for this one"
    lowered = str(text or "").lower()
    risk_hit = next((word for word in RISK_KEYWORDS if word in lowered), "")
    if risk_hit:
        return f"it mentions {risk_hit.strip()}"
    if not has_repo_target:
        return "it looks like a question, not a code change"
    return "it's normal repository work with no risk flags"


def format_route_announcement(
    class_key: str,
    reason: str,
    *,
    ticket_id: Optional[int] = None,
    overridable: bool = True,
) -> str:
    """The sentence shown and spoken when a ticket is routed.

    ``overridable=False`` drops the change-your-mind sentence for channels that
    cannot yet act on it, so the message never promises more than we honour.
    """
    label = str(class_policy(class_key).get("label") or class_key)
    prefix = f"Ticket #{int(ticket_id)}" if ticket_id is not None else "This ticket"
    base = f"{prefix} goes to the {label} workflow ({reason})."
    if not overridable:
        return base
    return f"{base} Say 'make it lighter' or 'make it heavier' to change that."


def find_class_workflow_id_in_session(session: Any, class_key: str) -> Optional[int]:
    """Session-safe lookup of a managed class workflow.

    Intake already holds a transaction, so it cannot call the creating helper
    without risking a nested write lock. Managed workflows are seeded at
    startup, so a read-only lookup is enough here.
    """
    from distr.core.db.workflow import AutoWorkflow

    wanted = str(class_key or "").strip().lower()
    if wanted not in WORKFLOW_CLASSES:
        return None
    rows = session.query(AutoWorkflow).filter(AutoWorkflow.status != "archived").all()
    for row in rows:
        if workflow_weight_class(row.run_settings) == wanted:
            return int(row.id)
    return None


def is_retired_workflow(workflow_id: Optional[int]) -> bool:
    """True when a workflow is gone or archived, so its link must not be reused.

    Tickets pinned to the retired Development pipeline would otherwise resume the
    11-step flow forever, because their link outlives the workflow itself.
    """
    if not workflow_id:
        return False
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow

    try:
        with get_session() as db:
            row = (
                db.query(AutoWorkflow)
                .filter(AutoWorkflow.id == int(workflow_id))
                .first()
            )
            return row is None or str(row.status or "").lower() == "archived"
    except Exception:
        logger.debug("Could not check workflow %s status", workflow_id, exc_info=True)
        return False


def relink_ticket_to_workflow(ticket_id: int, workflow_id: int) -> bool:
    """Point a ticket at the workflow it should actually run on."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket

    try:
        with get_session() as db:
            ticket = (
                db.query(KanbanTicket)
                .filter(KanbanTicket.id == int(ticket_id))
                .first()
            )
            if ticket is None:
                return False
            ticket.linked_workflow_id = int(workflow_id)
            db.commit()
            return True
    except Exception:
        logger.debug("Could not relink ticket %s", ticket_id, exc_info=True)
        return False


def route_ticket_plan(
    text: str,
    *,
    has_repo_target: bool = True,
    requested: Optional[str] = None,
    ticket_id: Optional[int] = None,
) -> dict[str, Any]:
    """Route a ticket and guarantee a usable workflow id.

    Creates the managed workflows on first use so a fresh install can route work
    without anyone hand-building a pipeline. Falls back to ``standard`` rather
    than returning nothing, because dispatching a ticket with no workflow is
    worse than dispatching it on the default policy.
    """
    plan = route_ticket(
        text,
        has_repo_target=has_repo_target,
        requested=requested,
        ticket_id=ticket_id,
    )
    if plan.get("workflow_id"):
        return plan
    try:
        ensure_class_workflows()
    except Exception:
        logger.warning("Could not create managed class workflows", exc_info=True)
    plan["workflow_id"] = (
        find_class_workflow_id(str(plan.get("class") or ""))
        or find_class_workflow_id("standard")
        or None
    )
    return plan


def route_ticket_workflow_id(
    text: str,
    *,
    has_repo_target: bool = True,
    requested: Optional[str] = None,
) -> int:
    """Return the managed workflow id a ticket should run on."""
    plan = route_ticket_plan(text, has_repo_target=has_repo_target, requested=requested)
    return int(plan.get("workflow_id") or 0)


def announce_route_choice(
    chat_id: Optional[int],
    text: str,
    *,
    ticket_id: Optional[int] = None,
    has_repo_target: bool = True,
    requested: Optional[str] = None,
    speak: bool = True,
) -> dict[str, Any]:
    """Tell the user which policy picked up their ticket, and why.

    Routing is only trustworthy if it is visible and overridable, so the choice
    is posted into the thread and read aloud when voice is available.
    """
    plan = route_ticket_plan(
        text,
        has_repo_target=has_repo_target,
        requested=requested,
        ticket_id=ticket_id,
    )
    announcement = str(plan.get("announcement") or "").strip()
    if announcement and chat_id:
        try:
            from distr.core.chat import ChatService

            ChatService.append_assistant_notice(int(chat_id), announcement)
        except Exception:
            logger.debug("route announcement notice failed", exc_info=True)
        if speak:
            try:
                from distr.core.signals import speak_text_directly_event_queue

                speak_text_directly_event_queue(announcement)
            except Exception:
                logger.debug("route announcement speech failed", exc_info=True)
    return plan


# Overrides must be deliberate: a trigger phrase plus a direction or class word.
_OVERRIDE_TRIGGERS = (
    "make it ",
    "use the ",
    "switch to ",
    "route it ",
    "put it on ",
    "change it to ",
)
_ROUTE_UP_WORDS = ("heavier", "bigger", "more careful", "escalate")
_ROUTE_DOWN_WORDS = ("lighter", "smaller", "simpler", "downgrade")
CLASS_LADDER: tuple[str, ...] = ("light", "standard", "heavy", "critical")


def detect_route_override(text: str) -> Optional[str]:
    """Return a requested class, or "up"/"down", from an explicit override phrase."""
    lowered = str(text or "").lower().strip()
    if not any(trigger in lowered for trigger in _OVERRIDE_TRIGGERS):
        return None
    for key in CLASS_LADDER:
        if re.search(rf"\b{key}\b", lowered):
            return key
    if any(word in lowered for word in _ROUTE_UP_WORDS):
        return "up"
    if any(word in lowered for word in _ROUTE_DOWN_WORDS):
        return "down"
    return None


def _step_class(base_class: str, direction: str) -> str:
    if direction in CLASS_LADDER:
        return direction
    index = CLASS_LADDER.index(base_class) if base_class in CLASS_LADDER else 1
    index = min(len(CLASS_LADDER) - 1, index + 1) if direction == "up" else max(0, index - 1)
    return CLASS_LADDER[index]


def apply_route_override(
    ticket_id: int,
    text: str,
    *,
    chat_id: Optional[int] = None,
    speak: bool = True,
) -> dict[str, Any]:
    """Honour "make it lighter/heavier" by relinking the ticket to that policy.

    Risk still wins: a migration cannot be talked down to the cheap policy, so the
    override is floored at ``heavy`` whenever risk keywords are present.
    """
    direction = detect_route_override(text)
    if not direction:
        return {"changed": False}

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket

    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        if ticket is None:
            return {"changed": False}
        ticket_text = f"{ticket.title or ''}\n\n{ticket.description or ''}"

    base_class = classify(ticket_text, has_repo_target=True)
    target_class = _step_class(base_class, direction)
    if any(word in ticket_text.lower() for word in RISK_KEYWORDS) and CLASS_LADDER.index(target_class) < CLASS_LADDER.index("heavy"):
        target_class = "heavy"

    workflow_id = find_class_workflow_id(target_class)
    if not workflow_id:
        try:
            ensure_class_workflows()
        except Exception:
            logger.warning("Could not create managed class workflows", exc_info=True)
        workflow_id = find_class_workflow_id(target_class)
    if not workflow_id:
        return {"changed": False, "error": f"No workflow exists for the {target_class} policy"}

    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        if ticket is None:
            return {"changed": False}
        ticket.linked_workflow_id = int(workflow_id)
        db.commit()

    label = str(class_policy(target_class).get("label") or target_class)
    announcement = (
        f"Ticket #{int(ticket_id)} is now on the {label} workflow. "
        f"That takes effect on the next step."
    )
    if chat_id:
        try:
            from distr.core.chat import ChatService

            ChatService.append_assistant_notice(int(chat_id), announcement)
        except Exception:
            logger.debug("route override notice failed", exc_info=True)
        if speak:
            try:
                from distr.core.signals import speak_text_directly_event_queue

                speak_text_directly_event_queue(announcement)
            except Exception:
                logger.debug("route override speech failed", exc_info=True)
    return {
        "changed": True,
        "class": target_class,
        "workflow_id": int(workflow_id),
        "announcement": announcement,
    }


def create_phased_workflow(
    name: str,
    class_key: str,
    *,
    goal: str = "",
    done_when: str = "",
    description: str = "",
    phases: Optional[Iterable[str]] = None,
    chat_id: Optional[int] = None,
) -> Optional[int]:
    """Persist a single-session workflow built from phases. Returns its id."""
    from distr.core.workflow.service import add_step, create_workflow

    definition = build_workflow_definition(
        name,
        class_key,
        goal=goal,
        done_when=done_when,
        description=description,
        phases=phases,
    )
    workflow_id = create_workflow(
        name=str(definition["name"]),
        description=str(definition["description"] or ""),
        workflow_type=str(definition["workflow_type"]),
    )
    if not workflow_id:
        return None
    for position, step in enumerate(definition["steps"]):
        add_step(
            workflow_id,
            name=str(step["name"]),
            action_type=str(step["action_type"]),
            position=position,
            instruction=str(step["instruction"]),
            config=dict(step.get("config") or {}),
        )
    return workflow_id
