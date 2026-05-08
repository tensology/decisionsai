---
id: dai_003_context_budget_engine
title: Implement Context Budget Engine for Workflow Steps
project: DecisionsAI
created: 2026-05-05 17:20:00
status: ideation
priority: p0
category: workflow
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-003: Implement Context Budget Engine for Workflow Steps

## Problem
Oversized and unstructured context assembly increases token waste and response drift.

## Proposed Solution
Create a context budget system with per-step limits, relevance scoring, and compression rules.

## Scope
- Per-step token budgets by workflow type/risk profile.
- Relevance scoring for candidate context items.
- Compression/summarization fallback before hard truncation.
- Metrics on dropped/compressed context units.

## Acceptance Criteria
- Each workflow step remains inside configured token budget.
- Overflow behavior is deterministic and observable.
- Decision trace records what was included/excluded and why.

## Dependencies
- None.

## Risks
- Aggressive trimming may remove important context.

