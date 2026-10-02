# Premium styling — non-negotiable quality gate — SYNTHESIZED

## Gate law

**No UI ships until it passes this checklist.** “Looks fine” is not evidence.

## Mandatory sources (in-pack copies + live refs)

| Source | Pack path | Role |
|--------|-----------|------|
| taste-skill | `copied-skills/taste/` | Anti-slop baselines, typography, motion, density |
| awesome-design | `copied-skills/awesome-design/` | Named aesthetic systems (enterprise/clean/shadcn/…) |
| decisions-design-references | `copied-skills/decisionsai/decisions-design-references/` | Which reference tool for which job |
| builderio | `copied-skills/builderio/` | Visual plan / arbiter / stay-within-limits |
| open-design | live `reference/open-design` + decisions-open-design skill | Design systems / templates |
| frontend-design | `copied-skills/decisionsai/frontend-design/` | Distinctive production UI |

Also consult: `copied-skills/gstack/DESIGN.md`, `ETHOS.md`, design-html skill.

## Pre-ship checklist (every component / page)

- [ ] **Aesthetic committed** — named direction from awesome-design or project brand (not generic AI purple/Inter slop).
- [ ] **Taste baselines applied** — typography, spacing, density, motion match taste-skill (or explicit user override).
- [ ] **Anti-emoji / anti-cliché** — no emoji UI; no calc-flex percentage grids; prefer CSS grid; `min-h-[100dvh]` not `h-screen` heroes.
- [ ] **Tokens** — colors/type/radius from one theme source (Tailwind theme or CSS variables).
- [ ] **Stencil match** — page type satisfies `09-page-stencils/<type>/`.
- [ ] **Reference check** — at least one pass against design-references playbook (Aceternity/Refero/Mobbin/Godly as appropriate) *or* explicit “no external ref needed” note for trivial chrome.
- [ ] **Builderio discipline** — stayed within visual plan / limits; no drive-by redesign of unrelated surfaces.
- [ ] **Verify evidence** — screenshot or Playwright assert attached for user-facing pages.

## Fail → fix

If any box unchecked: fix before FRAC done-state. Do not merge “we’ll polish later” for first customer-visible screens.
