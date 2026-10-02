---
name: architecture-deepening-review
description: Use when reviewing a codebase for architectural friction, shallow modules, poor test seams, scattered behavior, duplicated orchestration, or opportunities to deepen modules before implementing a ticket or workflow.
---

# Architecture Deepening Review

Use this to find high-leverage refactors before a Decisions ticket or workflow implementation.

## Workflow

1. Read project domain language first. Use `domain-modeling` if terms are unresolved.
2. Use `codebase-design` vocabulary: module, interface, seam, adapter, depth, leverage, locality.
3. Explore where understanding one behavior requires bouncing across many files or where tests cannot reach real behavior through a public interface.
4. Produce a short report with candidates, files involved, current friction, proposed deeper module, test improvement, and recommendation strength.
5. Ask the user or workflow owner to choose a candidate before designing the new interface.

## Report Rules

- Do not propose implementation during the first review.
- Do not fight existing ADRs unless the friction is concrete.
- Keep AgentManager patterns to assessment notes unless they preserve DecisionsAI one-agent-per-board orchestration.
- Prefer a local HTML or Markdown artifact outside runtime-critical paths.
