---
id: dai_005_mcp_health_observability
title: Add MCP Health and Registration Observability
project: DecisionsAI
created: 2026-05-05 17:20:00
status: ideation
priority: p0
category: mcp
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-005: Add MCP Health and Registration Observability

## Problem
Dynamic MCP registration can fail silently, and tool caps/timeouts are hard to diagnose.

## Proposed Solution
Implement per-server MCP health telemetry and reconciliation visibility.

## Scope
- Track connected/disconnected servers, tool counts, and dropped tools.
- Expose timeout/error rates per server and per tool.
- Surface reconciliation diffs in runtime diagnostics and UI.

## Acceptance Criteria
- Operators can identify why a tool is missing in under 1 minute.
- Tool cap drops are explicitly visible and countable.
- MCP health panel includes error trends and last successful sync.

## Dependencies
- None.

## Risks
- Telemetry volume could add noise without filtering.

