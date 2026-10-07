# Macrostructure Catalog

Page-shape archetypes — independent of vibe. Vibe locks visual identity (palette + typography + spatial language); macrostructure locks page rhythm (section sequence + page chrome density + hero pattern).

Same macrostructure with different vibes = different feel. 7 macrostructures × 11 vibes = 77 valid combinations. Diversification rule prevents repetition across runs in same project.

## When to pick a macrostructure (Phase 2c)

After vibe is locked (Phase 1) and palette + typography are chosen (Phase 2a-2b), pick macrostructure at Phase 2c. The macrostructure choice drives:

- Hero archetype (see `assets/nextjs-skeleton/section-archetypes.md` § Hero Archetypes)
- Section rhythm (which sections, what order)
- DESIGN_VARIANCE + VISUAL_DENSITY default adjustment (±2 from vibe default in `visual-direction-guide.md`)

Diversification rule (hard): macrostructure pick must NOT match any of the last 3 entries in `.perfect-ui/log.json`.

## The 7 macrostructures

### 1. Marquee Hero
- **Hero archetypes:** A1 (Editorial Asymmetric 60/40) | A5 (Full-bleed declarative)
- **Section rhythm:** Hero → social-proof → 2-3 features → CTA → footer
- **Best for:** brand statements, declarative launches, single-product landings
- **Dial defaults:** DESIGN_VARIANCE 7, VISUAL_DENSITY 3
- **Diversification axes:** paper (any), accent (any), display style (any)
- **AI tell to avoid:** 5-section equal-weight feature cards underneath the hero (use 2-3 max, asymmetric weights)

### 2. Bento Grid
- **Hero archetypes:** A6 (Mini hero + bento canvas) | A7 (No hero, full-bleed grid)
- **Section rhythm:** Mini hero → 6-12 cell bento → CTA → footer
- **Best for:** SaaS feature showcase, modular product, integration ecosystems
- **Dial defaults:** DESIGN_VARIANCE 7, VISUAL_DENSITY 6
- **Gapless mandate (Tier 1 anti-slop):** `grid-flow-dense` is MANDATORY on all bento grids. Mathematical verification: every col-span/row-span value must interlock with neighbors — no missing corners, no voids, no empty cells. Empty cells = templated AI feel. See `anti-slop-rules.md` Tier 1 rule #13.
- **Diversification axes:** cell count (6-8 vs 10-12), accent corner (top-left vs bottom-right), span pattern
- **AI tell to avoid:** 3×3 perfect symmetric grid (use 8-cell asymmetric mix with varied col-span / row-span values)

### 3. Long Document
- **Hero archetypes:** A2 (Minimal Centered) | A8 (Editorial spread) | A9 (Number + headline)
- **Section rhythm:** Editorial hero → numbered section 01-05 → CTA → footer
- **Best for:** case studies, manifestos, deep narratives, editorial-led pages
- **Dial defaults:** DESIGN_VARIANCE 3, VISUAL_DENSITY 3
- **Meta-label ban exception:** numbered sections ALLOWED in this macrostructure (universal meta-label ban — `anti-slop-rules.md` Tier 1 rule #12 — is relaxed for genuinely ordinal Long Document content). Cap at ≤5 numbered sections.
- **Diversification axes:** display style (serif vs sans), accent hue (warm vs cool), section count
- **AI tell to avoid:** numbered sections without actual ordinal content (decorative numbers are slop)

### 4. Manifesto
- **Hero archetypes:** A10 (Massive typography only) | A11 (Statement + signature)
- **Section rhythm:** Statement → quote chain → small footer
- **Best for:** brand identity, mission statement, atelier sites, foundry-adjacent
- **Dial defaults:** DESIGN_VARIANCE 9, VISUAL_DENSITY 2
- **Diversification axes:** any vibe, any paper, any accent
- **AI tell to avoid:** centered statement at variance < 4 (defeats macrostructure point — Manifesto demands commitment)

### 5. Stat-Led
- **Hero archetypes:** A12 (Number-led headline) | A13 (Proof bar above hero)
- **Section rhythm:** Stat hero → logos → 3-4 stat cards → testimonial → CTA → footer
- **Best for:** B2B SaaS proof-heavy landings, fintech, enterprise marketing
- **Dial defaults:** DESIGN_VARIANCE 5, VISUAL_DENSITY 5
- **Honest copy enforcement (CRITICAL):** every stat MUST have source OR em-dash placeholder (`— metric to confirm`). Fabricated stats = immediate fail. See `anti-slop-rules.md § Honest Copy Mandate` for the 3 accepted paths.
- **Diversification axes:** logo bar position (top vs after hero), card layout (3-col vs 2x2), stat presentation (large vs compact)
- **AI tell to avoid:** round-fake stats (99.99% / 10x faster / 1M+ users) — these auto-fail at Phase 8

### 6. Workbench
- **Hero archetypes:** A7 (No hero, full-bleed) | A14 (Compact toolbar hero)
- **Section rhythm:** Tool surface → sidebar nav → main content area → optional bottom bar
- **Best for:** app surfaces (dashboards, admin panels) — generic tier only
- **Tier constraint:** NOT for landing/portfolio (special tier). Generic tier appropriate when `--type=dashboard|admin|app|e-commerce`.
- **Dial defaults:** DESIGN_VARIANCE 4, VISUAL_DENSITY 7
- **Diversification axes:** sidebar position (left vs right vs collapsible), top bar density (compact vs feature-rich)
- **AI tell to avoid:** sidebar + cards + breadcrumbs + top bar all at once = chrome overload (pick one organizing principle, commit)

### 7. Letter
- **Hero archetypes:** A11 (Statement + signature) | A15 (Correspondence opener) | A16 (Handwritten note + sketch)
- **Section rhythm:** Letter opening → body paragraphs → signature → contact
- **Best for:** about pages, founder letters, personal portfolios variant, brand stories
- **Dial defaults:** DESIGN_VARIANCE 6, VISUAL_DENSITY 3
- **Diversification axes:** opener style (formal "Dear" vs casual "Hi"), signature placement (right vs centered), body width (narrow vs full)
- **AI tell to avoid:** "Hi I'm a passionate designer who loves coffee" — see `portfolio-anatomy.md` anti-clichés. Use real, specific opener tied to the writer.

## Macrostructure × Vibe interaction

Same macrostructure with different vibe = different feel. Three illustrative combinations:

- **Bento Grid + Editorial vibe** = magazine-spread bento with serif display, asymmetric column weights, paper textures, generous gutter
- **Bento Grid + Brutalist vibe** = monolithic cells with hard divider lines (`border-2`), no shadows, sharp corners, monospace numerics
- **Bento Grid + Glass-tech vibe** = refractive glass cells with `backdrop-blur`, subtle inner shadows, hairline borders, OKLCH accent

Vibe locks the SURFACE; macrostructure locks the SHAPE. They combine multiplicatively across 7 × 11 = 77 valid pairs.

## Diversification rule

Read `.perfect-ui/log.json` at Phase 0.5 (see `workflow-brainstorm.md` § Phase 0.5 — log read).

**Hard rule:** macrostructure pick must NOT match any of the last 3 entries. If user demands the same macrostructure for a 4th consecutive run, log override in `plans/{date}-{slug}/overrides.md` with explicit user reason.

**Soft rules (warning, override allowed without log):**
- Vibe + wildcard combo same as last entry (warn)
- DESIGN_VARIANCE OR VISUAL_DENSITY same as last (warn — at least one dial should differ ≥3 points)
- Motion personality same as last (warn)

State diversification check verbosely at Phase 2c, before user picks:

> "Last 3 macrostructures: Marquee Hero (Tracejam, editorial+warm) · Bento Grid (Foundry, glass-tech) · Long Document (Maple, editorial+cool). Picking Marquee Hero again would violate the hard rule — choosing from {Manifesto, Stat-Led, Workbench, Letter} this time."

## Cross-references

- Hero archetypes: `assets/nextjs-skeleton/section-archetypes.md` § Hero Archetypes (A1-A16)
- Dial defaults (DESIGN_VARIANCE + VISUAL_DENSITY): `visual-direction-guide.md` § Two dials
- Anti-slop rules (Gapless bento Tier 1, Honest copy, Meta-label ban exception for Long Document): `anti-slop-rules.md`
- Phase 2c workflow integration: `workflow-phases.md` § Phase 2c + `workflow-brainstorm.md` § Phase 0.5
- Project memory log: `.perfect-ui/log.json` (schema in `workflow-brainstorm.md` § Phase 0.5)
