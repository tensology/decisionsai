---
id: dai_006_memory_retention_tiering
title: Add Memory Retention Policy with Hot/Warm/Cold Tiers
project: DecisionsAI
created: 2026-05-05 17:33:00
status: ideation
priority: p1
category: memory
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-006: Add Memory Retention Policy with Hot/Warm/Cold Tiers

## Problem
Unbounded memory growth lowers retrieval relevance and increases context noise.

## Proposed Solution
Introduce tiered retention based on recency, access frequency, confidence, and importance.

## Scope
- Scoring model for retention tier assignment.
- Promotion/demotion job with policy configuration.
- Retrieval bias toward hot and high-confidence memory.

## Acceptance Criteria
- Tier transitions are auditable and reversible.
- Retrieval precision improves on benchmark memory queries.
- Cold tier remains accessible for forensic lookup.

## Dependencies
- DAI-001 and DAI-002 recommended.

## Risks
- Mis-scoring could bury critical but infrequent memory.

