# Harness Adapters

Write queue instructions in neutral language first, then adapt to the active surface.

| Harness | Adapter guidance |
| --- | --- |
| DecisionsAI | Attach queue artifacts to the workflow run, use project and board context, and report packet evidence in the step result. |
| Codex | Use local files and commands through the active workspace. Prefer project skills and tool discovery over global setup assumptions. |
| Claude Code | A slash command can be offered as an optional entrypoint. Keep user config paths in this adapter, not in the main skill. |
| Cursor | Project commands and rules may be projected, but avoid relying on a specific extension state. |
| Gemini/Pi/Cline | Keep instructions as portable skill files with explicit command alternatives. |

Availability detection should be explicit:

- A Decisions workflow field or board policy can force planning or execution mode.
- A local sentinel file can be used only if the operator created it and the path is named in a config artifact.
- If ambiguous, plan mode is the safe default.
