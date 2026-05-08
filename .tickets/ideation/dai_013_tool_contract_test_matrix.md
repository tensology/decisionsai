---
id: dai_013_tool_contract_test_matrix
title: Create Tool Contract Test Matrix Across Native, Skills, and MCP
project: DecisionsAI
created: 2026-05-05 17:33:00
status: ideation
priority: p2
category: tooling
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-013: Create Tool Contract Test Matrix Across Native, Skills, and MCP

## Problem
Tool schema and behavior drift can break workflows unexpectedly.

## Proposed Solution
Define CI contract tests for tool schemas, required args, auth behavior, and error semantics.

## Scope
- Contract snapshots for representative tool classes.
- Required parameter and enum parity checks.
- Error code and failure-mode consistency checks.

## Acceptance Criteria
- CI fails on schema or behavior contract regressions.
- Tool contracts are versioned and reviewable.
- New tool integrations require contract test coverage.

## Dependencies
- DAI-004 recommended.

## Risks
- Snapshot maintenance overhead if contracts churn frequently.

