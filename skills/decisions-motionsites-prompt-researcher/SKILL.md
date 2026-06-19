---
name: decisions-motionsites-prompt-researcher
description: Research and normalize MotionSites-style prompt directions, then output a clean motion prompt brief for implementation.
license: MIT
---

# Decisions MotionSites Prompt Researcher

**I am collecting high-signal motion-web prompt directions so the team can reuse proven storytelling language without spending time on raw prompt experiments.**

## Purpose

Use this skill when a user asks for:
- 3D motion websites
- Cinematic landing concepts
- Prompt-driven UI direction from premium motion libraries

## What it does

- Distills prompt families into reusable directions (tone, motion profile, color system, hierarchy).
- Captures usable “prompt modules” for hero, feature reveal, CTA reveal, and transition treatment.
- Produces concise output suitable for `motion-web-site-builder` or direct implementation notes.

## Inputs

1. Project objective (landing page, portfolio, SaaS onboarding, etc.)
2. Audience and primary action (signup, explore, demo)
3. Constraints (framework/libraries, accessibility limits, performance budget)

## Process

1. Normalize the direction from user request and available references.
2. Choose 3–6 core prompts inspired by motion-focused themes:
   - cinematic SaaS hero
   - pulse-depth landing
   - product orbit or orbiting cards
   - dark premium glassmorphism
   - minimal motion-first commerce/agency tone
3. Translate each into a short module:
   - Visual language
   - Motion language
   - Copy voice
   - Risks (motion fatigue, accessibility, readability)
4. Return a ranked `prompt_brief` with one primary and two alternates.

## Required output format

```markdown
# Motion Direction Brief
- Primary Prompt: ...
- Alternate Prompt A: ...
- Alternate Prompt B: ...
- Animation Stack: ...
- Safety Notes: ...
- Acceptance Checklist:
  - Is the hierarchy clear?
  - Can the same action be completed without reading all motion?
  - Contrast ratios preserved at key CTAs?
```

## Usage boundaries

- Do not call external premium libraries as authoritative APIs unless configured.
- Do not claim exact MotionSites internal prompt text.
- Do not propose inaccessible animations (high-CPU full-screen blur unless user allows).

## Coordination

Pair with `motion-web-site-builder` to convert the brief directly into implementation steps.
