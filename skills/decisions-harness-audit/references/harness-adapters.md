# Harness Audit Adapters

Audit surfaces conditionally. Missing surfaces are SKIP, not FAIL.

| Surface | Check |
| --- | --- |
| DecisionsAI | `skills/skills_registry.json`, `distr/core/workflow/skill_provision.py`, workflow `pre_chain` fields, project projections. |
| Codex | Project `.codex/commands`, installed Codex skills/plugins, and RTK command expectations. |
| Claude Code | Project/global command and skill folders, hooks, dynamic workflow files if present. Claude user config paths belong only in this adapter. |
| Cursor | Project `.cursor/commands` and `.cursor/rules`, especially Ponytail projection. |
| Gemini | Project `.gemini/commands` and skill push compatibility. |
| Pi | Project `.pi/skills` folders with bundled resources. |
| Cline | Project `.cline/skills` folders with bundled resources. |
| RTK | `rtk --version`, hook status where supported, and whether project instructions require RTK prefixes. |

Minimum report:

- surface map
- confirmed findings
- rejected findings
- benchmark commands
- recommended next action
