"""
Hand-authored loop preset definitions.

The active catalog intentionally exposes one dependable developer workflow.
Provider/model selection belongs to Auto routing, not to workflow step names.
"""

from __future__ import annotations

from typing import Any

from distr.core.workflow.developer_workflow import (
    DEVELOPER_WORKFLOW_NAME,
    DEVELOPER_WORKFLOW_RUN_SETTINGS,
    DEVELOPER_WORKFLOW_SLUG,
)
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
checks_run=0
if [ -f package.json ] && command -v node >/dev/null 2>&1; then
  cmd=$(node -e "const p=require('./package.json').scripts||{}; const cmds=[]; if(p.lint) cmds.push('npm run lint'); if(p.test) cmds.push('npm test'); if(p.build) cmds.push('npm run build'); console.log(cmds.join(' && '));")
  if [ -n "$cmd" ]; then
    echo "$cmd"
    sh -lc "$cmd"
    checks_run=1
  fi
fi
if [ -f pyproject.toml ] || [ -d tests ]; then
  if command -v pytest >/dev/null 2>&1; then
    pytest -q
  else
    python3 -m pytest -q
  fi
  checks_run=1
fi
if [ -f Makefile ]; then
  make test
  checks_run=1
fi
if [ "$checks_run" -eq 0 ]; then
  echo "No project safety net command discovered; add lint/test/build command to the ticket plan." >&2
  exit 2
fi"""

_SECRET_AUDIT_COMMAND = r"""set -eu
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git worktree; cannot prove workspace secret safety." >&2
  exit 2
fi
secret_files=$(git ls-files --cached --others --exclude-standard | grep -E '(^|/)(\.env($|\.(local|prod|production|staging)$)|.*\.(pem|key|p12|pfx)$)' || true)
if [ -n "$secret_files" ]; then
  echo "Secret-like workspace files require removal or explicit sanitisation:"
  echo "$secret_files"
  exit 1
fi
hardcoded=$(rg -l --hidden --glob '!.git/**' --glob '!vendor/**' --glob '!node_modules/**' --glob '!.venv/**' --glob '!*.lock' --pcre2 "(?i)(api[_-]?key|secret(?:_key)?|password|token|client_secret|private_key)[[:space:]]*[:=][[:space:]]*['\"][^'\"]{8,}['\"]" . || true)
if [ -n "$hardcoded" ]; then
  echo "Hard-coded secret candidates found in these files (values suppressed):"
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
    max_retries: int = 0,
    config: dict[str, Any] | None = None,
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
    if max_retries:
        out["max_retries"] = max(0, int(max_retries))
    if config:
        out["config"] = config
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
    """Goal: requirements become a product/project container, a product board, scoped tickets, and a development queue
Max iterations: 3
Exit when: the brief is written, the project/board names are product names, tickets carry the implementation scope, and dev-ready tickets are queued.
Step 1: Read the linked requirements document and extract product identity, constraints, and delivery slices.
This workflow stays inside the configured CLI harness.
"""
    + SELF_PACE_FOOTER
    + "\n\n"
    + GUARDRAILS_FOOTER,
)

_IDEATION_STEPS = [
    _step(
        "Read requirements document",
        "Read the requirements document path from run metadata. Extract product name, domain/repo hint if present, product goal, "
        "required views, interactions, quality bar, delivery slices, non-goals, and risks. The project and board names must stay "
        "as product/container names; implementation details belong in ticket titles and descriptions. Do not write code.",
        skills=["product-lens", "brainstorming", "doc-coauthoring"],
        tools=["other"],
        other_tool="Requirements document",
        action_type="agent_instruction",
        guardrail=_guardrail(
            "- Ideation only: no code, no Cursor, no project CLI",
            "- Do not put implementation goals into the project or board name",
        ),
        failure_checklist=[
            "Product/container identity is missing",
            "Delivery slices are not separated from project/board naming",
            "Non-goals or risks are missing",
        ],
        validation_prompt="Product identity, domain/repo hint, slices, constraints, non-goals, and risks are explicit.",
        config={
            "execution_scope": "product_discovery",
            "expected_outputs": ["product_identity", "delivery_slices", "non_goals", "risks"],
        },
    ),
    _step(
        "Write product brief",
        "Write a concise product brief with product/container naming, acceptance themes per delivery slice, recommended lanes, "
        "ticket titles, and ticket descriptions. The brief must clearly separate the product/board name from implementation work.",
        skills=["writing-plans", "product-lens", "doc-coauthoring"],
        tools=["other"],
        other_tool="Product brief artifact",
        action_type="agent_instruction",
        guardrail=_guardrail(
            "- Brief must map 1:1 to future board tickets",
            "- Board name should be the product/project name, not the first ticket",
        ),
        failure_checklist=[
            "Brief does not name the product/container cleanly",
            "Ticket slices do not map 1:1 to acceptance themes",
            "Board/lane recommendations are missing",
        ],
        validation_prompt="Product brief exists with clean product/board naming, ticket titles, descriptions, and acceptance themes per slice.",
        config={
            "execution_scope": "product_brief",
            "expected_outputs": ["product_brief", "ticket_blueprint", "lane_plan"],
        },
    ),
    _step(
        "Create board, lanes, and tickets",
        "Create or update one product project and one product board using the product name. Create exactly the canonical "
        "delivery lanes Backlog, In Progress, QA, and Complete. Create one ticket per development slice from the brief with description, "
        "acceptance criteria, priority, complexity, and project/workflow linkage. Do not duplicate boards or tickets if matching "
        "records already exist. Automation may move tickets only from Backlog to In Progress and then to QA; only a human may "
        "move a QA ticket to Complete.",
        skills=["product-lens", "executing-plans"],
        tools=["other"],
        other_tool="Kanban board + ticket creation",
        action_type="agent_instruction",
        guardrail=_guardrail(
            "- Every ticket needs acceptance criteria; no IDE handoff",
            "- One product should have one product board unless the user explicitly asks for another",
            "- Reuse or update matching records instead of creating residual duplicates",
        ),
        failure_checklist=[
            "Project or board name includes implementation scope",
            "Duplicate board/tickets were created for the same product",
            "Tickets lack acceptance criteria, priority, complexity, or workflow linkage",
        ],
        validation_prompt="One product project and board exist with lanes and scoped development tickets that match the brief.",
        config={
            "execution_scope": "board_creation",
            "expected_outputs": ["project_id", "board_id", "lane_ids", "ticket_ids"],
            "idempotency": "reuse_matching_product_records",
        },
    ),
    _step(
        "Queue tickets for development",
        "Link the board and development-ready tickets to the Development workflow. Set queue order "
        "from infrastructure/foundation first through feature slices and validation. Attach the brief/result packet and end with "
        "a concise handoff summary that names the first ticket to run.",
        skills=["internal-comms", "product-lens"],
        tools=["other"],
        other_tool="Workflow queue + board linkage",
        action_type="agent_instruction",
        failure_checklist=[
            "Tickets are not linked to the development workflow",
            "Queue order does not put foundation/infrastructure first",
            "Handoff summary does not identify the first runnable ticket",
        ],
        validation_prompt="Development tickets are queued on the product board with workflow linkage, brief attached, and first runnable ticket identified.",
        config={
            "execution_scope": "queue_setup",
            "expected_outputs": ["queued_ticket_ids", "first_ticket_id", "handoff_summary"],
        },
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

_DEVELOPMENT_KICKOFF = _kickoff(
    DEVELOPER_WORKFLOW_NAME,
    """Goal: one linked ticket is implemented on the linked project with CLI harness iteration
Max iterations: 6
Exit when: the ticket execution contract is confirmed, the scoped slice is implemented, relevant checks are green or explicitly blocked, browser evidence exists or is marked N/A with reason, and the ticket has a compact result packet.
Step 1: Load the ticket, board, project, workflow memory, AGENTS.md/context files, linked files/media, and acceptance criteria before changing code.
"""
    + SELF_PACE_FOOTER
    + "\n\n"
    + GUARDRAILS_FOOTER,
)

_DEVELOPMENT_STEPS = [
    _step(
        "Understand ticket and acceptance criteria",
        "Load the exact linked ticket and project before doing any implementation work. Read the board/lane, ticket title, "
        "description, acceptance criteria, linked media/files, AGENTS.md, project memory, workflow memory, and the current "
        "repository shape. Return a concise context packet with: ticket scope, non-goals, project folder, likely stack, "
        "required files to inspect, missing information, and the proposed CLI/model route. For UI-bearing work, also infer a one-line "
        "design read from the audience, brand/reference assets, existing interface, intended user journey, and appropriate visual density; "
        "do not substitute a generic AI aesthetic. Prefer existing result packets, project documentation/evidence indexes, and high-signal manifests/configuration over a fresh "
        "whole-repository scan. Stay within the configured focused inspection budget (10 calls for low, 18 for medium, 30 for high-complexity tickets), never enumerate dependency, virtual-environment, cache, "
        "database, media, or generated trees, and stop inspecting as soon as every expected output is supported. Inspect existing docs/evidence before claiming there are no prior artifacts; for a routed UI app, inspect its route entrypoint before claiming a screen or homepage is absent. The handoff already contains the ticket and summarized memory: do not reread the same path, invent sibling paths, or inspect old agent/session iteration history. When the ticket supplies public source URLs, record them as implementation inputs; do not fetch or research them during this context-only phase. Do not edit files in this step.",
        skills=["product-lens", "brainstorming", "systematic-debugging"],
        tools=["cli", "other"],
        other_tool="Ticket context, board, project repository, workspace memory, and linked attachments",
        action_type="send_to_project_cli",
        timeout_seconds=600,
        max_retries=1,
        guardrail=_guardrail(
            _SCOPE,
            "- Do not write code before the ticket execution contract is confirmed",
            "- For copy-first work, inventory source files without copying credentials, .env files, local settings, databases, media, logs, caches, or generated/user data",
            "- If linked files/media cannot be fetched, record the missing attachment and continue only with an explicit blocker note",
            "- The project and board are containers; implementation intent belongs to the ticket",
            "- Planning inspection uses the configured complexity-aware ceiling (10 low, 18 medium, 30 high); do not recursively inspect dependency, virtual-environment, cache, database, media, or generated trees",
        ),
        failure_checklist=[
            "Ticket scope, non-goals, or acceptance criteria are missing",
            "Project folder or repository shape was not identified",
            "Linked files/media or memory files were not checked or marked unavailable",
            "The agent started editing before the context packet was produced",
        ],
        validation_prompt="A context packet exists with ticket scope, non-goals, project route, linked files/media status, memory status, unknowns, and CLI/model route.",
        config={
            "execution_scope": "ticket",
            "step_role": "planning",
            "read_only": True,
            "model_policy": {"mode": "auto", "free_only": True, "prefer_local": True},
            "inspection_budget": {
                "max_tool_calls": 18,
                "max_files": 24,
                "max_tool_calls_by_complexity": {"low": 10, "medium": 18, "high": 30},
            },
            "required_context": ["ticket", "board", "project", "workflow_memory", "project_memory", "linked_attachments"],
            "expected_outputs": ["context_packet", "unknowns", "route_recommendation", "ui_design_read_if_applicable"],
        },
    ),
    _step(
        "Confirm ticket execution contract",
        "Treat the selected ticket and its acceptance criteria as the implementation plan. Confirm the smallest shippable "
        "slice, upstream ticket dependencies, existing artifacts to reuse, files expected to change, discovered commands, "
        "browser/evidence path, rollback notes, and ticket-specific skip rules. For UI-bearing work, define the primary user flow, "
        "observable visual claims, required desktop/mobile viewports, interaction states, brand/design tokens to preserve, and explicit "
        "anti-bloat constraints. Return this as a compact execution contract "
        "in the workflow handoff; do not create plan.md or another parallel project plan. If the ticket is not executable, "
        "pause with one precise missing decision instead of producing more planning documents. The complete ticket and upstream planning "
        "fields are already attached. Synthesize them directly: do not call tools, reread workspace files, or rescan the project.",
        skills=["writing-plans", "executing-plans", "doc-coauthoring"],
        tools=["cli", "other"],
        other_tool="Ticket description, dependencies, acceptance criteria, and prior ticket artifacts",
        action_type="send_to_project_cli",
        timeout_seconds=600,
        max_retries=1,
        guardrail=_guardrail(
            _SCOPE,
            "- The ticket group is the delivery plan; do not create a second plan hierarchy",
            "- The execution contract must match this ticket only and name its upstream dependencies",
            "- Reuse completed upstream artifacts instead of repeating their work",
            "- A copy manifest must distinguish reusable source code from excluded secrets, local configuration, databases, media, logs, caches, generated output, and user/product data",
            "- If the ticket is too vague, ask for the missing decision rather than writing a speculative plan",
            "- Contract confirmation is a tool-free synthesis of the supplied ticket and upstream planning fields; do not repeat inspection",
        ),
        failure_checklist=[
            "A duplicate plan.md or parallel project plan was created",
            "Execution contract is broader than the ticket",
            "Upstream dependencies or reusable artifacts were not checked",
            "Expected files, checks, browser evidence, rollback, or skip rules are missing",
        ],
        validation_prompt="A compact execution contract exists in the run handoff, treats the ticket as the plan, confirms dependencies and reusable artifacts, and includes expected files, checks, evidence, rollback, and skip rules without creating plan.md.",
        config={
            "execution_scope": "ticket",
            "step_role": "planning",
            "read_only": True,
            "disable_tools": True,
            "review_mode": "independent",
            "model_policy": {"auto_route_models": True},
            "required_context": ["context_packet", "unknowns", "route_recommendation", "ui_design_read_if_applicable"],
            "expected_outputs": ["execution_contract", "dependency_status", "reusable_artifacts", "copy_manifest_if_applicable", "ui_acceptance_contract_if_applicable"],
        },
    ),
    _step(
        "Implement the planned change",
        "Implement only the planned slice. Detect stack and commands from real repo files such as package.json, pyproject.toml, "
        "requirements files, manage.py, Makefile, docker compose files, or existing scripts. Make minimal edits, run the relevant "
        "checks that are available, capture exact command output, and report changed files. If setup requires dependency install "
        "or network access, report the blocked command clearly instead of pretending checks passed. Consume every field in the upstream "
        "execution contract before inspecting the repository. Reuse each named existing artifact and evidence path; validate or reference "
        "it instead of recreating it. For UI work, implement the declared "
        "design read and user flow with coherent hierarchy, spacing, density, controls, responsive states, loading/empty/error states, "
        "accessible contrast/focus, and motivated motion. Reject generic card/pill/gradient clutter and controls that look out of place.",
        skills=["executing-plans", "tdd-workflow", "verification-loop", "systematic-debugging"],
        tools=["cli", "shell"],
        action_type="send_to_project_cli",
        timeout_seconds=1800,
        guardrail=_guardrail(
            _SCOPE,
            _DEV_CHECKS,
            "- Do not create duplicate app roots if the project already has frontend/backend folders",
            "- Do not install global dependencies; use project-local tooling or a local virtual environment",
            "- Never install a dependency or browser tool solely to recreate an upstream artifact that already exists; validate and reference the existing artifact instead",
            "- When the handoff names an existing artifact, inspect that path first and create a replacement only when the ticket explicitly requires regeneration or validation proves it unusable",
            "- Never copy live credentials, .env files, local settings, databases, media, logs, caches, generated/user data, or source-project identity; create environment-backed destination configuration instead",
            "- Never create a default administrative password; report the secure operator setup command without embedding credentials",
        ),
        failure_checklist=[
            "Implementation deviated from the ticket execution contract",
            "Project commands were guessed instead of detected",
            "Checks were skipped without exact blocker output",
            "Changed files were not reported",
            "A duplicate project scaffold was created instead of using the existing folder",
        ],
        validation_prompt="The planned slice is implemented, project commands were detected from repo files, checks ran or have exact blockers, and changed files are reported.",
        on_fail_goto_position=2,
        config={
            "execution_scope": "ticket",
            "step_role": "implementation",
            "model_policy": {"auto_route_models": True},
            "required_context": [
                "execution_contract",
                "dependency_status",
                "reusable_artifacts",
                "copy_manifest_if_applicable",
                "ui_acceptance_contract_if_applicable",
            ],
            "expected_outputs": ["changed_files", "command_log", "security_sanitisation", "blockers", "ui_flow_evidence_if_applicable"],
        },
    ),
    _step(
        "Independently review and validate the change",
        "Review the implementation independently from the implementation model. Inspect the diff for correctness, regressions, "
        "security, maintainability, dead code, and acceptance-criteria coverage. Run the repository's discovered lint, test, and build "
        "commands. For UI work, start the app and use Playwright or the browser tool to capture screenshot evidence at the contract's "
        "desktop and mobile viewports. Verify each visible claim separately, walk the primary flow, inspect console/runtime errors, and "
        "critique information hierarchy, spacing/density, copy, control placement, state behavior, accessibility, visual consistency, "
        "brand fit, and obvious AI-template bloat. 'Looks good' is not evidence. For non-UI work, record why browser validation is not "
        "applicable. Classify every finding as either a ticket blocker or a wider project-release finding. The ship_verdict applies "
        "only to the linked ticket: a pre-existing or out-of-scope defect must be preserved as a project_release_finding and must not "
        "fail this ticket unless the ticket introduced it, modified the affected surface, or cannot satisfy acceptance safely without it. "
        "Do not edit files in this review step.",
        skills=["qa-tester", "verification-loop", "browser-qa", "systematic-debugging", "requesting-code-review"],
        tools=["cli", "shell", "playwright", "browser_use"],
        action_type="send_to_project_cli",
        timeout_seconds=1200,
        guardrail=_guardrail(
            _SCOPE,
            _DEV_CHECKS,
            "- Use a provider/model independent from implementation when a ready alternative exists",
            "- Do not edit files during independent review; return findings to the correction step",
            "- Do not fake browser evidence; report exact blockers and command output",
            "- Inspect both tracked and untracked files for copied credentials, local settings, databases, source identity, user data, and unsafe default accounts",
            "- Separate ticket blockers from pre-existing or out-of-scope project release findings; never trap an unrelated ticket in a correction loop",
        ),
        failure_checklist=[
            "Review used the same provider as implementation without explaining why",
            "Repository checks were not discovered and run",
            "Acceptance criteria were not checked against the diff",
            "UI work lacks desktop/mobile screenshot evidence, observable claim verdicts, or a concrete blocker",
            "UI review ignores hierarchy, density, task flow, states, accessibility, brand fit, or template-like bloat",
            "Findings do not have severity and actionable evidence",
        ],
        validation_prompt="Independent review reports no unresolved ticket-blocking findings, repository checks relevant to this ticket pass, acceptance criteria are covered, and UI evidence exists or is explicitly N/A with reason. Wider project-release findings remain visible but do not change the ticket verdict unless causally related.",
        on_fail_goto_position=4,
        config={
            "execution_scope": "ticket",
            "step_role": "review",
            "read_only": True,
            "model_policy": {"auto_route_models": True, "independent_from": "implementation"},
            "required_context": ["execution_contract", "changed_files", "command_log", "acceptance_criteria"],
            "expected_outputs": ["review_findings", "project_release_findings", "check_results", "security_audit", "browser_evidence", "visual_claim_verdicts", "ship_verdict"],
        },
        on_pass_goto_position=5,
    ),
    _step(
        "Correct defects found by validation",
        "Use the independent review, command output, and browser evidence to correct ticket-blocking defects only. Preserve wider "
        "project-release findings for the result packet or their existing linked ticket; do not edit unrelated files to clear them. Re-run the relevant "
        "checks, skip only non-applicable checks with a ticket-specific reason, or stop for a human decision when the blocker needs "
        "product or credential input. For UI findings, correct the rendered experience and repeat the same visual claims at the same "
        "viewports so before/after evidence is comparable. The result must say whether the loop should continue, retry implementation, or exit.",
        skills=["verification-loop", "systematic-debugging", "executing-plans"],
        tools=["cli", "shell", "playwright"],
        action_type="send_to_project_cli",
        timeout_seconds=1200,
        guardrail=_guardrail(
            _SCOPE,
            "- Do not advance on red checks, unresolved visual issues, or missing evidence",
            "- Skipped work needs a concrete reason tied to the ticket scope",
        ),
        failure_checklist=[
            "Known defect was left unresolved without blocker",
            "Checks or browser evidence were not rerun after correction",
            "Skip reason is generic or not tied to the ticket",
            "Next loop action is unclear",
        ],
        validation_prompt="All known defects are corrected or explicitly blocked/skipped with evidence, rerun results are recorded, and the next loop action is explicit.",
        on_pass_goto_position=3,
        on_fail_goto_position=4,
        config={
            "execution_scope": "ticket",
            "step_role": "implementation",
            "model_policy": {"auto_route_models": True},
            "required_context": ["review_findings", "check_results", "browser_evidence"],
            "expected_outputs": ["rerun_results", "skip_or_blocker_reason", "next_action"],
        },
    ),
    _step(
        "Final production polish and ship audit",
        "Use Codex as the strongest final production pass after implementation and independent review. Read the ticket execution contract, the diff, "
        "review findings, correction evidence, and the exact acceptance criteria. Fix only remaining release-blocking defects, "
        "then run the discovered project checks and the required Playwright/browser path for UI work. For every UI-bearing ticket, inspect "
        "the rendered desktop and mobile experience as a senior product designer: the task flow must be immediately understandable; hierarchy, "
        "spacing and density must feel deliberate; buttons and controls must be simple, consistent, well placed and fully stateful; copy must be "
        "clear; brand/reference assets must drive the aesthetic; and no generic AI cards, pills, gradients, excess whitespace, visual noise, clipped "
        "content, awkward responsiveness or unmotivated motion may remain. Confirm that no placeholder content, debug residue, dead code, broken "
        "navigation, console errors, missing responsive states, or unverified checkout/auth paths remain. Produce a concise ship verdict with "
        "before/after screenshot evidence and per-claim verdicts; do not broaden the ticket or redesign accepted work.",
        skills=["verification-loop", "finishing-a-development-branch", "browser-qa", "requesting-code-review"],
        tools=["cli", "shell", "playwright", "browser_use"],
        action_type="send_to_project_cli",
        timeout_seconds=1800,
        guardrail=_guardrail(
            _SCOPE,
            _DEV_CHECKS,
            "- This is a production polish pass, not a new implementation scope",
            "- Fix release blockers and regressions only; preserve accepted design and behavior",
            "- Never claim ship-ready without exact test/browser evidence",
            "- Re-run the tracked-and-untracked secret audit and reject copied credentials, local settings, databases, source identity, user data, or unsafe default accounts",
        ),
        failure_checklist=[
            "Release-blocking review finding remains unresolved",
            "UI work lacks final desktop/mobile screenshot evidence and per-claim verdicts",
            "Rendered UI remains confusing, bloated, visually inconsistent, inaccessible, off-brand, or template-like",
            "Console/runtime errors or broken critical flows remain",
            "Ship verdict does not map evidence to acceptance criteria",
        ],
        validation_prompt="Codex final polish resolves release blockers, project and browser checks pass, and the ship verdict maps evidence to every acceptance criterion.",
        on_pass_goto_position=6,
        on_fail_goto_position=4,
        config={
            "execution_scope": "ticket",
            "step_role": "final_polish",
            "model_policy": {"auto_route_models": True, "preferred_backend": "codex"},
            "required_context": ["execution_contract", "changed_files", "review_findings", "rerun_results", "acceptance_criteria"],
            "expected_outputs": ["final_fixes", "final_check_results", "final_security_audit", "final_browser_evidence", "visual_quality_verdict", "ship_verdict"],
        },
    ),
    _step(
        "Report, update ticket, and compact memory",
        "Return a compact result packet for the system to persist on the ticket: summary, files changed, commands run, browser "
        "evidence or N/A reason, remaining risks, blockers, and next actions. Treat the accepted implementation, review, and final "
        "polish results in the handoff as authoritative; this is a reporting transformation, not another validation pass. Do not "
        "re-open evidence, run project tools, or create/edit report or handoff files in the project. Write only durable memory "
        "deltas: project commands, conventions, paths, decisions, failed approaches, root causes, corrections, and learned rules. "
        "Do not paste full transcripts into memory. Return the exact named fields ticket_result_packet, compact_memory_delta, "
        "failed_attempts, and lessons in the response. Keep the user-facing summary natural and omit internal ticket/run/step IDs "
        "and dates unless the user explicitly asks for them.",
        skills=["internal-comms", "finishing-a-development-branch"],
        tools=["cli", "other"],
        other_tool="Ticket update, result packet, and compact workspace memory",
        action_type="send_to_project_cli",
        timeout_seconds=600,
        guardrail=_guardrail(
            _SCOPE,
            "- Do not paste full transcripts into memory; compact durable facts only",
            "- Ticket result must be usable by the next run without reading the full chat",
            "- Do not re-run validation or inspect evidence; summarize the accepted upstream result",
            "- Do not create or edit project files during reporting; return the packet in the model response only",
        ),
        failure_checklist=[
            "Ticket lacks final result packet",
            "Changed files or commands are missing",
            "Evidence or N/A reason is missing",
            "Memory delta contains transcript noise instead of durable facts",
        ],
        validation_prompt="Ticket contains summary, evidence, commands, changed files, risks/blockers, next actions, and compact memory deltas for future runs.",
        validation_pass_action="end_loop",
        validation_fail_action="retry",
        on_fail_goto_position=4,
        config={
            "execution_scope": "ticket",
            "step_role": "reporting",
            "read_only": True,
            "model_policy": {"auto_route_models": True},
            "required_context": ["final_changed_files", "command_log", "evidence", "memory_delta"],
            "expected_outputs": [
                "ticket_result_packet",
                "compact_memory_delta",
                "failed_attempts",
                "lessons",
            ],
        },
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
    run_settings: dict[str, Any] | None = None,
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
        "run_settings": dict(run_settings or {}),
        "steps": steps,
        "references": {
            "source": "decisionsai_role_presets",
            "parked_presets": "Older role presets remain as unlisted JSON bundles under loop_preset_bundles/bundles.",
        },
    }


LOOP_PRESET_DEFINITIONS: list[dict[str, Any]] = [
    _meta(
        name=DEVELOPER_WORKFLOW_NAME,
        slug=DEVELOPER_WORKFLOW_SLUG,
        role="senior_software_engineer",
        category="Engineering",
        archetype="incremental_ship",
        description=(
            "Ingest a ticket as the delivery plan, confirm its execution contract, implement with scoped CLI harness iteration, capture evidence, "
            "correct defects, and close the development slice with compact memory."
        ),
        kickoff=_DEVELOPMENT_KICKOFF,
        goal="linked ticket slice implemented from its confirmed execution contract with harness evidence",
        exit_when="the ticket execution contract is confirmed, the scoped slice is implemented and self-tested, relevant evidence exists, and the ticket has a compact result packet",
        check_command="project lint/test commands from harness self-assessment",
        max_iterations=6,
        steps=_DEVELOPMENT_STEPS,
        tags=["engineering", "ticket", "execution-contract", "cli", "browser-evidence", "memory"],
        run_settings=DEVELOPER_WORKFLOW_RUN_SETTINGS,
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
