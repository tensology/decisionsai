"""
Hand-authored loop preset definitions.

Only one built-in preset is active for now: a Senior Software Engineer role loop
that ingests a ticket, plans it, executes it, validates it, repairs it, and exits
only when the evidence packet is green.
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


_SENIOR_SWE_KICKOFF = _kickoff(
    "Senior Software Engineer: Ticket to Green",
    """Goal: linked ticket is implemented, validated, cleaned up, and evidence-backed
Max iterations: 8
Between iterations run: project safety nets, secret/security audit, UI/product QA, code-health review, and eval scoring
Exit when: plan.md is attached to the ticket, implementation matches acceptance criteria, all checks are green, test data is cleaned up, and the result packet explains why the ticket is green
Step 1: Ingest the ticket, board, lane, project, linked files, acceptance criteria, and prior workflow context before planning.
"""
    + SELF_PACE_FOOTER
    + "\n\n"
    + GUARDRAILS_FOOTER,
)


_SENIOR_SWE_STEPS = [
    _step(
        "Ingest ticket and project context",
        "Read the linked ticket title, description, comments, board, lane, project, todos, linked files, "
        "acceptance criteria, recommended skills, and any prior result-packet context. Restate the problem, "
        "constraints, risks, missing information, and the project route that should own the work.",
        skills=["product-lens", "brainstorming", "systematic-debugging"],
        tools=["cli", "other"],
        other_tool="Kanban ticket context + project repository",
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, "- Do not write code before the plan artifact exists"),
        failure_checklist=[
            "Ticket acceptance criteria were not restated",
            "Project route or linked project context was not identified",
            "Prior result-packet context was ignored",
        ],
        validation_prompt="Ticket brief, acceptance criteria, risks, project route, and context handoff are explicit.",
    ),
    _step(
        "Write plan.md and attach to ticket",
        "Create or update a ticket-specific plan.md artifact in the project. Include requirements, implementation "
        "slices, affected files, tests to write before shipping, security checks, UI checks, rollback plan, data "
        "cleanup plan, and eval scoring criteria. Attach or link plan.md back to the ticket before implementation.",
        skills=["writing-plans", "executing-plans", "doc-coauthoring", "product-lens"],
        tools=["cli", "other"],
        other_tool="Ticket file attachment / ticket update",
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, "- The plan must be tied to this ticket, not a generic checklist"),
        failure_checklist=[
            "plan.md missing or not linked to ticket",
            "Plan lacks tests, rollback, cleanup, security, UI, or eval criteria",
            "Plan omits context transferred from the ticket",
        ],
        validation_prompt="plan.md exists, is linked or attached to the ticket, and includes tests, rollback, cleanup, security, UI, and eval criteria.",
    ),
    _step(
        "Execute planned slice",
        "Implement the next planned slice using the project conventions. Write or update tests before shipping the "
        "behavior. Keep the diff tight, preserve existing patterns, and record what context is handed to validation.",
        skills=["executing-plans", "tdd-workflow", "verification-loop", "systematic-debugging"],
        tools=["cli"],
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, _DEV_CHECKS, "- Do not bypass tests or weaken acceptance criteria"),
        failure_checklist=[
            "No test or eval coverage for changed behavior",
            "Implementation drifts outside the ticket plan",
            "Context handoff to later steps is missing",
        ],
        validation_prompt="Planned slice is implemented with focused tests or evals and a clear context handoff.",
        on_fail_goto_position=2,
    ),
    _step(
        "Run project safety nets",
        "Run the discovered project lint, test, and build commands. Capture the exact command and output. "
        "If a command fails, route back to the implementation step with the first concrete failure.",
        skills=["verification-loop", "ln-622-build-auditor", "systematic-debugging"],
        tools=["cli"],
        action_type="run_command",
        command=_PROJECT_SAFETY_NET_COMMAND,
        timeout_seconds=600,
        guardrail=_guardrail(_SCOPE, _DEV_CHECKS, "- Do not edit commands, tests, or configs just to force green"),
        failure_checklist=[
            "Safety net command was skipped",
            "A failing command was ignored",
            "The command did not run in the linked project folder",
        ],
        validation_type="exit_code",
        validation_prompt="Discovered lint/test/build safety nets exit 0 in the linked project folder.",
        on_fail_goto_position=2,
    ),
    _step(
        "Audit secrets and security",
        "Run tracked-secret and hard-coded-secret checks, then review the changed code for injection risks, unsafe "
        "deserialization, authz/authn drift, weak error handling, and rollback hazards.",
        skills=["ln-621-security-auditor", "pre-flight-review", "systematic-debugging"],
        tools=["cli"],
        action_type="run_command",
        command=_SECRET_AUDIT_COMMAND,
        timeout_seconds=180,
        guardrail=_guardrail(
            _SCOPE,
            "- Never print secret values into the ticket or activity log",
            "- Do not commit .env files, API keys, tokens, passwords, private keys, or generated credentials",
        ),
        failure_checklist=[
            "Tracked .env or key material found",
            "Hard-coded secret candidate found",
            "SQL injection or unsafe input path not addressed",
        ],
        validation_type="exit_code",
        validation_prompt="Secret/env scan exits 0 and no unresolved security blocker remains.",
        on_fail_goto_position=2,
    ),
    _step(
        "Verify UI and product behavior",
        "Use Playwright/browser-use against the affected local or staging UI. Check changed flows, all visible buttons "
        "introduced or touched, loading states, snackbars/toasts, empty/error states, responsive layout, console errors, "
        "and whether the UI logically matches the ticket and project patterns.",
        skills=["webapp-testing", "qa-tester", "browser-qa"],
        tools=["playwright", "browser_use"],
        action_type="playwright",
        guardrail=_guardrail(_SCOPE, "- Do not accept hidden required actions, clipped text, or rogue UI controls"),
        failure_checklist=[
            "Changed flow not exercised in browser",
            "Button, snackbar, spinner, error, or loading state missing",
            "Console errors, visual drift, or nonsensical UI ignored",
        ],
        validation_prompt="Browser evidence proves the changed UI flow makes sense, works, and has no console or visual blockers.",
        on_fail_goto_position=2,
    ),
    _step(
        "Capture desktop evidence when needed",
        "Use computer-use for desktop/sidecar evidence when the workflow involves native UI, browser chrome, file pickers, "
        "screen-level state, or anything Playwright cannot see. Capture screenshot evidence and summarize what was verified.",
        skills=["qa-tester", "browser-qa"],
        tools=["computer_use"],
        action_type="computer_use",
        guardrail=_guardrail(_SCOPE, "- Use this as evidence capture, not as a substitute for deterministic checks"),
        failure_checklist=[
            "Computer-use evidence needed but not captured",
            "Screenshot does not show the relevant state",
            "Observed UI state contradicts browser or test evidence",
        ],
        validation_prompt="Desktop/screenshot evidence is captured when needed, or the step states why Playwright evidence was sufficient.",
        on_fail_goto_position=2,
    ),
    _step(
        "Review backend and code health",
        "Review the changed backend and shared code for dead code, duplicate logic, excessive complexity, files drifting "
        "toward 1600+ lines, leaky abstractions, inefficient queries, unclear errors, and missing rollback paths. Refactor "
        "only when it directly supports the ticket and reduces risk.",
        skills=["requesting-code-review", "ln-511-code-quality-checker", "ln-512-tech-debt-cleaner"],
        tools=["cli"],
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, "- No drive-by rewrites; refactor only the code touched or directly implicated"),
        failure_checklist=[
            "Dead code or duplicate logic left behind",
            "Large-file or complexity drift ignored",
            "Backend errors are vague or unsafe",
        ],
        validation_prompt="Code-health review is complete with dead code, duplication, complexity, and backend drift addressed or explicitly deferred.",
        on_fail_goto_position=2,
    ),
    _step(
        "Run eval and regression criteria",
        "Create or run evaluation-based tests where assertions alone are insufficient. Score criteria such as accuracy, "
        "tone, schema compliance, safety, and useful error messaging. Verify regressions called out in plan.md.",
        skills=["qa-tester", "verification-loop", "product-lens"],
        tools=["cli", "other"],
        other_tool="Evaluation harness / orchestrator scoring",
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, "- Eval scores must reflect the ticket acceptance criteria, not vanity metrics"),
        failure_checklist=[
            "Eval criteria missing for model/content/schema behavior",
            "Accuracy, tone, schema, safety, or error messaging not scored where relevant",
            "Potential regressions not tested",
        ],
        validation_prompt="Relevant evals and regressions are scored with pass/fail rationale tied to plan.md.",
        on_fail_goto_position=2,
    ),
    _step(
        "Cleanup test data and rollback proof",
        "Remove temporary test data, generated fixtures, debug artifacts, and workflow noise created by this run. Prove "
        "rollback steps or migration safety where applicable. Confirm tests and evals leave no persistent data behind.",
        skills=["verification-loop", "finishing-a-development-branch", "ln-512-tech-debt-cleaner"],
        tools=["cli"],
        action_type="send_to_project_cli",
        guardrail=_guardrail(_SCOPE, "- Do not leave seeded tickets, workflows, temp files, or credentials behind"),
        failure_checklist=[
            "Test data or generated artifacts left behind",
            "Rollback plan untested or missing",
            "Migration/fixture cleanup not verified",
        ],
        validation_prompt="Cleanup is verified, rollback notes exist where needed, and no persistent test data is left behind.",
        on_fail_goto_position=2,
    ),
    _step(
        "Evaluate green exit",
        "Decide whether the loop can exit. It can only pass when plan.md is attached, acceptance criteria are met, safety "
        "nets passed, security is clean, UI/product checks passed or were not applicable with rationale, backend/code "
        "health is acceptable, evals passed, cleanup is verified, and context handoffs are visible.",
        skills=["product-lens", "finishing-a-development-branch", "requesting-code-review"],
        tools=["other"],
        other_tool="Orchestrator exit judgment",
        action_type="agent_instruction",
        guardrail=_guardrail(_SCOPE, "- Do not mark green with unresolved blockers or missing evidence"),
        failure_checklist=[
            "Exit declared with missing plan.md attachment",
            "Exit declared with failing tests, security, UI, eval, or cleanup gate",
            "Final result packet does not explain why the run is green",
        ],
        validation_prompt="All gates are green and the result packet explains the evidence for exit.",
        on_fail_goto_position=2,
    ),
    _step(
        "Attach evidence and close ticket loop",
        "Update the ticket with plan.md, changed files, commands run, browser/computer-use evidence, security notes, eval "
        "scores, cleanup proof, rollback notes, and the final result packet. Mark the workflow completed only after this "
        "ticket-facing summary is written.",
        skills=["internal-comms", "learnings-keeper", "finishing-a-development-branch"],
        tools=["other"],
        other_tool="Ticket update + result packet",
        action_type="agent_instruction",
        guardrail=_guardrail(_SCOPE, "- The final summary must be useful to the next developer who opens the ticket"),
        failure_checklist=[
            "Ticket was not updated with evidence",
            "Context handoff to future work is missing",
            "Status changed without a final result packet",
        ],
        validation_prompt="Ticket contains the final evidence summary, result packet, and closure status.",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
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
        name="Senior Software Engineer: Ticket to Green",
        slug="senior-software-engineer-ticket-to-green",
        role="senior_software_engineer",
        category="Engineering",
        archetype="incremental_ship",
        description=(
            "Ingest a ticket, attach plan.md, implement, run project safety nets, security checks, "
            "UI/product QA, code-health review, eval scoring, cleanup, and loop until green."
        ),
        kickoff=_SENIOR_SWE_KICKOFF,
        goal="linked ticket implemented, validated, cleaned up, and evidence-backed",
        exit_when=(
            "plan.md is attached to the ticket, acceptance criteria are met, safety nets pass, "
            "security/UI/backend/eval/cleanup gates are green, and the result packet explains why"
        ),
        check_command=(
            "project safety net discovery command + secret/security audit + browser/computer-use evidence + "
            "code-health/eval/cleanup gates"
        ),
        max_iterations=8,
        steps=_SENIOR_SWE_STEPS,
        tags=["engineering", "ticket", "plan.md", "security", "playwright", "computer-use"],
    )
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
