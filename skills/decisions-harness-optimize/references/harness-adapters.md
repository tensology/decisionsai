# Harness Optimize Adapters

Apply fixes through the active project when possible.

| Surface | Apply strategy |
| --- | --- |
| DecisionsAI | Update bundled skills, registry rows, workflow chains, or project projections with tests. |
| Codex | Refresh project `.codex/commands` from canonical skills. |
| Claude Code | Treat user-level config as opt-in and backup-first. |
| Cursor | Refresh project commands/rules, especially Ponytail rules. |
| Gemini/Pi/Cline | Refresh projected skill folders and preserve bundled resources. |

Every adapter must provide:

- backup path
- validation command
- before/after evidence
- skipped findings with reasons
