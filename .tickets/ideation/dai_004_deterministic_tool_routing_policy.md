---
id: dai_004_deterministic_tool_routing_policy
title: Add Deterministic Tool Routing Policy Layer
project: DecisionsAI
created: 2026-05-05 17:20:00
status: ideation
priority: p0
category: tooling
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-004: Add Deterministic Tool Routing Policy Layer

## Problem
Prompt-driven routing across native tools, skills, and MCP tools can become non-deterministic.

## Proposed Solution
Define typed routing policies with explicit precedence and tie-break rules.

## Scope
- Routing precedence matrix (`native`, `skill`, `mcp`).
- Confidence thresholds and fallback behavior.
- Allow/deny policy hooks by tool class and risk level.
- Runtime reason code for each routing decision.

## Acceptance Criteria
- Same input + same state yields same tool path.
- Every routed decision includes machine-readable reason code.
- Denied tools surface actionable fallback paths.

## Dependencies
- None.

## Risks
- Over-constrained policy may reduce flexibility on novel tasks.

