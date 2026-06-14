"""
Agent Skill Awareness — makes the agent aware of all available skills,
workflow presets, and the learnings system at session start.

This module provides context that gets injected into the agent's system
prompt so it knows what tools it has and when to use them.
"""

from __future__ import annotations



def get_skill_routing_rules() -> str:
    """Return skill routing rules for agent awareness."""
    return """## Skill Routing Rules

When the user's request matches an available skill, invoke it. When in doubt, invoke the skill.

### Core workflow skills (THINK → PLAN → REVIEW → BUILD → TEST → SHIP → REFLECT)

| User says / context implies | Invoke skill |
|------------------------------|-------------|
| "I have an idea", "help me design", brainstorming a feature | brainstorming |
| "Is this the right scope?", "am I thinking too small/big?", before any multi-step workflow | ceo-scope-review |
| "Review this plan", "check before running", "pre-flight", before executing workflows | pre-flight-review |
| "Test this", "QA", "find bugs", "does this work?", "quality check" | qa-tester |
| "Ship it", "create a PR", "merge and deploy", finishing a feature | finishing-a-development-branch |
| "What did we do?", "retro", after any significant session | session-retro |
| "Remember this", "what have we learned?", persist knowledge across sessions | learnings-keeper |

### Specialized skills

| Situation | Invoke skill |
|-----------|-------------|
| Bug, test failure, unexpected behavior | systematic-debugging |
| Multiple independent tasks that can run concurrently | dispatching-parallel-agents |
| Testing a web application with Playwright | webapp-testing |
| Claiming work is complete | verification-before-completion |
| Working in production, destructive operations, "be careful" | safety-guard |

### Skill chain — the full sprint

For any significant feature, follow this chain:
1. brainstorming — explore and design
2. ceo-scope-review — challenge scope (expand/hold/reduce)
3. pre-flight-review — architecture + security + design check
4. Implement — build what was approved
5. qa-tester — structured testing with health scores
6. finishing-a-development-branch — ship it
7. session-retro — log learnings, compound knowledge

### Learnings system

At session start, use get_context_learnings() to retrieve at most 3 surgically
relevant learnings — not a full dump. Learnings have staleness decay (14-day
half-life), are scored by relevance (branch match, file overlap, tag match),
and auto-reinforce when used. Never dump the full learnings file into context.

During sessions, log patterns, pitfalls, and preferences via log_learning().
Use reinforce_learning() when a learning proves useful — it survives decay longer.

This makes the agent smarter without context rot.

### ECC harness pack

DecisionsAI vendors ECC under plugins/ecc and exposes it through the merged skill
registry. ECC adds cross-harness agents, commands, rules, hooks, MCP configs,
and ecosystem skills. Do not duplicate ECC source files into native DecisionsAI
skills unless a skill is intentionally merged. Generated Codex, Claude, Cursor,
and Pi files are harness projections only.

### Safety system

Three modes available:
- careful: warns before destructive commands (rm, DROP TABLE, force-push)
- freeze: restricts file edits to one directory
- guard: both modes combined — maximum safety for production work
"""


def get_workflow_engine_capabilities() -> str:
    """Return workflow engine capabilities for agent awareness."""
    return """## Workflow Engine Capabilities

### Workflow types
- manual: user-triggered, interactive workflows
- instruction: AI-planned, agent-executed workflows
- scheduled: runs on cron/timer
- audit: validation and verification workflows
- retro: post-session analysis workflows (NEW)
- review: pre-execution review workflows (NEW)
- deploy: deployment pipeline workflows (NEW)

### Safety modes (per-workflow)
- null: no safety (default for trusted workflows)
- careful: warns before destructive operations
- freeze: restricts edits to frozen_scope directory
- guard: careful + freeze — maximum safety

### Structured verification templates
Instead of free-text verification, workflows can use named templates:
- web_app: auth, core flows, error handling, UI states, responsive
- api: endpoints, error handling, security
- cli: execution, edge cases
- security: OWASP Top 10, infrastructure, LLM-specific

### Skill chaining
Workflows support pre_chain and post_chain:
- pre_chain: skills run before first step (e.g., ["ceo-scope-review", "pre-flight-review"])
- post_chain: skills run after last step (e.g., ["session-retro"])

### Available presets
- scope_then_execute: full think→plan→review→build→qa→retro cycle
- qa_pass: structured QA with tier selection and health scores
- safe_deploy: deployment pipeline with safety gates and canary monitoring
- (plus existing: file_operations, http_api_health_check, open_applications, python_data_pipeline, web_login_playwright, web_scraping_playwright)
"""


def get_agent_context() -> str:
    """Return complete agent awareness context."""
    return f"""{get_skill_routing_rules()}

---

{get_workflow_engine_capabilities()}

---

## Key principles

- Use skills, don't reimplement them. Skills are tested, structured, and compound knowledge.
- Chain skills together. The full sprint is brainstorming → scope → review → build → QA → ship → retro.
- Search learnings first. Before investigating a bug or starting work, check what the agent already knows about this codebase.
- Log discoveries. Every pattern, pitfall, and preference logged makes the next session faster.
- Safety by default. If in doubt, activate safety-guard. You can always override.
- Evidence before claims. Run verification-before-completion before declaring work done.
"""
