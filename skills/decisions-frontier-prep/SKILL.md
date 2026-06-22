---
name: decisions-frontier-prep
description: Use when preparing hardest high-leverage work for a stronger or scarce model, building a ranked execution queue, asking for fable prep, frontier prep, round-collapse, wave queue, or model-upgrade readiness across code, design, copy, marketing, or architecture work.
---

# Decisions Frontier Prep

Use this to turn broad, difficult work into a small set of execution-ready packets for the best available model. The core rule is selection: keep only work where a stronger model plausibly collapses multiple iterate/fix rounds or raises the quality ceiling.

## Workflow

1. Detect the target harness, project, active board, and candidate repos. Use Decisions project context first; fall back to local git repos only when no board/project context exists.
2. Run a broad diagnosis by domain: security, correctness, performance, architecture, design, copy, marketing, and tech debt.
3. Reject phantom work. Every finding must be verified against live files, current tickets, or an explicit source artifact before it becomes a packet.
4. Write packets using `references/queue-schema.md`. Each packet needs concrete files or artifacts, an operation, acceptance criteria, and a machine check.
5. Score with `scripts/score_frontier_queue.py` or the scoring rules in `references/scoring.md`.
6. Group packets into waves. Wave 1 should be high score and low dependency so it can prove the queue quickly.
7. For DecisionsAI workflows, attach the queue artifact path to the workflow result and keep execution gated by the board/workflow owner.

## Harness Rules

- Read `references/harness-adapters.md` before emitting harness-specific instructions.
- Do not assume a slash command, a single CLI, or a specific model provider.
- Use Decisions workflow `pre_chain` skills for execution context. Do not silently add this skill to default `pre_chain`.
- Keep AgentManager-style multi-agent spawning out of scope; Decisions keeps one operating agent per board.

## Safety

- Archive instead of deleting.
- Never loosen sandbox, permission, branch, or deployment rules to make a packet easier.
- Do not queue work whose machine check is only "agent says done."
- Route work that a normal model can one-shot to ordinary board tickets, not the frontier queue.
