---
name: decisions-harness-optimize
description: Use after a verified Decisions harness audit when the user asks to apply safe fixes to harness drift, dead references, stale projected skills, duplicate routing, bloated state files, or invalid local config.
---

# Decisions Harness Optimize

Use this only after `decisions-harness-audit` has produced verified findings. The job is to apply safe, reversible fixes with evidence.

## Workflow

1. Read the audit artifacts and classify each finding as confirmed, needs scoping, rejected, or user-owned.
2. Back up every file before changing it.
3. Apply the smallest confirmed fix first.
4. Validate after each change with the benchmark command from the audit.
5. End with PASS/MISS/SKIP rows and artifact paths for Decisions workflow memory.

## Allowed Fix Patterns

- Remove dead references from config after backup and parse validation.
- Move unused clutter into a dated backup/archive folder.
- Narrow over-broad hooks or projected commands only when the audit proves the intended trigger.
- Refresh stale skill projections from the canonical `skills/` source.
- Add rotation for unbounded state files, keeping recent lines and archiving the rest.

## Hard Stops

- Do not delete; move to backup/archive.
- Do not loosen permissions, sandboxing, branch protections, auth, or deployment gates.
- Do not touch user secrets except to report and recommend rotation.
- Do not import AgentManager runtime code as part of optimization.
- Do not apply recommendations that were not verified.
