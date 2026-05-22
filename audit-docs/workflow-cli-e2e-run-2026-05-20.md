# Workflow CLI E2E Run

Date: 2026-05-20

Workflow: `121` Ticket Execution Workflow

Ticket: `111` Create user and org simultaneously

Run: `75`

Project execution session: `1`

Route: Codex CLI, model `auto`

Timing:

- Workflow started: `2026-05-20 21:02:27 UTC`
- Codex CLI session started: `2026-05-20 21:02:42 UTC`
- Codex CLI session completed: `2026-05-20 21:08:39 UTC`
- Codex CLI elapsed: `357s`
- Workflow completed: `2026-05-20 21:08:43 UTC`

Outcome:

- Codex CLI completed successfully.
- Workflow validation failed because the Workflow Orchestrator LLM is configured to `openai/gpt-5.5`, and the OpenAI account returned `insufficient_quota`.
- Ticket `workflow_status` ended as `failed`.

Discrepancies found:

- Direct unauthenticated API e2e call returned `Unauthorized`; backend function path was used for this test.
- Short-lived direct workflow runner can orphan the workflow thread; the e2e test needed to keep the Python process alive until terminal state.
- Agent model errors were previously recorded as passed step results.
- LLM validation previously failed open when the validator was unavailable.
- CLI output stored the noisy beginning of Codex output and lost the useful final tail.
- Execution session serialization did not expose `elapsed_seconds`.
- Execution sessions did not snapshot project git status before/after the CLI run.
- Workflow result packets and ticket audit entries hardcoded `cursor` as execution lane even when the executor was Codex CLI.
- The run could not distinguish pre-existing dirty Player1Sport files from files touched during this ticket because git-status baseline capture was added after the run.

Fixes applied:

- Workflow agent text results now fail closed for quota, unsupported-parameter, rate-limit, and dispatch failure text.
- LLM validation now fails closed instead of passing when no validator response is available.
- CLI output compaction keeps the head and final tail rather than only the noisy beginning.
- Project execution sessions now include serialized elapsed time.
- Future project execution sessions now capture `git_status_before` and `git_status_after`.
- Workflow audit/result execution lane now uses `workflow` instead of hardcoded `cursor`; the concrete CLI backend remains recorded on the project execution session.
