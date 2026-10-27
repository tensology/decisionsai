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

from typing import Any, Iterable, Optional


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
