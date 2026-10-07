# Workflow — Phase 6 Plan (Inline plan protocol)

Detailed protocol for Phase 6. See `workflow-phases.md` for full pipeline navigation.

Skill writes plan files directly. Output: `plans/{date}-{slug}/plan.md` + `phase-XX-*.md` files.

## Step 1 — plan.md frontmatter + body schema

```markdown
---
name: {slug}
status: pending
priority: {high|medium|low}
created: {date}
target: {type} {new|redesign}
blockedBy: []
blocks: []
---

# Plan — {summary}

## Source of truth
[brief.md](./brief.md) · [visual-direction.md](./visual-direction.md)

## Context links
- Existing icons: `app/components/icons/`
- Existing assets: `public/{type}/`

## Goal
{1-3 sentence outcome statement}

## Phases
{table with #, name, file link, status, effort}

## Key dependencies
{which phases block which}

## File ownership
{table mapping file paths → owner phase}

## Success criteria (overall)
{checkbox list — measurable per phase}

## Risks
{table — risk, mitigation}
```

## Step 2 — Phase decomposition rules

Each phase = one logical concern. Tier-branched output:

**If type = landing — phases (in order):**
1. Project scaffold + Tailwind theme tokens from `visual-direction.md`
2. Font loading via `next/font` (display + body)
3. Layout primitives (`Container`, `Section`, `Grid`)
4. Hero section
5. Content sections (social-proof, features, how-it-works, testimonials, pricing?, FAQ, final-CTA, footer)
6. *{if 3D}* Visual effect layer integration
7. Animations + scroll behavior (locked Phase 2e intensity)
8. Responsive + a11y polish + tier-filtered anti-slop audit

**If type = portfolio — phases (in order):**
1. Project scaffold + Tailwind theme tokens
2. Font loading via `next/font`
3. Layout primitives
4. Hero (intro) section
5. Selected Work Grid section
6. Featured Case Study section(s) — count from brief
7. About / Bio section
8. *{if applicable}* Process / Approach section
9. Contact / Availability CTA section
10. Footer
11. *{if case studies have own pages}* Per-project page template at `app/work/[slug]/page.tsx`
12. *{if 3D}* Visual effect layer integration
13. Animations + scroll behavior
14. Responsive + a11y polish + tier-filtered anti-slop audit

**If tier = generic (any other type) — phases driven by Page-Purpose Exercise (NOT a fixed template):**
1. Project scaffold + Tailwind theme tokens
2. Font loading via `next/font`
3. Layout primitives (adjust to page chrome density)
4. Page-purpose definition (consume Phase 1 brief answers)
5. Section selection — pick from `generic-page-anatomy.md` § Section Pattern Library based on purpose. Do NOT default to a hero+features+CTA stack.
6. Implement chosen sections in dependency order
7. *{if 3D}* Visual effect layer integration
8. Animations + scroll behavior (respect locked motion intensity from Phase 2e)
9. Responsive + a11y polish
10. Tier-filtered anti-slop audit per § Applicability Matrix

Example section stacks by generic type:
- `blog` → nav + hero + article-list + footer
- `pricing` → nav + hero + pricing-tiers + FAQ + final-CTA + footer
- `about` → nav + hero + team-grid + values + contact-cta + footer
- `contact` / `coming-soon` → minimal nav + hero + form + footer
- `dashboard` → app-shell (sidebar + topbar) + filter-bar + data-grid + empty-state
- `404` → minimal banner + return-home link
- `legal` → nav + long-form-prose + footer
- custom → user / page-purpose drives

## Step 3 — Dependency analysis (per phase)

For each phase, identify:
- **Inputs:** files produced by previous phases (read-only)
- **Outputs:** files / components produced by this phase
- **Blockers:** must wait for which phases
- **Parallel candidates:** can run alongside which other phases

Default rule: Layout primitives → Sections (sections depend on primitives). Sections within the same depth are parallel-safe.

## Step 4 — File ownership contracts (parallel-safe)

For each phase, declare exact file paths owned. No other phase may write to these files. File-level granularity, not function-level.

Example:
```
| File | Owner phase | Action |
|------|-------------|--------|
| app/page.tsx | 04 (Hero) | CREATE/MODIFY |
| app/components/sections/hero.tsx | 04 | CREATE |
| app/components/sections/features.tsx | 05 | CREATE |
```

If two phases need to modify the same file, restructure tasks OR designate one phase as "shared file integrator" (which handles all modifications to that file).

## Step 5 — Per-phase success criteria + risks

Each `phase-XX-*.md` must include:
- **Explicit checkable success criteria** (measurable, not "looks good")
- **≥2 identified risks** with mitigations

## Step 6 — Hard constraints to surface in every phase

- Custom icons only (NEVER icon library imports)
- Locked palette as Tailwind tokens — no inline hex outside SVG paths
- Real draft copy — no Lorem, no AI clichés (per applicability matrix tier)
- `min-h-[100dvh]` not `h-screen`
- All fonts via `next/font` — no `<link>` CDN
- 3D components: `'use client'` + `dynamic({ ssr: false })`
- Type-specific anti-slop:
  - portfolio → no "Hi I'm passionate" opener, no skill bars
  - landing → no two equal-weight CTAs, no 3-col equal-feature-grid
  - generic + marketing intent → no AI gradient hero, no fake stats, no generic SaaS CTA labels
  - generic + no marketing intent (dashboard / 404 / legal) → `[universal]` rules only

## Step 7 — Approval gate
User reviews `plan.md` + each `phase-XX-*.md`. Iterate until approved. Only proceed to Phase 7 once approved.

## Step 8 — Forbidden plan patterns (auto-refuse, push back)
- Phase that touches >10 files = too broad; decompose
- Phase that exceeds ~200 lines of phase file detail = too large; split
- Two phases owning the same file = conflict; restructure
- Vague success criteria ("works correctly") = push back, force measurable

## Cross-references

- `workflow-phases.md` — full pipeline navigation
- `workflow-brainstorm.md` — Phase 1 brief schema (feeds into plan)
- `workflow-implement.md` — Phase 7 implement protocol (consumes plan)
- `workflow-audit.md` — Phase 8 audit protocol
- `macrostructure-catalog.md` — page-shape archetypes referenced in phase planning
- `anti-slop-rules.md` — Hard constraints reference
