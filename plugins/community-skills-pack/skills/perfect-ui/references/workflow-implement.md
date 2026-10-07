# Workflow — Phase 7 Implement (Inline implement protocol)

Detailed protocol for Phase 7. See `workflow-phases.md` for full pipeline navigation.

Skill implements directly from `plan.md` + `phase-XX-*.md` files. Per CLAUDE.md: NO auto-commit — user reviews + commits manually after each phase.

## Step 1 — Phase execution order
Follow `plan.md` dependency graph. Default sequential unless plan marks phases parallel-safe. Mark each phase status `in_progress` before starting; `completed` after success criteria all check.

## Step 1.5 — Pre-emit `<design_plan>` verification (v2.5.0+)

At Phase 7 entry, BEFORE any code emission, run the pre-emit design_plan verification gate. See `preemit-design-plan.md` for full 10-field schema + validation rules.

1. Populate 10-field block from Phases 1-6 locked picks
2. Run validation; collect all errors
3. If any FAIL → return to relevant phase to fix; do NOT proceed to Step 2
4. If ALL PASS → block stamped in CSS + `plans/{slug}/plan.md § Pre-emit verification`; Step 2 proceeds

**Component-scope runs minimal subset (4 fields):** `vibe_validity`, `motion_personality`, `button_contrast`, `honest_copy`. See `preemit-design-plan.md § Component-scope subset`.

## Step 1.6 — Component-scope short-circuit (v2.5.0+)

If Phase 0.5 detected component-scope (see `component-scope.md`), Phase 7 collapses page-level emission:

- **Skip:** project scaffold (single component), macrostructure-driven section sequence, hero enrichment, multi-section composition
- **Emit:** 2 files — component artifact (`Button.tsx` / `Card.vue` / `button.css+html` / etc.) + `.preview.*` 8-state wrapper (extension auto-detected from framework, per `component-scope.md § What component-scope EMITS`)
- **Stamp:** component-scoped CSS comment (see `component-scope.md § Stamp format`)

Skip Step 6 (project memory log write) — component runs don't rotate.

After 2 files emitted, Phase 7 complete. Continue to Phase 8 audit (component-scope subset; see `workflow-audit.md § Step Y`).

## Step 2 — Per-phase constraints (enforce throughout)

**Imports:**
- Import icons from `app/components/icons` — NEVER `npm install` any icon library
- All fonts via `next/font/local` or `next/font/google` — NO `<link>` CDN
- 3D components: `'use client'` + `dynamic(() => import(...), { ssr: false })`
- React Three Fiber used as shader runner only — no `<GLTFLoader>` / `useGLTF` / `<OrbitControls>` unless user-GLB override logged

**Colors + tokens:**
- Use Tailwind theme tokens for all colors — no inline hex outside SVG icon paths
- Single accent token, ≤ 10% surface area
- Off-black / off-white only (never pure `#000` / `#FFF`)

**Layout + composition:**
- Hero composition follows `visual-direction.md` § Spatial Language (no centered-H1 unless vibe = minimal)
- `min-h-[100dvh]` not `h-screen`
- `text-wrap: balance` on h1/h2/h3; `text-wrap: pretty` on `<p>`

**Copy:**
- Real draft copy — no Lorem, no AI cliché vocabulary (per applicability matrix tier)
- Realistic data (no John Doe / 99.99% / Acme Corp)
- **Honest copy** — if metric / testimonial / logo / case-study count not supplied by user, use em-dash placeholder + label (`— metric to confirm`) rendered as visible grey block. Never invent. See `anti-slop-rules.md § Honest Copy Mandate` for 3 accepted paths.

**Motion (per `motion-patterns.md` § Motion Personalities + Vibe × Motion Intensity Matrix, locked Phase 2e + 2.6):**
- Apply motion ONLY at locked intensity (0/3 → 3/3)
- Use locked Brand Motion Identity (signature easing + duration palette + entrance pattern from Phase 2.6 personality)
- Stack escalation: CSS → Framer Motion → Lenis → GSAP (only escalate if prior tier insufficient)
- NO generic fade-up on every element (≤30% sections animate at 2/3 intensity)
- NO motion on body `<p>` text
- Use personality-paired `cubic-bezier(...)` easing (see `motion-patterns.md` § Motion Personalities) — NOT `ease-in-out` / `ease-out` named keywords
- `prefers-reduced-motion` MUST be respected (Framer Motion `useReducedMotion()` or CSS `@media`)
- Mobile auto-degrades intensity by 1 step at < 768px
- Total motion JS bundle ≤ 100KB gz
- **GSAP integration (v2.4.1+):** if intensity = 3/3 OR GSAP keyword detected, invoke gsap-* skills or fallback inline patterns. See Step 2.5 below + `gsap-integration.md`.

## Step 2.5 — GSAP integration check (v2.4.1+)

If motion intensity (Phase 2e) = 3/3 OR brief contains keywords (`GSAP` / `ScrollTrigger` / `scrub` / `pin` / `scroll choreography` / `scroll-driven`), invoke GSAP skill integration workflow per `gsap-integration.md`.

Auto-detect at entry to Phase 7:

```
1. Check trigger conditions:
   - motion_intensity == 3/3?
   - brief.text contains GSAP keyword?
   - phase_2d_effect == "scroll-driven distortion"?
2. If any condition true:
   a. Check skills installed: `ls ~/.claude/skills/gsap-core/SKILL.md`
   b. If installed: invoke gsap-* skills per selection logic (see `gsap-integration.md § The 8 gsap skills`)
   c. If not installed: use fallback inline patterns (see `gsap-integration.md § Fallback inline patterns`)
3. If all conditions false: skip GSAP integration; CSS / Framer Motion / Lenis cover intensity 0-2/3
```

See `gsap-integration.md` for full detection rules + skill selection table + invocation pseudo-code + fallback patterns.

## Step 2.6 — Smooth scroll setup (type-gated for landing/portfolio, vibe-gated for generic)

Apply the smooth-scroll tier selected at Phase 2e.1 (stored in `plans/{slug}/visual-direction.md § Smooth Scroll`). Full decision tree + per-tier setup + guards + audit checklist live in `references/smooth-scroll-flow.md`.

**v2.5.1+ rule:** landing AND portfolio outputs auto-get Lenis (Tier 3 at intensity 1-2/3, Tier 4 at 3/3) regardless of vibe. Generic tier remains vibe-gated. Hard guards (reduced-motion / touch / intensity 0/3 / CDN failure) always force Tier 1 native at runtime regardless of authored tier.

```
Resolve smooth-scroll tier from visual-direction.md:

1. Read tier (1-5) chosen at Phase 2e.1
2. Branch by tier:
   • Tier 1 (native) → no JS scroll runtime; add scroll-margin-top to anchor targets
   • Tier 2 (CSS smooth) → html { scroll-behavior: smooth } + reduced-motion media query
   • Tier 3 (Lenis) → emit lenis.js with hard guards (prefers-reduced-motion + touch-primary + CDN failure) + lazy rAF loop + window.__lenis export
   • Tier 4 (Lenis + ScrollTrigger sync) → Tier 3 setup + gsap.ticker.add(lenis.raf) + lenis.on('scroll', ScrollTrigger.update)
   • Tier 5 (ScrollSmoother, PAID) → verify license logged in overrides.md; wrap <body> content in #smooth-wrapper > #smooth-content; init ScrollSmoother.create(); apply data-speed/data-lag attrs per plan
3. Route ALL anchor links through chosen scroll API (lenis.scrollTo / ScrollSmoother.scrollTo) — NEVER raw window.scrollTo when Tier ≥ 3
4. Wire modal/dropdown lock pattern (Tier 3-5):
   • MutationObserver on body.scroll-locked class → lenis.stop() / lenis.start()
   • OR imperative pause when modal opens
5. If Tier ≥ 4 and Phase 6 plan included pinned scroll-scrub: emit ScrollTrigger setup per gsap-integration.md
6. If Tier 5: ensure plans/{slug}/overrides.md § ScrollSmoother Escalation log is present with ≥2 criteria + license confirmation
```

**Mandatory guards in emitted code (Tier 3-5):**
- `prefers-reduced-motion: reduce` → early return (no smooth scroll runtime init)
- `(hover: none) and (pointer: coarse)` → early return (touch-primary devices use native momentum)
- `typeof window.Lenis === 'undefined'` (or ScrollSmoother) → early return (CDN/module failure graceful fallback)

**Anti-slop check (v2.5.1+):** Type-aware check.
- **Landing OR Portfolio**: NO vibe check needed — any vibe + any intensity 1-3/3 gets Lenis (Tier 3 or 4). Restraint vibes (editorial / brutalist / minimal / industrial / hand-crafted) on landing/portfolio surfaces also get Lenis — this is intentional per the type-gate rule. No override required.
- **Generic tier**: if selected tier ≥ 3 AND vibe is restraint set, confirm Phase 2e.1 override was logged. Otherwise force back to Tier 1 — dashboards / admin / data-tables benefit from native momentum.

See `smooth-scroll-flow.md` for: full setup code (vanilla + React/Next.js) per tier, anchor link routing patterns, common pitfalls (mobile rubber-band, ScrollTrigger desync, iframe blocking, sticky flicker, tab visibility rAF drain), bundle budget table, ScrollSmoother license + override protocol.

## Step 3 — Mid-implementation spot-checks
After each section completes, verify:
- **Imports list** — any forbidden icon / font library?
- **Color values** — any inline hex outside theme tokens (excluding SVG paths)?
- **Copy** — any "Elevate / Seamless / Unleash / Empower / Game-changer / Next-gen"?
- **Motion** — any `ease-in-out 0.3s` default? Any motion on body `<p>`?
- **Icons** — any emoji used in place of icon?
- **3D** — any `OrbitControls` / `MeshNormalMaterial` / `useGLTF` without override log?
- **Bento Grid (if used)** — `grid-flow-dense` present? No empty cells / voids?

## Step 4 — Commit pattern (when user commits manually)
- One phase = one focused commit (not one mega-commit at end)
- Commit message: conventional commits format (`feat:` / `fix:` / `refactor:` / `docs:` / `chore:`)
- No AI-tool references in commit messages
- Stage files explicitly (no `git add .`) to avoid accidentally committing secrets / build artifacts
- Pre-commit hooks pass (lint, type-check) — never `--no-verify`

## Step 5 — Phase completion check
- Mark phase status `completed` in `phase-XX-*.md`
- Update `plan.md` phase table status
- Update `plan.md` § Success criteria checkboxes
- Notify user phase is done; await confirmation before starting next phase

## Step 6 — Project memory log write (v2.4.0+)

After Phase 7 completes (all plan phases marked `completed`), append a new entry to `.perfect-ui/log.json` at project root. Schema:

```json
{
  "date": "{YYYY-MM-DD}",
  "brief": "{1-line summary from brief.md}",
  "vibe": "{anchor from brief.md}",
  "wildcard": "{adjective from brief.md}",
  "macrostructure": "{name from Phase 2c}",
  "design_variance": {value 1-10 from Phase 2},
  "visual_density": {value 1-10 from Phase 2},
  "motion_personality": "{Playful|Premium|Corporate|Energetic from Phase 2.6}",
  "motion_intensity": {value 0-3 from Phase 2e},
  "illustration_style": "{from 2d-illustration-catalog.md picked at Phase 4}"
}
```

Insert at the FRONT of the JSON array. Trim to last 20 entries (oldest dropped). Create `.perfect-ui/` directory if missing. Suggest adding `.perfect-ui/` to `.gitignore` on first scan (respect existing `.gitignore`).

This entry is what `workflow-brainstorm.md` § Phase 0.5 reads on the NEXT run for diversification enforcement.

## Cross-references

- `workflow-phases.md` — full pipeline navigation
- `workflow-brainstorm.md` — Phase 0.5 reads log.json written by this file
- `workflow-plan.md` — Phase 6 plan protocol (this file consumes plan)
- `workflow-audit.md` — Phase 8 audit protocol
- `motion-patterns.md § Motion Personalities` — Brand Motion Identity used here
- `anti-slop-rules.md` — Honest Copy Mandate + Diversification Rule
- `macrostructure-catalog.md` — macrostructure name + gapless bento mandate
