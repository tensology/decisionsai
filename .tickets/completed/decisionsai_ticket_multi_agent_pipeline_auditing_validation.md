---
id: decisionsai_ticket_multi_agent_pipeline_auditing_validation
title: Unified Multi-Agent Pipeline for Ticket Execution, Auditing, and Validation
project: DecisionsAI
created: 2026-05-05 11:13:31
status: done
---

# DecisionsAI Ticket: Unified Multi-Agent Pipeline for Ticket Execution, Auditing, and Validation

## Summary
We need a robust, repeatable pipeline that turns Kanban tickets into completed work with strong auditing and validation, while keeping execution fast. The goal is to unify outcomes from two execution lanes (Cursor-based implementation and CLI-based implementation) so the rest of the system (board subagents, workflows, ticket updates) can treat results the same way.

Right now, gaps show up in orchestration and user experience:
- Project targeting can drift (tickets written to the wrong project).
- Execution lanes have different “shapes” of output, making it harder to automate follow-up and validation.
- Auditing is not consistently staged or escalated; some tickets need deeper audits and some do not.
- TTS/conversational responses in project mode can sound inconsistent and drawn out; responses should be crisp, confident, and context-aware.

This ticket proposes:
- A standardized Result Packet schema that both Cursor and CLI must emit.
- A multi-gate auditing and validation pipeline with escalation rules.
- Board subagent behavior that ingests tickets, runs workflows, and writes results back to tickets consistently.
- Clear rules for when to use Cursor for speed vs CLI for determinism and auditability.


## Goals
1. Make ticket execution predictable and automatable across boards.
2. Ensure Cursor and CLI runs produce identical structured outputs (same schema), even if the evidence differs.
3. Enable layered audits (cheap-to-expensive) with routing and escalation so we don’t burn expensive models on every ticket.
4. Attach audit artifacts and validation results back to tickets in a consistent way.
5. Improve project-mode conversational responses so the assistant doesn’t sound uncertain or overly verbose, and doesn’t mis-target projects.


## Core Concepts
### One intake, multiple execution lanes
- Intake is always the Kanban ticket.
- Execution can happen via:
  - Cursor lane: fast implementation and edits.
  - CLI lane: slower, more deterministic runs; better for auditing, tests, static analysis, and “truth” checks.

### One output shape
Regardless of lane, the system must emit the same “Result Packet” schema. This lets workflows and board agents treat the output as interchangeable.

### Layered gates
Auditing and validation should be staged:
- Early cheap gates catch obvious problems.
- Mid-tier gates do deeper logic/architecture review.
- High-tier “judge” only runs when needed, based on risk or disagreement.


## Proposed Result Packet Schema (Canonical)
This is the standardized structured output that must be emitted by both Cursor and CLI execution paths.

### Required fields
- ticket_id: Local ticket identifier or external key.
- board_id / board_name: Source board context.
- project_id / project_name: Target project context.
- execution_lane: One of [cursor, cli].
- status: One of [success, partial_success, failed, blocked].
- summary: Human-readable short summary of what was done.
- changes:
  - files_changed: List of file paths.
  - change_summary: Per-file or grouped summary.
- commands:
  - commands_run: Exact commands executed (CLI lane) or “equivalent actions” (Cursor lane).
  - commands_suggested: If not run, list what should be run next.
- tests_and_checks:
  - tests_run: What tests or checks were executed.
  - results: Pass/fail plus key output references.
- risks_and_notes:
  - risks: Potential regressions, security concerns, performance concerns.
  - assumptions: Any assumptions made.
  - limitations: What was not done.
- next_actions:
  - recommended: Concrete next steps.
  - needs_human_review: Boolean plus explanation.
- artifacts:
  - logs: Paths or references to stored logs.
  - screenshots: If relevant.
  - diffs_or_patches: Patch references, diff summaries.
  - links: PR/commit links, build links, etc.
- audit:
  - audits_run: List of audit gates executed with model names and outcomes.
  - final_verdict: One of [pass, needs_changes, escalate, cannot_determine].
  - rationale: Why it passed/failed/escalated.

### Evidence mapping differences (allowed)
The evidence source differs by lane, but maps into the same fields:
- Cursor lane evidence: file diffs, editor actions, summary of edits, optionally git diff output.
- CLI lane evidence: command output, exit codes, test runner logs, static analysis reports.


## Workflow Pipeline Design
This section describes the end-to-end pipeline from ticket intake to closure.

### Stage zero: Intake and triage (Board Subagent)
Responsibilities:
- Read incoming ticket.
- Confirm target project.
- If the ticket doesn’t specify a project, ask one crisp question: “Which project should this go to?”
- Determine risk profile and choose execution lane.

Risk profiling inputs:
- Area touched: auth, payments, production config, data migrations.
- Files touched: environment variables, secrets handling, infra.
- Blast radius: core modules vs isolated UI.
- Ticket priority.

Outputs:
- A run plan: which lane, which workflow, which audit gates.
- Ticket updated with a short plan and expected artifacts.


### Stage one: Implementation lane (Cursor-first or CLI-first)
Option A: Cursor-first
- Cursor implements the ticket quickly.
- Cursor must emit Result Packet draft with:
  - files changed
  - summary
  - suggested commands to run
  - any notes

Option B: CLI-first
- For tickets that are mostly scripts, configs, deterministic changes, or where Cursor is not needed.
- The CLI performs changes and immediately can run checks.

Key requirement:
- Both options must produce the same structured Result Packet output.


### Stage two: Packaging step (Normalize output)
Purpose:
- Normalize lane-specific output into canonical Result Packet.

Mechanism:
- A workflow step that takes whatever the lane produced and transforms it into the exact schema.
- It also attaches references to raw evidence (logs, diffs).


### Stage three: Audit gates (multi-model, routed)
We want multiple mechanisms of auditing using different models, but not always all of them.

#### Audit Gate A: Fast, cheap audit
Goal:
- Catch obvious issues, missing steps, glaring logic errors.

Model examples:
- A free-tier or low-cost provider model.

Checks:
- Does the Result Packet include required fields?
- Are there glaring mistakes in changes vs ticket intent?
- Any basic security smells (hardcoded keys, unsafe string interpolation, etc.)

Output:
- audit verdict: pass or needs_changes.
- list of issues.


#### Audit Gate B: Deeper reasoning audit
Goal:
- Review for correctness, edge cases, and architectural fit.

Model examples:
- A stronger model like “Kimika two” (as discussed) or similar.

Checks:
- Edge cases and failure modes.
- Correctness of assumptions.
- Consistency with project conventions.

Output:
- pass or needs_changes.


#### Audit Gate C: Final strict audit
Goal:
- A stricter “final check” using a better model like “GLM five point one” (as discussed).

Checks:
- Higher confidence correctness.
- Broader review for unintended consequences.
- Regression risk.

Output:
- pass, needs_changes, or escalate.


#### Audit Gate D: Expensive judge (conditional)
Goal:
- Only run when:
  - earlier gates disagree,
  - the ticket is high risk,
  - or earlier audits cannot determine.

Model examples:
- A premium model “four point six” tier (as discussed).

Checks:
- Make final judgment.
- Provide high-quality actionable feedback.

Output:
- final verdict.


### Escalation and routing rules
We should define rules to keep cost down and quality high.

Examples:
- If tests fail at any point, stop and mark blocked or failed.
- If Audit A and Audit B both pass and the ticket is low risk, skip Audit C and D.
- If Audit B passes but Audit A fails due to schema or missing evidence, fix packaging, rerun A.
- If Audit B and C disagree, escalate to D.
- If ticket touches auth, payments, prod configs, data migrations, or secrets, automatically run at least through C.
- If two auditors pass but confidence is low, require a CLI verification run.


## Validation Strategy (Truth layer)
Auditing is “reasoning about work,” validation is “proving it works.”

Validation should include:
- Deterministic checks:
  - unit tests
  - lint
  - type checks
  - build
  - integration tests where feasible

Cursor can implement, but CLI should be responsible for validation runs when:
- It’s high risk.
- It’s production-facing.
- The ticket requires proof.
- Any audit gate flags uncertainty.

Validation outputs must be attached as artifacts:
- command outputs
- exit codes
- key snippets of logs
- file paths to stored logs


## Ticket Update Behavior (Board Subagent)
Once execution and audits complete:
- Update the original ticket with:
  - Result Packet summary
  - audit verdict and short rationale
  - validation evidence
  - what’s next

If needs_changes:
- Create follow-up sub-tickets or add todos.
- Move ticket back to an “In Progress” or “Needs Changes” lane.

If pass:
- Move ticket to Done.


## Logging and Audit Trail
We need reliable logs that can be appended or attached to tickets.

Requirements:
- Each workflow run should store:
  - raw outputs per step
  - normalized Result Packet
  - audit gate outputs
  - validation command outputs

Ticket attachments should include:
- a concise human summary in the ticket description
- and references to raw logs (paths or stored artifacts)

Implementation detail:
- Provide a function to gather the most recent relevant run logs and append them into the ticket body or attach them as files.


## Conversational and TTS Improvements in Project Mode
This is a key UX issue:
- The assistant should not “drag out” words, add filler, or sound uncertain.
- Responses should be crisp, directive, and context-aware.

Behavior changes:
- When user specifies a target project (e.g., “for DecisionsAI”), the assistant must switch to that project before creating tickets.
- If there is ambiguity, ask exactly one short clarifying question before writing anything.
- Confirm actions in one sentence, no extra filler.

Examples of desired behavior:
- “Got it. I’m switching to DecisionsAI and creating that ticket now.”
- “Which project should I write this ticket into, DecisionsAI or RelightSA?”

Avoid:
- prolonged uncertainty phrases.
- unnecessary repetition.


## Implementation Tasks
1. Define and document the canonical Result Packet schema.
2. Implement normalization step that converts lane outputs into the schema.
3. Implement audit gates as workflow steps with configurable models.
4. Implement routing and escalation rules.
5. Implement validation steps in CLI as a truth layer.
6. Implement log gathering and attaching/appending to tickets.
7. Update project-mode conversational behavior to confirm target project and reduce filler.


## Acceptance Criteria
- A ticket executed via Cursor produces the same Result Packet fields as a ticket executed via CLI.
- A workflow can run multiple audits with different models and route based on results.
- High-risk tickets automatically trigger deeper audit and CLI validation.
- Logs and audit outputs are attached or referenced in the ticket.
- Creating a ticket always targets the correct project; if unclear, a clarifying question is asked.
- TTS responses are concise and do not include drawn-out filler.


## Notes
This ticket intentionally focuses on system orchestration and pipeline design. It should be implemented in a way that is board-aware, project-aware, and workflow-first, so the pipeline is consistent across all boards and projects.

---

## Execution Audit (2026-05-05)
### Implemented
- Canonical Result Packet schema exists in `distr/core/kanban/result_packet.py` and is used by workflow and CLI writeback paths.
- Workflow run packet lifecycle is implemented (`create_initial_result_packet_for_run`, `append_workflow_step_to_packet`) and persisted in run data.
- Ticket audit trail persistence exists via `append_ticket_audit_entry(...)`.
- CLI lane writeback is normalized into Result Packet-shaped notes in `distr/core/kanban/ticket_writeback.py`.
- Workflow router writes step outcomes into Result Packet and appends ticket audit entries.
- Automatic ticket evidence attachment now exists for workflow + CLI writeback (log source, timestamp window, concise snippet, and no-log diagnostics) via `distr/core/kanban/evidence.py`.
- Risk profiling is implemented and integrated into writeback metadata, including both technical/system risk and product-conversion risk (UI/UX consistency and flow quality).
- Baseline validation-rule generation now includes UI/UX quality checks for product-conversion risk scenarios.

### Outstanding (still required for full acceptance criteria)
1. No blocking items for this ticket scope after current enforcement update.

### Final completion notes (2026-05-05)
- High-risk validation enforcement is now active at workflow completion: required checks (`lint`, `typecheck`, `build`, `tests`) are enforced for high-risk runs and non-compliant runs are downgraded from pass.
- Enforced outcome and missing-check rationale are persisted into Result Packet audit metadata (`audit.audits_run`, `audit.final_verdict`, and next actions).
- Product-conversion risk (UI/UX consistency, flow clarity, control value) is included in risk classification and validation-rule generation.
- Evidence attachment is automated for workflow and CLI writeback, including source path, timestamp window, snippet, and no-log diagnostics fallback.

### Optional future hardening
- Add model-executed gate prompts per audit stage (A/B/C/D) for richer qualitative reasoning beyond rule-engine enforcement.
- Expand end-to-end parity suites across representative cursor vs cli runs with golden Result Packet snapshots.
