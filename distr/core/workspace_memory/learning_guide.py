"""Agentic learning guide — how harnesses read and update workflow memory."""

from __future__ import annotations

from .template import write_text


def workflow_learning_guide_md(*, workflow_id: int, workflow_name: str) -> str:
    """Filesystem guide for standards, memory, skills, and harness continuity."""
    return f"""# Workflow learning guide — {workflow_name}

This workflow uses DecisionsAI filesystem memory. Your harness (Cursor, Codex, Claude Code, Pi, Cline, Hermes, VS Code, or any IDE agent) should treat these files as source of truth — not chat history alone.

## On pick up (start of session)

1. Read `agents.md` in this workflow folder (workflow_id={workflow_id}).
2. Read `router.md` — follow parent links only when routing is ambiguous.
3. Read `context.md` — context rules, variables, and step routing table.
4. Read `memory/handoff.md` + `memory/active.md` for continuity from the last harness.
5. Skim `references/` — learned rules, context rules, and agent-context variables.
6. If a run is active, also read `~/.decisions/workspaces/runs/{{run_id}}/memory/handoff.md`.
7. If working in a linked repo, read `{{repo}}/.decisions/agents.md` (projection of project memory).

Say **pick up** in DecisionsAI chat to reload this chain without re-pasting paths.

## Standards this workflow learns

Record durable standards in these places (most specific wins):

| What | Where | When |
|------|-------|------|
| Board validation / policy | `references/learned-rules.md` (board scope) + orchestrator | After repeated failures or explicit "remember this" |
| Project quirks / stack | `references/context-items/` + `.decisions/learnings/learnings.jsonl` in repo | After you discover repo-specific patterns |
| Workflow rules | `references/context-rules.md` + `context.md` | When workflow context rules change in DecisionsAI |
| Step skills / harness | `stages/{{NN}}_{{slug}}/CONTEXT.md` | When a step's pre_chain skills change |
| Session continuity | `memory/handoff.md` | End of every session before switching harness |

Do not invent parallel memory in random markdown files. Update the table above or say **handoff** in DecisionsAI.

## How to update memory (handoff)

Before ending a session or switching harness:

1. Edit `memory/handoff.md` with:
   - What you finished
   - What is in progress
   - Files changed, tests run, blockers
   - Next harness should start here
2. Append one line to `memory/ledger.jsonl`:
   `{{"ts":"ISO8601","source":"harness","summary":"one line"}}`
3. Say **handoff** in DecisionsAI chat (or use the ticket handoff API) so the orchestrator and repo projection sync.

## How to record learnings (one-shot improvement)

When you discover a pattern, pitfall, or user preference that should survive sessions:

- **Repo-scoped** (codebase facts): append to `{{repo}}/.decisions/learnings/learnings.jsonl` using the learnings-keeper format (type, key, insight, confidence).
- **Board/project policy**: orchestrator learned rules — DecisionsAI syncs to `references/learned-rules.md`.
- **Workflow steering**: visible in run steering memory; important items belong in `memory/handoff.md`.

Reinforce learnings when they prove useful. Do not dump the full learnings file into every prompt — retrieve surgically (max 3 relevant entries).

## Skills and harness projection

Step routing in `context.md` lists skills per step. When skills change:

- Project harness folders receive projections: `.cursor/commands`, `.codex/commands`, `.pi/skills`
- Use **Sync to repo** on the linked project in DecisionsAI after workflow skill changes
- Read `stages/{{NN}}_{{slug}}/CONTEXT.md` for the active step's Read/Skip/Skills contract

## Planning and UI design

For planning, spec, or UI-heavy steps, use **decisions-open-design** (Open Design reference) alongside Mermaid diagrams. Put briefs in `pipeline/brief/` and outputs in `pipeline/output/` — not scattered in chat.

## Return contract (report back to DecisionsAI)

When a step is done, paste this block into DecisionsAI or your harness reporter:

```
Status: completed | failed | needs_input
Summary: <plain English>
Tests run: <commands + pass/fail; or N/A>
Drift check: <scope/UI drift vs ticket, or none>
Security: <findings or none>
UI assessment: <browser checks, or N/A>
Self-corrections: <what you fixed>
Files changed: <paths or none>
Blockers: <none or what stops progress>
```

The orchestrator stores this on the workflow run and advances the loop when validation passes.
"""


def sync_workflow_learning_guide(workflow_id: int, *, workflow_name: str = "") -> str:
    from .paths import companion_root

    name = workflow_name.strip() or f"Workflow {workflow_id}"
    if not workflow_name.strip():
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflow

            with get_session() as session:
                wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
                if wf and (wf.name or "").strip():
                    name = wf.name.strip()
        except Exception:
            pass

    root = companion_root("workflows", workflow_id) / "references"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "learning-guide.md"
    write_text(path, workflow_learning_guide_md(workflow_id=workflow_id, workflow_name=name))
    return str(path)
