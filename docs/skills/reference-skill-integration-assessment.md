# Reference Skill Integration Assessment

## Summary

The imported reference repos are useful, but not as wholesale installs. DecisionsAI should keep local workflow skills canonical, selectively merge stronger process guidance, and adapt new skills to the existing project-owned registry and harness projection flow.

| Source | Decision | Rationale |
| --- | --- | --- |
| Dallionking/fable-prep | Adopt as `decisions-frontier-prep` | Unique queueing method for scarce/strong models. Needs harness-neutral language and Decisions workflow artifacts. |
| Dallionking/claude-harness-audit | Adopt as `decisions-harness-audit` and `decisions-harness-optimize` | Useful audit/verify/apply split. Original is Claude-oriented, so adapters isolate surface-specific paths. |
| mattpocock/skills | Merge selectively | Strong engineering language. Existing Decisions/superpowers skills already cover TDD, debugging, brainstorming, and plans, so duplicates should not be imported. |
| simonstaton/AgentManager | Assess-only, reference only | Valuable operational patterns, but its multi-agent platform model does not replace DecisionsAI one-agent-per-board orchestration. |

## Matt Pocock Skill Decisions

| Skill | Decision | Notes |
| --- | --- | --- |
| `tdd` | Merge into `test-driven-development` | Add public-interface tests, vertical slices, and anti-horizontal-slice language. |
| `diagnosing-bugs` | Merge into `systematic-debugging` | Add feedback-loop-first, minimization, ranked hypotheses, and post-mortem cleanup. |
| `grilling`, `grill-me`, `grill-with-docs` | Merge into `brainstorming` | Keep one canonical brainstorming skill and add one-question design-tree pressure. |
| `codebase-design` | Adopt | Fills a vocabulary gap for module depth, seams, adapters, leverage, and locality. |
| `domain-modeling` | Adopt | Useful glossary/ADR discipline for Decisions project memory. |
| `improve-codebase-architecture` | Adapt as `architecture-deepening-review` | Keep the deepening-review loop without Claude-specific invocation assumptions. |
| Deprecated, in-progress, personal, setup utilities | Skip | Reference only until a concrete Decisions gap appears. |

## AgentManager Assessment

AgentManager is useful as an operations reference, not an app integration target for this pass.

Patterns worth borrowing if they map to one-agent-per-board:

- kill switch and incident runbook language
- stuck-agent diagnostics
- idle delivery and message queue ideas
- scoped MCP configuration per active work context
- workspace cleanup and stale-process handling
- token/cost visibility at the agent/session level

Patterns to reject or defer:

- batch agent teams
- recursive parent-child agent trees
- replacing board-owned workflow agents
- full Docker/Cloud Run platform adoption
- cross-agent self-approval or autonomous deploy/merge flows

Boundary: DecisionsAI keeps one-agent-per-board regardless of the number of tickets on that board. AgentManager can inform reliability, lifecycle, and safety design, but it is reference only.

## Registry Policy

New adapted skills use `source: reference-adapted`, source commit provenance, and `target_surfaces` for Codex, Claude, Cursor, Gemini, Cline, and Pi. Existing canonical skills use `merged_selectively` only when their body receives imported guidance.
