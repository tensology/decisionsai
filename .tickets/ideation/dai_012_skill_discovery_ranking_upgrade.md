---
id: dai_012_skill_discovery_ranking_upgrade
title: Upgrade Skill Discovery Ranking to Hybrid Retrieval
project: DecisionsAI
created: 2026-05-05 17:33:00
status: ideation
priority: p2
category: skills
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-012: Upgrade Skill Discovery Ranking to Hybrid Retrieval

## Problem
Lexical-first skill lookup can rank wrong skills for nuanced requests.

## Proposed Solution
Implement hybrid ranking combining semantic match, metadata quality, freshness, and usage success.

## Scope
- Add semantic embedding retrieval for skill descriptions.
- Blend lexical and semantic scores.
- Incorporate governance quality and recency weights.

## Acceptance Criteria
- Top-3 discovery relevance improves on benchmark prompts.
- Ranking explainability includes score components.
- Fallback to lexical ranking when semantic service is unavailable.

## Dependencies
- DAI-011 recommended.

## Risks
- Ranking complexity can make tuning harder without evaluation datasets.

