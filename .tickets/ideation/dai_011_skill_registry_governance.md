---
id: dai_011_skill_registry_governance
title: Add Skill Registry Governance Schema and Lifecycle
project: DecisionsAI
created: 2026-05-05 17:33:00
status: ideation
priority: p2
category: skills
source: competitive_audit_mempalace_cli_anything_composio_mcp_agent_omi
---

# DAI-011: Add Skill Registry Governance Schema and Lifecycle

## Problem
Large skill sets drift in quality without ownership, validation cadence, and deprecation controls.

## Proposed Solution
Add governance metadata and lifecycle rules to skill registry entries.

## Scope
- Required metadata: owner, quality score, last_validated_at, deprecation_state.
- Health checks for stale or failing skills.
- Default discovery excludes deprecated/low-confidence skills.

## Acceptance Criteria
- All active skills comply with governance schema.
- Registry can report stale and ownerless skills.
- Deprecated skills are hidden from default recommendation path.

## Dependencies
- None.

## Risks
- Governance overhead if automation is weak.

