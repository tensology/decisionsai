# Workflow — Phase 1 Discovery (Inline brainstorm protocol)

Detailed protocol for Phase 1. See `workflow-phases.md` for full pipeline navigation.

Skill conducts brainstorm directly via `AskUserQuestion`. Output: `plans/{date}-{slug}/brief.md`.

## Phase 0.5 — Read project memory (log.json)

Before Phase 1 brief, read `.perfect-ui/log.json` at project root if it exists. Schema:

```json
[
  {
    "date": "2026-05-28",
    "brief": "Specialty coffee subscription — Tokyo home-brew",
    "vibe": "editorial",
    "wildcard": "agrarian",
    "macrostructure": "Marquee Hero",
    "design_variance": 6,
    "visual_density": 3,
    "motion_personality": "Premium",
    "motion_intensity": 2,
    "illustration_style": "silkscreen"
  },
  {...}
]
```

20-entry rolling buffer (oldest dropped). Read at Phase 0.5 (this file). Written at Phase 7-end (see `workflow-implement.md`).

Surface diversification check as one-line summary at end of Phase 0.5:

> "Last 3 builds: Marquee Hero (Tracejam, editorial+warm) · Long Document (Maple, editorial+cool) · Bento Grid (Foundry, glass-tech).
> Diversification rule: macrostructure must NOT be {Marquee Hero, Long Document, Bento Grid}. Choosing from {Manifesto, Stat-Led, Workbench, Letter} this time."

If log.json doesn't exist, silent (first build for this project). Create on first Phase 7 completion.

See `macrostructure-catalog.md § Diversification rule` for full hard + soft rules.

## Studied-DNA input mode (NEW v2.5.0)

When `--study <URL>` ran and produced extracted DNA (see `study-mode.md`), Phase 1 brief uses extracted DNA as **LOCKED inputs** — skip vibe / palette / typography questions (already locked from the studied source).

User still answers:

- Audience
- Use case
- Tone
- Macrostructure pick uses extracted macrostructure as default (can override)

**Diversification rule SUSPENDED** for studied-DNA runs (`.perfect-ui/log.json` entry records `theme: studied-DNA`).

If user pivots ("use Linen theme instead" / "ignore the DNA"), route back to normal Phase 1 questions; diversification resumes.

## Step 1 — Scope sanity check
- If user request describes 3+ independent concerns (e.g. "build a landing + dashboard + admin"), flag for decomposition before continuing. Each becomes its own brief → plan → implement cycle.
- If trivial (single-section update, copy tweak, color swap), produce a 5-line brief inline and skip the approval gate.

## Step 2 — Question script (branched by type)

Use `AskUserQuestion` in this order. Lock each answer before asking next.

### If type = landing
1. **Product** — one sentence: what + who + why now
2. **Audience** — specific role (e.g. "freelance designer earning $80k+ who codes side projects" — NOT "everyone" / "users")
3. **Conversion goal** — single CTA destination: signup / demo / buy / waitlist / contact
4. **Vibe anchor** — pick 1 of: minimal | editorial | brutalist | retro-futuristic | organic | luxury | playful | industrial | art-deco | glass-tech | hand-crafted
5. **Wildcard adjective** — 1 word the brand owns (e.g. "agrarian", "harsh", "tender")
6. **Inspirations** — 3 reference URLs (real, current)
7. **Anti-references** — 2 landings to avoid
8. **Constraints** — technical / deadline / budget

### If type = portfolio
1. **Owner one-liner** — you + craft, plainly stated. NOT cute.
2. **Audience** — specific: hiring managers at tech cos / agency clients / freelance leads / fellow craft community
3. **Single goal** — hire me / book a call / freelance inquiry / "available from {date}"
4. **Work focus** — project types featured + count: 4 / 6 / 8 / 12
5. **Case study depth** — gallery thumbnails | 1-2 deep dives | hybrid (drives Phase 6 plan complexity)
6. **Vibe anchor** — same 11-option list as landing
7. **Wildcard adjective** — 1 word tied to your craft
8. **Inspirations** — 3 portfolio URLs you admire
9. **Anti-references** — 2 portfolio styles to avoid (e.g. "no hover-overload bento grids")
10. **Constraints**

### If tier = generic (any other type)
1. **Page purpose** — pick ONE: inform / convert / navigate / display data / collect input / tell a story
2. **Marketing intent flag** — true / false (drives which anti-slop rules apply in Phase 8)
3. **Audience** + **primary action** (may be "none" for legal / 404)
4. **Vibe anchor** + **wildcard adjective** (same 11-option list)
5. **Inspirations** — 3 URLs (or 2 + 1 visual-style reference)
6. **Anti-references** — 2 to avoid
7. **Constraints** — technical / deadline / chrome density

## Step 3 — Write brief.md

Output `plans/{date}-{slug}/brief.md`:

```markdown
# Brief — {slug}

## Type & tier
- Type: {landing | portfolio | blog | pricing | dashboard | ...}
- Tier: {special | generic}
- Marketing intent: {true | false}  # generic tier only

## Audience
{specific role}

## Goal / primary action
{single CTA destination, or "none" for informational}

## Vibe
- Anchor: {one of 11}
- Wildcard: {adjective}

## Inspirations
1. {URL}
2. {URL}
3. {URL}

## Anti-references
1. {URL or pattern}
2. {URL or pattern}

## Constraints
{technical / deadline / budget / chrome density}

## (generic tier only) Page-Purpose Exercise
- Job: {inform / convert / navigate / display / collect / story}
- Success: {metric or qualitative}
- Primary action: {action, or "none"}
```

## Step 4 — Approval gate
User reviews `brief.md`. Skill does NOT propose colors, fonts, or copy yet. Only proceed to Phase 2 once user explicitly approves.

## Step 5 — Forbidden brief patterns (auto-refuse, ask user to refine)
- "Build a website" without audience or conversion goal → push back, ask for specifics
- Generic vibe ("modern", "clean", "professional") without anchor → push back, force pick from 11
- 3 inspirations all from the same era or aesthetic → ask for variety (at least 1 wildcard reference)
- Vibe + wildcard that obviously contradict (e.g. "minimal + maximalist") → ask user to reconcile

## Quality bar (all tiers)
Brief is approved only when:
- [ ] Audience is specific
- [ ] Goal locked (one destination, or explicitly "none" for informational)
- [ ] Vibe is exactly 1 anchor + 1 wildcard (not a list)
- [ ] 3 real inspiration URLs (or ≥2 + visual-style reference)

## Portfolio-specific extra quality checks
- [ ] Work focus is specific ("brand identity for early-stage tech" not "design")
- [ ] Case study depth chosen
- [ ] Anti-references include hover-overload / "Hi I'm passionate" if user mentioned similar issues

## Cross-references

- `workflow-phases.md` — full pipeline navigation
- `workflow-plan.md` — Phase 6 plan protocol
- `workflow-implement.md` — Phase 7 implement protocol (writes log.json)
- `workflow-audit.md` — Phase 8 audit protocol
- `macrostructure-catalog.md` — Phase 2c macrostructure pick
- `anti-slop-rules.md § Diversification Rule` — cross-run rules
