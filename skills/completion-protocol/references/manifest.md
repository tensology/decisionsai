# Completion summary format

Return a short Markdown section that a person can scan without interpreting structured data.

Use this format:

```markdown
## Completion summary

| Area | Status | Evidence |
|---|---|---|
| Primary workflow | Passed | Concise fresh evidence |
| Responsive UI | Passed | Desktop, tablet, and mobile evidence |
| Production deployment | Blocked | Concrete blocker and next action |

Negative-space checks:

- Passed: concise omitted-behavior check and evidence.

Independent audit: Passed, with no unresolved findings.

**Final verdict: Complete.**
```

Rules:

- Use plain Markdown, not JSON, YAML, XML, a code block, or a serialized object.
- Keep the table focused on user-relevant surfaces. Combine closely related inventory items.
- Use `Passed`, `Excluded`, or `Blocked` in the Status column.
- Every Passed item must include fresh evidence.
- Every Excluded item must state the user-approved reason.
- Every Blocked item must state the blocker and next action.
- Include negative-space checks and the independent-audit result after the table.
- Use `Final verdict: Complete` only when every item passed or was explicitly excluded, every required journey passed, and the independent audit has no unresolved findings.
- Otherwise use `Final verdict: Incomplete` and list exactly what remains.
- Output a machine-readable manifest only when the user explicitly asks for JSON or another structured format.
