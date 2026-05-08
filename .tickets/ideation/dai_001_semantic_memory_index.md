---
id: dai_001_semantic_memory_index
title: Add Semantic Memory Index Over Distilled Memory
project: DecisionsAI
created: 2026-05-05 17:20:00
status: ideation
priority: p0
category: memory
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-001: Add Semantic Memory Index Over Distilled Memory

## Problem
Current memory recall is mostly lexical/section-based, which is brittle for paraphrased prompts and latent facts.

## Proposed Solution
Add a semantic retrieval index over distilled memory artifacts while keeping markdown memory files as the source of truth.

## Scope
- Index distilled memory units into vector space.
- Query with hybrid strategy (semantic first, lexical fallback).
- Return provenance links to memory source files/entries.
- Keep degradation path when vector service is unavailable.

## Acceptance Criteria
- Paraphrased memory queries retrieve relevant entries with measurable improvement versus lexical-only baseline.
- Every semantic result includes provenance pointer(s).
- If semantic index is unavailable, lexical recall still works without workflow breakage.

## Dependencies
- None.

## Risks
- Retrieval quality regressions if embeddings are poorly tuned.
- Cost/latency if index strategy is not bounded.

