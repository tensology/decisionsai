---
name: decisions-harness-audit
description: Use when auditing DecisionsAI, Codex, Claude, Cursor, Gemini, Pi, Cline, RTK, workflow pre_chain skills, hooks, projected commands, skill routing, or harness setup for drift, duplication, dead references, slow hooks, bloated context, or stale config.
---

# Decisions Harness Audit

Use this for read-only harness health checks. It audits what is actually installed and projected, then produces verified findings with benchmark commands.

## Workflow

1. Map the surface: Decisions bundled skills, registry rows, workflow `pre_chain` usage, projected project skills, local agent configs, RTK, and available CLI backends.
2. Inventory by domain: hooks/config, instruction surface, skill registry, projected commands, workflow chains, session/history friction, state files, and secrets exposure.
3. Verify each finding against disk before reporting it. Rejected findings stay out of recommendations.
4. Write findings as delete/update/enhance/new recommendations with evidence, confidence, effort, and benchmark commands.
5. For Decisions workflows, return artifact paths and a PASS/MISS/SKIP summary. Do not apply fixes.

## References

- Read `references/harness-adapters.md` for harness-specific paths and command equivalents.
- Use `scripts/harness_inventory.py` for a deterministic first-pass inventory.

## Rules

- This skill is read-only.
- Do not remove, rewrite, or move files.
- Do not recommend loosening permissions as an optimization.
- Treat duplicate skills as merge candidates only when the local Decisions skill is not already canonical.
- AgentManager is reference material only; do not suggest replacing DecisionsAI board orchestration with it.
