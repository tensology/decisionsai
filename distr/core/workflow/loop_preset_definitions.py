"""
Hand-authored loop preset definitions.

Each preset is a full step-runner workflow: role-specific instructions, skills,
tools, guardrails, validation, and routing — not a generic work/check/evaluate shell.
"""

from __future__ import annotations

from typing import Any

from distr.core.workflow.loop_text import GUARDRAILS_FOOTER, SELF_PACE_FOOTER

_SCOPE = (
    "- Stay on the ticket scope; avoid unrelated refactors or scope creep\n"
    "- Prefer minimal diffs and concrete evidence over broad rewrites"
)

_DEV_CHECKS = (
    "- Detect lint and test commands from the repo (package.json, Makefile, pyproject.toml, etc.)\n"
    "- Do not assume npm — use whatever this project actually uses"
)


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
    if on_pass_goto_position is not None:
        out["on_pass_goto_position"] = on_pass_goto_position
    if on_fail_goto_position is not None:
        out["on_fail_goto_position"] = on_fail_goto_position
    if validation_pass_action:
        out["validation_pass_action"] = validation_pass_action
    if validation_fail_action:
        out["validation_fail_action"] = validation_fail_action
    return out


# ── Developer: flagship ticket → PRD → build → verify ─────────────────────────

_TICKET_TO_SHIP_KICKOFF = _kickoff(
    "Ticket to PRD to Ship",
    """Goal: ticket acceptance criteria met with a reviewed PRD, implementation, and browser verification
Max iterations: 8
Between iterations run: project test suite + targeted browser check on changed flows
Exit when: PRD approved, tests green, browser verification passed, and self-review clean
Step 1: Ingest the ticket, clarify acceptance criteria, and draft a PRD before writing code.
"""
    + SELF_PACE_FOOTER
    + "\n\n"
    + GUARDRAILS_FOOTER,
)

_TICKET_TO_SHIP_STEPS = [
    _step(
        "Ingest ticket",
        "Read the linked ticket (title, description, comments, attachments). Extract acceptance criteria, "
        "constraints, and unknowns. List assumptions that must be confirmed before a PRD is written.",
        skills=["product-lens", "brainstorming"],
        tools=["cli", "other"],
        other_tool="Kanban ticket context + project repo",
        guardrail=_guardrail(_SCOPE, "- Do not start implementation in this step"),
        failure_checklist=[
            "Acceptance criteria not stated clearly",
            "Ticket context missing or not read",
        ],
        validation_prompt="Acceptance criteria and constraints are written down with any open questions listed.",
    ),
    _step(
        "Draft PRD",
        "Write a concise PRD for this ticket: problem, users, acceptance criteria, out-of-scope, "
        "test plan, and rollout notes. Use writing-plans and doc-coauthoring patterns. Save where the project keeps specs.",
        skills=["writing-plans", "doc-coauthoring", "product-lens"],
        tools=["cli"],
        failure_checklist=["PRD missing acceptance criteria or test plan", "PRD is implementation-only with no why"],
        validation_prompt="PRD exists with acceptance criteria, scope boundaries, and verification approach.",
    ),
    _step(
        "Scrutinize PRD",
        "Run ceo-scope-review on the PRD: challenge scope size, risks, and missing edge cases. "
        "Run pre-flight-review for security/architecture flags relevant to this change. Produce a revised PRD or explicit hold.",
        skills=["ceo-scope-review", "pre-flight-review"],
        tools=["other"],
        other_tool="Orchestrator PRD review",
        guardrail=_guardrail(_SCOPE, "- Do not weaken acceptance criteria to force approval"),
        failure_checklist=["Scope concerns ignored", "P0 security/architecture flags not addressed"],
        validation_prompt="PRD reviewed; scope mode chosen; blocking concerns resolved or explicitly accepted.",
    ),
    _step(
        "Implement slice",
        "Implement the next highest-priority unchecked item from the PRD using executing-plans and tdd-workflow. "
        "One coherent slice per iteration — minimal diff, tests for behavior changed.",
        skills=["executing-plans", "tdd-workflow", "verification-loop"],
        tools=["cli"],
        guardrail=_guardrail(_SCOPE, _DEV_CHECKS),
        failure_checklist=["No tests for changed behavior", "Unrelated files changed"],
        validation_prompt="A coherent slice landed with tests and a readable diff.",
        on_fail_goto_position=3,
    ),
    _step(
        "Run project checks",
        "Run this repo's lint and unit/integration tests (discover commands from project config — do not assume npm). "
        "Capture output. Fix the first failure before continuing.",
        skills=["verification-loop", "ln-622-build-auditor", "systematic-debugging"],
        tools=["cli", "other"],
        other_tool="Project test/lint commands from repo config",
        guardrail=_guardrail(_SCOPE, _DEV_CHECKS, "- Do not disable failing tests to pass"),
        failure_checklist=["Checks skipped", "Wrong command used for this stack", "Failures not fixed"],
        validation_prompt="Project lint/tests pass with command output captured.",
        validation_type="llm_judgment",
        on_fail_goto_position=3,
    ),
    _step(
        "Browser verify changed flows",
        "Using webapp-testing and qa-tester, exercise the user flows touched by this change in the browser. "
        "Check happy path, one error state, and auth if applicable. Capture screenshots or notes for evidence.",
        skills=["webapp-testing", "qa-tester", "browser-qa"],
        tools=["playwright", "browser_use"],
        guardrail=_guardrail(_SCOPE, "- Use test credentials from project env — never invent secrets"),
        failure_checklist=[
            "Changed UI flow not exercised",
            "Console errors ignored",
            "Credentials hardcoded in test steps",
        ],
        validation_prompt="Changed flows verified in browser with evidence (screenshot, trace, or repro notes).",
        on_fail_goto_position=3,
    ),
    _step(
        "Self-review diff",
        "Review the full branch diff with requesting-code-review. Fix P0/P1 findings. Re-run checks if code changed.",
        skills=["requesting-code-review", "ln-511-code-quality-checker", "receiving-code-review"],
        tools=["cli"],
        failure_checklist=["Critical review findings left open", "Debug code or TODOs left in diff"],
        validation_prompt="Self-review complete; no open P0/P1 findings on the diff.",
        on_fail_goto_position=3,
    ),
    _step(
        "Evaluate ship readiness",
        "Confirm: PRD items for this iteration done, tests green, browser verification passed, self-review clean. "
        "If more PRD items remain, loop back. If ticket acceptance criteria met, exit.",
        skills=["product-lens", "finishing-a-development-branch"],
        tools=["other"],
        other_tool="Orchestrator ship judgment",
        guardrail=_guardrail(_SCOPE, "- Do not mark done with open acceptance criteria"),
        failure_checklist=["Exit declared with failing tests or open AC", "PRD items still unchecked without reason"],
        validation_prompt="Ticket acceptance criteria met OR clear list of remaining PRD items for next iteration.",
        on_fail_goto_position=3,
    ),
    _step(
        "Report iteration",
        "Summarize for the ticket: what shipped, test/browser evidence, PRD status, and whether another iteration is needed.",
        skills=["learnings-keeper", "internal-comms"],
        tools=["other"],
        other_tool="Orchestrator ticket update",
        validation_prompt="Ticket-ready summary with evidence links and next-step recommendation.",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
        wait_for_continue=False,
    ),
]

# ── Developer: scope gate then ship (condensed scope-then-execute) ───────────

_SCOPE_THEN_SHIP_STEPS = [
    _step(
        "Scope review gate",
        "Run ceo-scope-review on the ticket. Present HOLD / REDUCE / EXPAND / SELECTIVE EXPANSION. "
        "Produce a revised scope doc before any implementation.",
        skills=["ceo-scope-review", "brainstorming"],
        tools=["other"],
        other_tool="Orchestrator scope gate",
        validation_prompt="Scope mode selected and revised scope document produced.",
        wait_for_continue=True,
    ),
    _step(
        "Pre-flight review",
        "Run pre-flight-review on the approved scope. Surface P0 blockers. Auto-fix mechanical issues only.",
        skills=["pre-flight-review", "ln-621-security-auditor"],
        tools=["cli"],
        failure_checklist=["P0 issues still open", "Pre-flight skipped"],
        validation_prompt="Pre-flight verdict is READY or CONDITIONAL with no open P0.",
    ),
    _step(
        "Implement",
        "Execute the approved scope with executing-plans and tdd-workflow. One iteration slice if the scope is large.",
        skills=["executing-plans", "tdd-workflow", "verification-loop"],
        tools=["cli"],
        guardrail=_guardrail(_SCOPE, _DEV_CHECKS),
        validation_prompt="Approved scope slice implemented with passing local checks.",
        on_fail_goto_position=2,
    ),
    _step(
        "QA pass",
        "Run qa-tester STANDARD on critical paths. Use webapp-testing for UI flows. Health score and ship verdict required.",
        skills=["qa-tester", "webapp-testing"],
        tools=["playwright", "browser_use"],
        validation_prompt="QA health score produced; ship verdict SHIP or CONDITIONAL; P0/P1 addressed.",
        on_fail_goto_position=2,
    ),
    _step(
        "Evaluate exit",
        "All scope items for this iteration complete and QA passed. Otherwise loop to implement.",
        skills=["product-lens", "finishing-a-development-branch"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=2,
    ),
    _step(
        "Session retro",
        "Run session-retro. Log top learnings to learnings-keeper.",
        skills=["session-retro", "learnings-keeper"],
        tools=["other"],
        other_tool="Orchestrator summary",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── De-Sloppify (review cleanup, not npm theater) ─────────────────────────────

_DESLOPPIFY_STEPS = [
    _step(
        "Review diff for slop",
        "Review git diff against the base branch with requesting-code-review. Flag debug code, dead branches, "
        "naming drift, and convention violations. Prioritize P0/P1 only.",
        skills=["requesting-code-review", "ln-511-code-quality-checker", "ln-512-tech-debt-cleaner"],
        tools=["cli"],
        validation_prompt="Review notes list concrete findings with file/line references.",
    ),
    _step(
        "Fix with minimal diffs",
        "Fix findings with the smallest correct diff. No drive-by refactors.",
        skills=["receiving-code-review", "ln-512-tech-debt-cleaner", "verification-loop"],
        tools=["cli"],
        on_fail_goto_position=0,
    ),
    _step(
        "Run project quality checks",
        "Run this repo's lint and tests (discover from project config). Do not assume npm.",
        skills=["ln-622-build-auditor", "verification-loop"],
        tools=["cli", "other"],
        other_tool="Project lint/test commands",
        guardrail=_guardrail(_SCOPE, _DEV_CHECKS),
        on_fail_goto_position=0,
    ),
    _step(
        "Evaluate clean pass",
        "No slop findings remain and quality checks pass.",
        skills=["receiving-code-review", "ln-511-code-quality-checker"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=0,
    ),
    _step(
        "Report",
        "Summarize cleanup for the ticket.",
        skills=["internal-comms"],
        tools=["other"],
        other_tool="Orchestrator summary",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── E2E until green ───────────────────────────────────────────────────────────

_E2E_STEPS = [
    _step(
        "Run E2E suite",
        "Discover and run the project's E2E command (package.json scripts, playwright config, etc.). "
        "Use webapp-testing skill. Capture first failure with trace.",
        skills=["webapp-testing", "e2e-testing"],
        tools=["playwright", "browser_use", "cli"],
        guardrail=_guardrail(_SCOPE, "- Do not delete or skip specs to force green"),
    ),
    _step(
        "Triage first failure",
        "Use systematic-debugging on the first failing spec only. Document root cause hypothesis.",
        skills=["systematic-debugging", "e2e-testing"],
        tools=["cli", "playwright"],
        on_fail_goto_position=0,
    ),
    _step(
        "Fix failing spec",
        "Fix the failing spec with tdd-workflow — minimal change, add regression coverage if needed.",
        skills=["tdd-workflow", "verification-loop"],
        tools=["cli"],
        on_fail_goto_position=0,
    ),
    _step(
        "Re-run E2E",
        "Re-run the full E2E suite or the failing file until green.",
        skills=["webapp-testing", "e2e-testing"],
        tools=["playwright", "browser_use", "cli"],
        on_fail_goto_position=1,
    ),
    _step(
        "Evaluate all green",
        "Entire E2E suite passes.",
        skills=["qa-tester", "verification-loop"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=1,
    ),
    _step(
        "Report",
        "Summarize specs fixed and evidence.",
        skills=["learnings-keeper"],
        tools=["other"],
        other_tool="Orchestrator summary",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── Ship PR until green ───────────────────────────────────────────────────────

_SHIP_PR_STEPS = [
    _step(
        "Confirm branch state",
        "Verify branch, commits, and diff against target branch. Ensure local checks pass before push.",
        skills=["finishing-a-development-branch", "ln-622-build-auditor"],
        tools=["cli"],
    ),
    _step(
        "Push and open PR",
        "Push branch. Open or update PR with summary, test plan, and linked ticket. Use gh CLI if available.",
        skills=["finishing-a-development-branch", "internal-comms"],
        tools=["cli", "other"],
        other_tool="GitHub CLI (gh pr create / gh pr view)",
    ),
    _step(
        "Read CI status",
        "Run gh pr checks (or equivalent). List failing checks with logs.",
        skills=["systematic-debugging", "ln-732-cicd-generator"],
        tools=["cli", "other"],
        other_tool="gh pr checks / CI logs",
        on_fail_goto_position=0,
    ),
    _step(
        "Fix CI blockers",
        "Fix the first CI failure. Re-push. Do not disable checks.",
        skills=["tdd-workflow", "systematic-debugging", "verification-loop"],
        tools=["cli"],
        on_fail_goto_position=2,
    ),
    _step(
        "Evaluate PR green",
        "All required PR checks success and PR is merge-ready.",
        skills=["finishing-a-development-branch", "qa-tester"],
        tools=["other"],
        other_tool="gh pr checks",
        on_fail_goto_position=2,
    ),
    _step(
        "Report",
        "PR link, check status, merge recommendation.",
        skills=["internal-comms"],
        tools=["other"],
        other_tool="Orchestrator summary",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── PR Babysitter ─────────────────────────────────────────────────────────────

_PR_BABYSITTER_STEPS = [
    _step(
        "List watched PRs",
        "Run gh pr list with the watch label (or project equivalent). Note stale, failing, or behind-main PRs.",
        skills=["plan-orchestrate", "systematic-debugging"],
        tools=["cli", "other"],
        other_tool="gh pr list --label codex-watch",
    ),
    _step(
        "Triage each PR",
        "For each watched PR: CI status, review state, behind-main. One action per PR this iteration.",
        skills=["product-lens", "qa-tester"],
        tools=["cli", "other"],
        other_tool="gh pr view / gh pr checks",
    ),
    _step(
        "Remediate once",
        "Fix CI once, rebase if behind, or comment if stale. Escalate repeated failures.",
        skills=["systematic-debugging", "finishing-a-development-branch"],
        tools=["cli"],
        on_fail_goto_position=0,
    ),
    _step(
        "Evaluate watch health",
        "Each watched PR green and current, or escalated with owner tagged.",
        skills=["plan-orchestrate"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=0,
    ),
    _step(
        "Report",
        "Status table per watched PR.",
        skills=["internal-comms"],
        tools=["other"],
        other_tool="Orchestrator summary",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── PR Self-Review ────────────────────────────────────────────────────────────

_PR_SELF_REVIEW_STEPS = [
    _step(
        "Senior review pass",
        "Review diff like a senior reviewer (requesting-code-review). Note P0–P2 with file refs.",
        skills=["requesting-code-review", "ln-511-code-quality-checker"],
        tools=["cli"],
    ),
    _step(
        "Fix findings",
        "Address P0/P1 from review. Minimal diffs.",
        skills=["receiving-code-review", "verification-loop"],
        tools=["cli"],
        on_fail_goto_position=0,
    ),
    _step(
        "Re-review pass",
        "Second pass on updated diff. Track pass count toward three clean passes.",
        skills=["receiving-code-review", "ln-511-code-quality-checker"],
        tools=["cli"],
        on_fail_goto_position=0,
    ),
    _step(
        "Evaluate three clean passes",
        "Three review passes complete with no critical findings.",
        skills=["requesting-code-review"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=0,
    ),
    _step(
        "Report",
        "Review summary ready for PR opening.",
        skills=["internal-comms"],
        tools=["other"],
        other_tool="Orchestrator summary",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── Spec-first ship ───────────────────────────────────────────────────────────

_SPEC_FIRST_STEPS = [
    _step(
        "Pick unchecked spec item",
        "Read spec.md (or project spec). Select the first unchecked requirement. Restate acceptance criteria.",
        skills=["writing-plans", "product-lens"],
        tools=["cli"],
    ),
    _step(
        "Plan the slice",
        "Write a short implementation plan for this item only: files, tests, risks.",
        skills=["writing-plans", "executing-plans"],
        tools=["cli"],
    ),
    _step(
        "Implement and test",
        "Implement the item with tdd-workflow. Run relevant tests.",
        skills=["tdd-workflow", "verification-loop"],
        tools=["cli"],
        on_fail_goto_position=2,
    ),
    _step(
        "Mark spec and verify",
        "Mark requirement [x] in spec.md only after verification. Run project test suite.",
        skills=["verification-loop", "doc-coauthoring"],
        tools=["cli"],
        on_fail_goto_position=2,
    ),
    _step(
        "Evaluate spec complete",
        "spec.md has no unchecked requirements OR this iteration's item is done and more remain.",
        skills=["product-lens"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=0,
    ),
    _step(
        "Report",
        "Spec progress and evidence.",
        skills=["learnings-keeper"],
        tools=["other"],
        other_tool="Orchestrator summary",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── Content: article from ticket ──────────────────────────────────────────────

_CONTENT_ARTICLE_STEPS = [
    _step(
        "Ingest content brief",
        "Read ticket brief: audience, angle, keywords, CTA, brand voice, deadline. List gaps to confirm.",
        skills=["product-lens", "brainstorming", "content-engine"],
        tools=["other"],
        other_tool="Kanban ticket + brand context",
    ),
    _step(
        "Outline article",
        "Produce H2/H3 outline with keyword map and internal link targets. Use article-writing and writing-plans.",
        skills=["article-writing", "writing-plans", "seo"],
        tools=["cli"],
    ),
    _step(
        "Draft body",
        "Write the draft following the outline. Use content-engine and doc-coauthoring patterns.",
        skills=["content-engine", "doc-coauthoring", "article-writing"],
        tools=["cli"],
        on_fail_goto_position=2,
    ),
    _step(
        "SEO and metadata pass",
        "Run seo skill: title, meta, headings, snippet. Fix readability issues.",
        skills=["seo", "content-engine"],
        tools=["cli"],
    ),
    _step(
        "Preview in browser",
        "Render preview (local CMS, staging, or markdown preview). Check formatting, links, CTA.",
        skills=["browser-qa", "webapp-testing"],
        tools=["playwright", "browser_use"],
        on_fail_goto_position=2,
    ),
    _step(
        "Evaluate publish-ready",
        "Draft matches brief, SEO pass done, preview checked.",
        skills=["content-engine", "product-lens"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=2,
    ),
    _step(
        "Report",
        "Publish checklist and draft location for human approval.",
        skills=["social-publisher", "internal-comms"],
        tools=["other"],
        other_tool="Orchestrator ticket update",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── Content: SEO landing page ─────────────────────────────────────────────────

_CONTENT_SEO_PAGE_STEPS = [
    _step(
        "Ingest page brief",
        "Extract target keyword, intent, audience, and conversion goal from the ticket.",
        skills=["seo", "product-lens", "brainstorming"],
        tools=["other"],
        other_tool="Kanban ticket",
    ),
    _step(
        "Page structure",
        "Outline hero, proof, FAQ, CTA. Map keywords to sections.",
        skills=["seo", "writing-plans", "frontend-design"],
        tools=["cli"],
    ),
    _step(
        "Implement page slice",
        "Build or update the page in the repo. Match project component patterns.",
        skills=["executing-plans", "frontend-design"],
        tools=["cli"],
        on_fail_goto_position=2,
    ),
    _step(
        "SEO technical check",
        "Verify title, meta, canonical, headings, schema if applicable.",
        skills=["seo", "ln-643-api-contract-auditor"],
        tools=["cli", "playwright"],
        on_fail_goto_position=2,
    ),
    _step(
        "Browser QA",
        "Check responsive layout, CTA, forms, and console errors on staging/local.",
        skills=["webapp-testing", "browser-qa", "qa-tester"],
        tools=["playwright", "browser_use"],
        on_fail_goto_position=2,
    ),
    _step(
        "Evaluate ship",
        "Page meets brief and SEO checklist.",
        skills=["seo", "product-lens"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=2,
    ),
    _step(
        "Report",
        "URL, screenshots, remaining items.",
        skills=["internal-comms"],
        tools=["other"],
        other_tool="Orchestrator summary",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
    ),
]

# ── Sales: cold outreach batch ────────────────────────────────────────────────

_SALES_COLD_STEPS = [
    _step(
        "Ingest lead context",
        "From the ticket: company, persona, pain, prior touchpoints. List what is still unknown.",
        skills=["product-lens", "brainstorming"],
        tools=["other"],
        other_tool="CRM / ticket fields",
    ),
    _step(
        "Research prospect",
        "Gather 2–3 specific hooks (recent news, tech stack, role). No generic fluff.",
        skills=["research-ops", "product-lens"],
        tools=["cli", "other"],
        other_tool="Web research / LinkedIn / company site",
    ),
    _step(
        "Draft outreach sequence",
        "Write initial email + one follow-up using email-ops patterns. Personalize with research hooks.",
        skills=["email-ops", "internal-comms", "writing-plans"],
        tools=["cli"],
        on_fail_goto_position=2,
    ),
    _step(
        "Compliance and tone check",
        "No false claims, no spam triggers, clear CTA, unsubscribe if required.",
        skills=["internal-comms", "safety-guard"],
        tools=["other"],
        other_tool="Orchestrator review",
        on_fail_goto_position=2,
    ),
    _step(
        "Evaluate ready to send",
        "Sequence is personalized, compliant, and tied to ticket goal.",
        skills=["product-lens"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=2,
    ),
    _step(
        "Report",
        "Paste-ready emails and send recommendation (human approves send).",
        skills=["email-ops"],
        tools=["other"],
        other_tool="CRM note / ticket update",
        validation_pass_action="end_loop",
        validation_fail_action="end_loop",
        wait_for_continue=True,
    ),
]

# ── Sales: follow-up cadence ──────────────────────────────────────────────────

_SALES_FOLLOWUP_STEPS = [
    _step(
        "Review open threads",
        "List open conversations from ticket/CRM: last touch, stage, next action date.",
        skills=["plan-orchestrate", "product-lens"],
        tools=["other"],
        other_tool="CRM / ticket history",
    ),
    _step(
        "Draft follow-ups",
        "Write follow-up for the highest-priority thread. Reference prior context. One ask only.",
        skills=["email-ops", "internal-comms"],
        tools=["cli"],
    ),
    _step(
        "Log next step",
        "Record next follow-up date and outcome in ticket/CRM note.",
        skills=["email-ops", "plan-orchestrate"],
        tools=["other"],
        other_tool="CRM / ticket update",
    ),
    _step(
        "Evaluate cadence",
        "No overdue threads without a drafted touch or explicit pause reason.",
        skills=["product-lens"],
        tools=["other"],
        other_tool="Orchestrator judgment",
        on_fail_goto_position=0,
    ),
    _step(
        "Report",
        "Threads touched and next dates.",
        skills=["internal-comms"],
        tools=["other"],
        other_tool="Orchestrator summary",
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
            "elorm": "https://loops.elorm.xyz/loops",
        },
    }


LOOP_PRESET_DEFINITIONS: list[dict[str, Any]] = [
    _meta(
        name="Ticket to PRD to Ship",
        slug="ticket-to-prd-to-ship",
        role="developer",
        category="Developer",
        archetype="incremental_ship",
        description="Ingest ticket, write and scrutinize a PRD, implement, run project checks, browser-verify, self-review, ship.",
        kickoff=_TICKET_TO_SHIP_KICKOFF,
        goal="ticket acceptance criteria met with reviewed PRD, tests, and browser proof",
        exit_when="PRD approved, tests green, browser verification passed, self-review clean",
        check_command="project lint + test suite + browser check on changed flows",
        max_iterations=8,
        steps=_TICKET_TO_SHIP_STEPS,
        tags=["developer", "prd", "playwright", "ticket"],
    ),
    _meta(
        name="Scope Then Ship",
        slug="scope-then-ship",
        role="developer",
        category="Developer",
        archetype="review_cleanup",
        description="Scope gate, pre-flight, implement, QA in browser, retro — no code before scope approval.",
        kickoff=_kickoff(
            "Scope Then Ship",
            "Goal: approved scope shipped with QA evidence\nMax iterations: 6\n"
            "Exit when: scope items done and QA ship verdict is SHIP or CONDITIONAL\n"
            "Orientation: read the linked ticket to confirm scope boundaries before ceo-scope-review.\n"
            "Step 1: Run ceo-scope-review before any implementation.",
        ),
        goal="approved scope shipped with QA evidence",
        exit_when="scope complete and QA ship verdict SHIP or CONDITIONAL",
        check_command="qa-tester health score + project tests",
        max_iterations=6,
        steps=_SCOPE_THEN_SHIP_STEPS,
        tags=["developer", "scope", "qa"],
    ),
    _meta(
        name="De-Sloppify Pass",
        slug="de-sloppify-pass",
        role="developer",
        category="Developer",
        archetype="review_cleanup",
        description="Senior-style diff review, minimal fixes, project quality checks — not generic npm theater.",
        kickoff=_kickoff(
            "De-Sloppify Pass",
            "Goal: diff is clean and convention-aligned\nMax iterations: 4\n"
            "Between iterations run: project lint and tests (discover from repo)\n"
            "Exit when: no slop findings and checks pass\n"
            "Orientation: read the linked ticket for scope before reviewing the diff.\n"
            "Step 1: Review diff for debug code, dead branches, naming issues.",
        ),
        goal="recent changes are clean, minimal, and convention-aligned",
        exit_when="review finds no slop and project checks pass",
        check_command="project lint + tests (from repo config)",
        max_iterations=4,
        steps=_DESLOPPIFY_STEPS,
        tags=["review", "quality"],
    ),
    _meta(
        name="E2E Until Green",
        slug="e2e-until-green",
        role="developer",
        category="Developer",
        archetype="check_fix_until_green",
        description="Run E2E with playwright, triage first failure, fix, re-run until green.",
        kickoff=_kickoff(
            "E2E Until Green",
            "Goal: E2E suite passes\nMax iterations: 10\n"
            "Between iterations run: project E2E command\n"
            "Exit when: E2E suite exits 0\n"
            "Orientation: read the linked ticket to confirm which flows this run must cover.\n"
            "Step 1: Run E2E tests and capture first failure.",
        ),
        goal="E2E suite passes",
        exit_when="E2E command exits 0",
        check_command="project E2E test command",
        max_iterations=10,
        steps=_E2E_STEPS,
        tags=["e2e", "playwright"],
    ),
    _meta(
        name="Ship PR Until Green",
        slug="ship-pr-until-green",
        role="developer",
        category="Developer",
        archetype="ship_with_ci",
        description="Push, open PR, read CI with gh, fix blockers, until checks green.",
        kickoff=_kickoff(
            "Ship PR Until Green",
            "Goal: PR open with all CI checks passing\nMax iterations: 10\n"
            "Between iterations run: gh pr checks\n"
            "Exit when: all PR checks success\n"
            "Orientation: read the linked ticket to confirm branch scope and acceptance criteria.\n"
            "Step 1: Confirm branch and open or update PR.",
        ),
        goal="PR open with all CI checks passing",
        exit_when="all PR checks are success",
        check_command="gh pr checks",
        max_iterations=10,
        steps=_SHIP_PR_STEPS,
        tags=["pr", "ci"],
    ),
    _meta(
        name="PR Babysitter",
        slug="pr-babysitter",
        role="developer",
        category="Developer",
        archetype="watch_maintain",
        description="Watch labeled PRs: triage, fix CI once, rebase, escalate stale.",
        kickoff=_kickoff(
            "PR Babysitter",
            "Goal: watched PRs healthy or escalated\nMax iterations: 20\n"
            "Between iterations run: gh pr list --label codex-watch\n"
            "Exit when: each watched PR green and current, or escalated.\n"
            "Orientation: read the linked ticket for which PRs or labels to watch.\n"
            "Step 1: List open PRs with the watch label and triage the first stale or failing one.",
        ),
        goal="open PRs labeled codex-watch are healthy (CI green, rebased, not stale)",
        exit_when="each watched PR is green and current, or escalated",
        check_command='gh pr list --label "codex-watch"',
        max_iterations=20,
        steps=_PR_BABYSITTER_STEPS,
        tags=["pr", "ci"],
    ),
    _meta(
        name="PR Self-Review",
        slug="pr-self-review",
        role="developer",
        category="Developer",
        archetype="review_cleanup",
        description="Three senior self-review passes on the diff before opening PR.",
        kickoff=_kickoff(
            "PR Self-Review",
            "Goal: three clean self-review passes\nMax iterations: 3\n"
            "Between iterations run: git diff against base branch\n"
            "Exit when: three passes with no critical findings.\n"
            "Orientation: read the linked ticket to confirm what the diff must deliver.\n"
            "Step 1: Review the current diff against ticket acceptance criteria.",
        ),
        goal="three clean self-review passes on the current diff",
        exit_when="three passes complete with no critical findings",
        check_command="git diff against base branch",
        max_iterations=3,
        steps=_PR_SELF_REVIEW_STEPS,
        tags=["review", "pr"],
    ),
    _meta(
        name="Spec-First Ship",
        slug="spec-first-ship",
        role="developer",
        category="Developer",
        archetype="incremental_ship",
        description="One spec.md checkbox per iteration: plan, implement, verify, mark done.",
        kickoff=_kickoff(
            "Spec-First Ship",
            "Goal: spec.md fully checked off\nMax iterations: 15\n"
            "Between iterations run: project tests\n"
            "Exit when: no unchecked requirements in spec.md.\n"
            "Orientation: read the linked ticket and locate spec.md before picking the next checkbox.\n"
            "Step 1: Read spec.md and select the next unchecked requirement.",
        ),
        goal="every requirement in spec.md is implemented and checked off",
        exit_when="spec.md has no unchecked requirements",
        check_command="project test suite",
        max_iterations=15,
        steps=_SPEC_FIRST_STEPS,
        tags=["spec", "planning"],
    ),
    _meta(
        name="Article from Ticket",
        slug="article-from-ticket",
        role="content",
        category="Content",
        archetype="incremental_ship",
        description="Brief → outline → draft → SEO → browser preview → publish-ready report.",
        kickoff=_kickoff(
            "Article from Ticket",
            "Goal: publish-ready article matching the ticket brief\nMax iterations: 5\n"
            "Exit when: draft passes SEO and browser preview checks.\n"
            "Orientation: read the linked ticket brief for audience, angle, keywords, and CTA.\n"
            "Step 1: Ingest the content brief and list gaps to confirm.",
        ),
        goal="publish-ready article matching ticket brief",
        exit_when="draft passes SEO and browser preview",
        check_command="SEO checklist + preview URL",
        max_iterations=5,
        steps=_CONTENT_ARTICLE_STEPS,
        tags=["content", "seo", "blog"],
    ),
    _meta(
        name="SEO Landing Page",
        slug="seo-landing-page",
        role="content",
        category="Content",
        archetype="incremental_ship",
        description="Keyword brief → structure → implement page → technical SEO → browser QA.",
        kickoff=_kickoff(
            "SEO Landing Page",
            "Goal: landing page live-ready for target keyword\nMax iterations: 6\n"
            "Exit when: technical SEO and browser QA pass.\n"
            "Orientation: read the linked ticket for target keyword, audience, and page goal.\n"
            "Step 1: Confirm keyword brief and page structure plan.",
        ),
        goal="landing page meets keyword brief and technical SEO bar",
        exit_when="technical SEO and browser QA pass",
        check_command="SEO metadata + staging URL QA",
        max_iterations=6,
        steps=_CONTENT_SEO_PAGE_STEPS,
        tags=["seo", "landing-page"],
    ),
    _meta(
        name="Cold Outreach Batch",
        slug="cold-outreach-batch",
        role="sales",
        category="Sales",
        archetype="incremental_ship",
        description="Lead context → research → personalized email sequence → compliance check.",
        kickoff=_kickoff(
            "Cold Outreach Batch",
            "Goal: personalized outreach ready for human send approval\nMax iterations: 5\n"
            "Exit when: sequence is researched, personalized, and compliant.\n"
            "Orientation: read the linked ticket for lead list, ICP, and messaging constraints.\n"
            "Step 1: Ingest lead context and confirm personalization requirements.",
        ),
        goal="personalized outreach ready for approved send",
        exit_when="sequence researched, personalized, and compliant",
        check_command="compliance + personalization checklist",
        max_iterations=5,
        steps=_SALES_COLD_STEPS,
        tags=["sales", "outreach", "email"],
    ),
    _meta(
        name="Follow-up Cadence",
        slug="follow-up-cadence",
        role="sales",
        category="Sales",
        archetype="watch_maintain",
        description="Review open sales threads, draft follow-ups, log next steps in CRM/ticket.",
        kickoff=_kickoff(
            "Follow-up Cadence",
            "Goal: no overdue threads without a drafted touch\nMax iterations: 10\n"
            "Exit when: each open thread has next action scheduled.\n"
            "Orientation: read the linked ticket for which accounts or threads this cadence covers.\n"
            "Step 1: Review open threads and identify overdue follow-ups.",
        ),
        goal="open threads have drafted follow-up and next date",
        exit_when="no overdue threads without draft or pause reason",
        check_command="CRM/ticket thread review",
        max_iterations=10,
        steps=_SALES_FOLLOWUP_STEPS,
        tags=["sales", "follow-up"],
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
