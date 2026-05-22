# Workflow Adaptive Quality Memory

Source: `/Users/paul/Downloads/standards.md`

Implemented state:

- Workflow prompts now include universal quality standards through `distr.core.workflow.standards_memory`.
- The Agent Context tab is the visible/editable surface for these standards.
- Meaningful feedback submitted while a workflow is waiting is captured into an `Adaptive Quality Memory` context row for that workflow.
- LLM validation now receives the same universal quality standards so validation does not only judge the narrow step text.
- Existing non-audit workflow `Ticket Execution Workflow` was seeded with the `Universal Quality Standards` Agent Context row.

Operating rule:

Ticket runs must not be treated as complete just because code changed. The workflow has to understand the ticket, route the work, execute carefully, validate with relevant evidence, and only then mark the ticket complete.
