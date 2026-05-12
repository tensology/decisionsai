---
name: decisions-codex-worker
description: Use when Codex is running work that originated from DecisionsAI tickets, projects, workflows, or Initiative proposals.
---

# DecisionsAI Codex Worker

When a task comes from DecisionsAI:

1. Treat the instruction as project-bound work. Use the provided ticket, board,
   workflow, and project context as the source of truth.
2. Keep edits scoped to the project folder and the explicit ticket outcome.
3. If the prompt starts with result-packet or previous-step context, use that as
   state handed over by the DecisionsAI workflow. Do not discard it when
   implementing or validating the current step.
4. Respect the model selected by DecisionsAI/Codex runtime. Do not claim a
   different model was used unless the runtime explicitly reports it.
5. Report status in a shape DecisionsAI can write back:

```text
Status: completed | failed | needs_input
Summary: ...
Files changed: ...
Tests: ...
Next step: ...
```

6. If a ticket lacks enough context, return `Status: needs_input` with the
   specific missing decision instead of inventing requirements.
7. If the task is part of a workflow, include any artifact paths or validation
   output the next workflow step needs.
8. If DecisionsAI asks for a practical implementation, make a real, inspectable
   artifact rather than a plan-only response unless the instruction is explicitly
   an audit or planning step.
