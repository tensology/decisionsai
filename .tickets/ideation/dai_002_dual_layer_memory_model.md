---
id: dai_002_dual_layer_memory_model
title: Introduce Dual-Layer Memory Model (raw_events + stable_memory)
project: DecisionsAI
created: 2026-05-05 17:20:00
status: ideation
priority: p0
category: memory
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-002: Introduce Dual-Layer Memory Model

## Problem
Long-running memory streams can become noisy and contradictory, creating context rot over time.

## Proposed Solution
Split memory into two explicit layers:
- `raw_events`: append-only timeline
- `stable_memory`: consolidated facts with confidence and provenance

## Scope
- Define data contracts for both layers.
- Add consolidation job from raw to stable memory.
- Track superseded or conflicting stable facts.

## Acceptance Criteria
- Stable memory entries always reference one or more raw event sources.
- Conflicts are marked as superseded, not silently deleted.
- Query path can target raw, stable, or hybrid views.

## Dependencies
- DAI-001 recommended.

## Risks
- Over-aggressive consolidation can hide useful detail.

