"""
Hand-authored loop preset definitions.

Active presets follow workflows-by-intent:
- Ideation: requirements → brief → board tickets (no Cursor)
- Development: ticket → plan → CLI harness implementation
- Polish: security, UI drift, regression, release evidence
"""

from __future__ import annotations

from typing import Any

from distr.core.workflow.loop_text import GUARDRAILS_FOOTER, SELF_PACE_FOOTER

_SCOPE = (
    "- Stay on the linked ticket scope; avoid unrelated refactors or scope creep\n"
    "- Prefer minimal diffs, explicit evidence, and reversible changes"
)

_DEV_CHECKS = (
    "- Detect commands from the actual project config before running them\n"
    "- Do not assume npm, pytest, or make unless the repo proves it"
)

_PROJECT_SAFETY_NET_COMMAND = r"""set -eu
if [ -f package.json ] && command -v node >/dev/null 2>&1; then
  cmd=$(node -e "const p=require('./package.json').scripts||{}; const cmds=[]; if(p.lint) cmds.push('npm run lint'); if(p.test) cmds.push('npm test'); if(p.build) cmds.push('npm run build'); console.log(cmds.join(' && '));")
  if [ -n "$cmd" ]; then
    echo "$cmd"
    sh -lc "$cmd"
    exit $?
  fi
fi
if [ -f pyproject.toml ] || [ -d tests ]; then
  if command -v pytest >/dev/null 2>&1; then
    pytest -q
  else
    python3 -m pytest -q
  fi
  exit $?
fi
if [ -f Makefile ]; then
  make test
  exit $?
fi
echo "No project safety net command discovered; add lint/test/build command to the ticket plan." >&2
exit 2"""

_SECRET_AUDIT_COMMAND = r"""set -eu
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git worktree; cannot prove tracked secret safety." >&2
  exit 2
fi
tracked_secret_files=$(git ls-files | grep -E '(^|/)(\.env($|\.)|.*\.(pem|key|p12|pfx)$)' || true)
if [ -n "$tracked_secret_files" ]; then
  echo "Secret-like files are tracked:"
  echo "$tracked_secret_files"
  exit 1
fi
hardcoded=$(git grep -n -E '(api[_-]?key|secret|password|token)[[:space:]]*[:=][[:space:]]*"[^"]{12,}"' -- . ':!vendor' ':!node_modules' ':!*.lock' || true)
if [ -n "$hardcoded" ]; then
  echo "Hard-coded secret candidates:"
  echo "$hardcoded"
  exit 1
fi
echo 'Secret and environment audit passed.'"""


def _kickoff(name: str, body: str) -> str:
    return f'Start the "{name}" loop.\n{body.strip()}'


def _guardrail(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def _step(
    name: str,
    instruction: str,
    *,
    skills: list[str],
    tools: list[str],
    other_tool: str = "",
    guardrail: str = "",
    failure_checklist: list[str] | None = None,
    validation_prompt: str = "",
    validation_type: str = "llm_judgment",
    on_pass_goto_position: int | None = None,
    on_fail_goto_position: int | None = None,
    validation_pass_action: str = "",
    validation_fail_action: str = "",
    action_type: str = "",
    wait_for_continue: bool = False,
    command: str = "",
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "instruction": instruction.strip(),
        "skills": skills,
        "tools": tools,
        "guardrail": guardrail or _guardrail(_SCOPE),
        "failure_checklist": failure_checklist
        or ["No concrete evidence attached", "Step skipped or hand-waved without proof"],
        "validation_prompt": validation_prompt or f"Step '{name}' completed with evidence.",
        "validation_type": validation_type,
        "wait_for_continue": wait_for_continue,
    }
    if other_tool:
        out["other_tool"] = other_tool
    if action_type:
        out["action_type"] = action_type
    if command:
        out["command"] = command
    if timeout_seconds is not None:
        out["timeout_seconds"] = timeout_seconds
    if on_pass_goto_position is not None:
        out["on_pass_goto_position"] = on_pass_goto_position
    if on_fail_goto_position is not None:
        out["on_fail_goto_position"] = on_fail_goto_position
    if validation_pass_action:
        out["validation_pass_action"] = validation_pass_action
    if validation_fail_action:
        out["validation_fail_action"] = validation_fail_action
    return out


_COMBINED_GATE_COMMAND = (
    _PROJECT_SAFETY_NET_COMMAND
    + "\necho '--- security audit ---'\n"
    + _SECRET_AUDIT_COMMAND
)


_IDEATION_KICKOFF = _kickoff(
    "Ideation: Brief to Board",
    """Goal: requirements become a product brief, board lanes, and development-ready tickets
Max iterations: 3
Exit when: the brief is written, the board exists with tickets and acceptance criteria, and dev-ready tickets are queued
Step 1: Read the linked requirements document and extract scope, constraints, and delivery slices.
This workflow stays inside the configured CLI harness.
"""
    + SELF_PACE_FOOTER
    + "\n\n"
    + GUARDRAILS_FOOTER,
)

_IDEATION_STEPS = [
    _step(
        "Read requirements document",
        "Read the requirements document path from run metadata. Extract product goal, required views, "
        "interactions, quality bar, and the delivery slices that should become tickets. Summarize scope, "
        "non-goals, and risks. Do not write code or open an IDE.",
        skills=["product-lens", "brainstorming", "doc-coauthoring"],
        tools=["other"],
        other_tool="Requirements document",
        action_type="agent_instruction",
        guardrail=_guardrail("- Ideation only: no code, no Cursor, no project CLI"),
        validation_prompt="Requirements scope, slices, constraints, and non-goals are explicit.",
    ),
    _step(
        "Write product brief",
        "Write a concise product brief with acceptance themes per delivery slice, recommended lane names, "
        "and ticket titles. Attach the brief to run metadata for downstream workflows.",
        skills=["writing-plans", "product-lens", "doc-coauthoring"],
        tools=["other"],
        other_tool="Product brief artifact",
        action_type="agent_instruction",
        guardrail=_guardrail("- Brief must map 1:1 to future board tickets"),
        validation_prompt="Product brief exists with ticket titles and acceptance themes per slice.",
    ),
    _step(
        "Create board, lanes, and tickets",
        "Create a Kanban board with lanes Backlog, Ready, In Progress, Validation, and Complete. "
        "Create one ticket per development slice from the brief with description and acceptance criteria. "
        "Do not hand off to Cursor.",
        skills=["product-lens", "executing-plans"],
        tools=["other"],
        other_tool="Kanban board + ticket creation",
        action_type="agent_instruction",
        guardrail=_guardrail("- Every ticket needs acceptance criteria; no IDE handoff"),
        validation_prompt="Board exists with lanes and development tickets that match the brief.",
    ),
    _step(
        "Queue tickets for development",
        "Link the board and tickets to the development workflow. Set queue order, attach the brief, "
        "and mark tickets ready for the development workflow. End with a handoff summary.",
        skills=["internal-comms", "product-lens"],
        tools=["other"],
        other_tool="Workflow queue + board linkage",
        action_type="agent_instruction",
        validation_prompt="Development tickets are queued on the board with workflow linkage and brief attached.",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

_DEVELOPMENT_KICKOFF = _kickoff(
    "Development: Ticket to Implementation",
    """Goal: one linked ticket is implemented on the project with harness iteration
Max iterations: 6
Exit when: plan.md is attached, the slice is implemented, browser evidence is captured or explicitly marked N/A, checks are green, and development evidence is on the ticket
Step 1: Ingest the ticket, project memory, linked files, and acceptance context before changing code.
"""
    + SELF_PACE_FOOTER
    + "\n\n"
    + GUARDRAILS_FOOTER,
)

_DEVELOPMENT_STEPS = [
    _step(
        "Ingest ticket, memory, and acceptance context",
        "Read the linked ticket, board, lane, project, acceptance criteria, AGENTS.md/context files, active memory, "
        "handoff notes, and linked media/files. Restate the slice, constraints, project route, and unknowns before planning.",
        skills=["product-lens", "brainstorming", "systematic-debugging"],
        tools=["cli", "other"],
        other_tool="Ticket context, project repository, workspace memory, and linked attachments",
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, "- Do not write code before plan.md exists"),
        validation_prompt="Ticket brief, acceptance criteria, linked files/media, memory files, unknowns, and project route are explicit.",
    ),
    _step(
        "Plan the smallest implementation slice",
        "Create ticket-specific plan.md with the smallest shippable slice, affected files, commands to run, browser/evidence plan, "
        "rollback notes, and explicit skip conditions for non-applicable checks. Attach plan.md to the ticket before implementation.",
        skills=["writing-plans", "executing-plans", "doc-coauthoring"],
        tools=["cli", "other"],
        other_tool="Ticket file attachment",
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, "- Plan must match this ticket only"),
        validation_prompt="plan.md exists, is linked to the ticket, and includes implementation slice, checks, browser evidence plan, rollback, and skip rules.",
    ),
    _step(
        "Implement the slice with project checks",
        "Implement only the planned slice. Detect the actual project commands from repo config, run the relevant checks, "
        "capture command output, and report files changed. Do not broaden scope without updating the ticket and plan.",
        skills=["executing-plans", "tdd-workflow", "verification-loop", "systematic-debugging"],
        tools=["cli", "shell"],
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, _DEV_CHECKS),
        validation_prompt="Slice is implemented, relevant project checks were run or explicitly blocked, and changed files are reported.",
        on_fail_goto_position=2,
    ),
    _step(
        "Capture browser evidence and self-assess",
        "If the change affects UI, run the app/browser flow, capture screenshots or Playwright evidence, compare the result against "
        "the ticket, and list visual/behavior issues. If the change is not UI-facing, mark browser evidence N/A with a concrete reason.",
        skills=["qa-tester", "verification-loop", "visual-regression-review", "systematic-debugging"],
        tools=["cli", "playwright", "browser_use"],
        action_type="send_to_project_cli",
        guardrail=_guardrail(
            _SCOPE,
            "- Do not fake browser evidence; if the app cannot run, report the blocker and exact command/output",
            "- Current step remains open until UI evidence is captured or explicitly marked N/A",
        ),
        validation_prompt="Browser evidence is attached for UI changes, or N/A is justified for non-UI changes, with self-assessment notes.",
        on_fail_goto_position=2,
    ),
    _step(
        "Correct, re-run, or skip with reason",
        "Use the evidence from checks and browser review to decide the next move: correct defects and re-run, skip a non-applicable "
        "step with reason, or stop for a human decision. The orchestrator must know why the loop continues or exits.",
        skills=["verification-loop", "systematic-debugging", "executing-plans"],
        tools=["cli", "shell", "playwright"],
        action_type="send_to_project_cli",
        guardrail=_guardrail(
            _SCOPE,
            "- Do not advance on red checks, unresolved visual issues, or missing evidence",
            "- Skipped work needs a concrete reason tied to the ticket scope",
        ),
        validation_prompt="All known defects are corrected or explicitly blocked/skipped with evidence, and rerun results are recorded.",
        on_fail_goto_position=2,
    ),
    _step(
        "Report, update ticket, and compact memory",
        "Update the ticket with files changed, commands run, screenshots/evidence, remaining risks, and the final development summary. "
        "Write the harness return contract so DecisionsAI can persist a compact memory delta for future runs.",
        skills=["internal-comms", "finishing-a-development-branch"],
        tools=["cli", "other"],
        other_tool="Ticket update, result packet, and compact workspace memory",
        action_type="send_to_project_cli",
        validation_prompt="Ticket contains evidence, commands, changed files, risks, and a complete harness return contract for memory compounding.",
        validation_pass_action="end_loop",
        validation_fail_action="retry",
        on_fail_goto_position=4,
    ),
]

_POLISH_KICKOFF = _kickoff(
    "Polish: Verify and Ship",
    """Goal: security, drift, UI regression, and release evidence are green before ship
Max iterations: 4
Exit when: security audit passes, UI evidence is captured, polish tickets are filed if needed, and release notes exist
Step 1: Review what development delivered on the linked board and project.
"""
    + SELF_PACE_FOOTER
    + "\n\n"
    + GUARDRAILS_FOOTER,
)

_POLISH_STEPS = [
    _step(
        "Review delivery context",
        "Read the board, completed development tickets, result packets, and project state. Summarize what shipped, "
        "what remains risky, and what polish checks are required.",
        skills=["product-lens", "qa-tester", "verification-loop"],
        tools=["other"],
        other_tool="Board + result packet review",
        action_type="agent_instruction",
        validation_prompt="Delivery context, risks, and polish checklist are explicit.",
    ),
    _step(
        "Run project and security gates",
        "Run discovered lint/test/build commands and the secret/security audit. Capture exact commands and output.",
        skills=["verification-loop", "ln-622-build-auditor", "ln-621-security-auditor"],
        tools=["cli"],
        action_type="run_command",
        command=_COMBINED_GATE_COMMAND,
        timeout_seconds=720,
        guardrail=_guardrail(_SCOPE, _DEV_CHECKS),
        validation_type="exit_code",
        validation_prompt="Safety nets and secret audit exit 0.",
        on_fail_goto_position=1,
    ),
    _step(
        "Verify UI, drift, and regression",
        "Use Playwright against the app. Check primary flows, responsive layout, console errors, scope drift, "
        "and whether the UI matches the product brief.",
        skills=["webapp-testing", "qa-tester", "browser-qa"],
        tools=["playwright", "browser_use"],
        action_type="playwright",
        guardrail=_guardrail(_SCOPE, "- Reject drift, dead screens, and console blockers"),
        validation_prompt="Browser evidence proves flows work with no console or visual blockers.",
        on_fail_goto_position=1,
    ),
    _step(
        "File polish tickets if needed",
        "If security, drift, or UI issues remain, create polish tickets on the board with acceptance criteria. "
        "If everything is green, state that no polish tickets are required.",
        skills=["internal-comms", "product-lens", "systematic-debugging"],
        tools=["other"],
        other_tool="Kanban ticket creation",
        action_type="agent_instruction",
        validation_prompt="Polish gaps are either filed as tickets or explicitly marked none.",
    ),
    _step(
        "Close with release evidence",
        "Write release notes, attach browser and security evidence to the board or lead ticket, and mark polish complete.",
        skills=["internal-comms", "learnings-keeper"],
        tools=["other"],
        other_tool="Release notes + evidence packet",
        action_type="agent_instruction",
        validation_prompt="Release evidence explains why the Spotify remake is ready to ship.",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
        on_fail_goto_position=1,
    ),
]


def _meta(
    *,
    name: str,
    slug: str,
    role: str,
    category: str,
    archetype: str,
    description: str,
    kickoff: str,
    goal: str,
    exit_when: str,
    check_command: str,
    max_iterations: int,
    steps: list[dict[str, Any]],
    tags: list[str] | None = None,
) -> dict[str, Any]:
    guardrails = [
        line.strip()[2:].strip()
        for line in GUARDRAILS_FOOTER.splitlines()
        if line.strip().startswith("- ")
    ]
    loop_contract = {
        "name": name,
        "goal": goal,
        "exit_when": exit_when,
        "check_command": check_command,
        "max_iterations": max_iterations,
        "guardrails": guardrails,
        "pacing_notes": SELF_PACE_FOOTER,
        "step_1": steps[0]["instruction"] if steps else "",
        "archetype": archetype,
        "role": role,
    }
    return {
        "format_version": "1.0",
        "format": "decisionsai_loop_preset_v1",
        "slug": slug,
        "name": name,
        "role": role,
        "category": category,
        "archetype": archetype,
        "description": description,
        "kickoff": kickoff,
        "loop_contract": loop_contract,
        "expected_check_command": check_command,
        "tags": tags or [],
        "steps": steps,
        "references": {
            "source": "decisionsai_role_presets",
            "parked_presets": "Older role presets remain as unlisted JSON bundles under loop_preset_bundles/bundles.",
        },
    }


LOOP_PRESET_DEFINITIONS: list[dict[str, Any]] = [
    _meta(
        name="Ideation: Brief to Board",
        slug="ideation-brief-to-board",
        role="product_manager",
        category="Product",
        archetype="scope_then_ship",
        description=(
            "Read a requirements document, write a product brief, create a Kanban board with tickets, "
            "and queue development-ready work. Never hands off to Cursor."
        ),
        kickoff=_IDEATION_KICKOFF,
        goal="requirements become a brief, board, tickets, and a development queue",
        exit_when="board exists with ticket acceptance criteria and dev-ready tickets are queued",
        check_command="brief artifact + board ticket count matches requirements slices",
        max_iterations=3,
        steps=_IDEATION_STEPS,
        tags=["ideation", "board", "tickets", "brief"],
    ),
    _meta(
        name="Development: Ticket to Implementation",
        slug="development-ticket-to-implementation",
        role="senior_software_engineer",
        category="Engineering",
        archetype="incremental_ship",
        description=(
            "Ingest a ticket, write plan.md, implement with in-step CLI harness iteration, and close the "
            "development slice. The only workflow that may hand off to Cursor."
        ),
        kickoff=_DEVELOPMENT_KICKOFF,
        goal="linked ticket slice implemented with plan.md and harness evidence",
        exit_when="plan.md is attached, the slice is implemented and self-tested, and the ticket has dev evidence",
        check_command="project lint/test commands from harness self-assessment",
        max_iterations=6,
        steps=_DEVELOPMENT_STEPS,
        tags=["engineering", "ticket", "plan.md", "cursor"],
    ),
    _meta(
        name="Polish: Verify and Ship",
        slug="polish-verify-and-ship",
        role="qa_engineer",
        category="Quality",
        archetype="review_cleanup",
        description=(
            "Review delivery context, run security and project gates, verify UI and drift with Playwright, "
            "file polish tickets if needed, and close with release evidence."
        ),
        kickoff=_POLISH_KICKOFF,
        goal="security, drift, UI regression, and release evidence are green",
        exit_when="security audit passes, UI evidence exists, and release notes explain ship readiness",
        check_command="project safety nets + secret audit + browser UI evidence",
        max_iterations=4,
        steps=_POLISH_STEPS,
        tags=["polish", "security", "playwright", "qa"],
    ),
]


def catalog_entries_from_definitions() -> list[dict[str, Any]]:
    """Shape compatible with loop_catalog.ELORM_LOOP_KICKOFFS."""
    out: list[dict[str, Any]] = []
    for preset in LOOP_PRESET_DEFINITIONS:
        lc = preset.get("loop_contract") or {}
        out.append(
            {
                "name": preset["name"],
                "slug": preset["slug"],
                "category": preset.get("category"),
                "role": preset.get("role"),
                "archetype": preset.get("archetype"),
                "kickoff": preset.get("kickoff"),
                "description": preset.get("description"),
                "expected_check_command": lc.get("check_command") or preset.get("expected_check_command"),
                "expected_max_iterations": lc.get("max_iterations"),
            }
        )
    return out
