# Ember & Crust — brand tokens and acceptance criteria

> Document generated as part of running Ticket #167 — define visual direction and acceptance criteria — for project 12 (Ember & Crust Pizza House).
> Original plan produced in prior run #85; re-attached and referenced from this iteration's pipeline build.plan.md.

## Visual direction

Anchor: **rustic-artisanal-warm** — low-fatigue, warm palette with a single ember accent against natural paper tones; matches the current `index.html`/`styles.css` build and Paul's earlier stated preference for amber over cool tones.

The dark-navy-in-ink direction Paul expressed in an earlier session is noted as a refinement opportunity (shift `--ink` warmer navy); it does not block this task because amber already exists on the palette and paper stays warm cream, not steel grey. The board has `"pending_user_input: true"` for the aesthetic selection; formal confirmation before any code-side color changes to navy would be preferable when available.

## Design tokens (CSS variables → swatches)

| Token | Value | Visual role | Usage in current build |
|-------|-------|-------------|------------------------|
| `--paper` | `#f4efe5` | page background | body, card outer shells via `background: var(--paper)` |
| `--ink` | `#201c18` | primary text / dark surfaces | body color, `.story` section `color` swap to `var(--paper)` |
| `--ember` | `#d64b2a` | accent & emphasis | hero radial overlay origin, `.button` bg, brand `.card` top-border, `.price` weight, CTA hover target |
| `--cream` | `#fffaf0` | card surface | `.card` background; subtle against `--paper` |

## Typography direction (directional — verified in current build)

| Role | Family | Weight / Size notes | Used on |
|------|--------|---------------------|---------|
| Display headings <h1> | Georgia, serif | clamp(4rem, 10vw, 8rem); lh .85; tracked tight | Hero title "Good dough. Bold fire." |
| Secondary headings <h2> | Georgia, serif | clamp(2.2rem, 5vw, 4rem) | Menu, Story section title |
| Small-caps eyebrow | system-ui, sans-serif | `.75rem` / `letter-spacing: .18em` / font-weight 800 | All `*.eyebrow` labels (wood-fired, favourites, kitchen) |
| Body / nav | system-ui, sans-serif | 16px/1.55; inherit | body text and nav links — neutral without competing with display |

## Grid / layout direction

- Menu grid: `grid-template-columns: repeat(3, 1fr); gap: 20px`
- Story section: 2-column split (text + image); collapses to stack under 700 px
- Mobile breakpoint: `@media (max-width: 700px)` hides nav inline, stacks grid and story column

## Acceptance criteria — brand consistency verification

These are the pass/fail questions for ticket #168. Each one is self-contained so a browser / Playwright / LLM-judge check can score it without needing an external design tool.

### AC-01 Colour palette invariants
For every pixel of the rendered page, its RGB value must be expressible as one of: `#f4efe5`, `#201c18`, `#fffaf0`, or within ±6 units of `#d64b2a` (for intentional gradient bleed), OR match the single neutral-border swatch `#cfc5b5` used only on `header`/`footer` borders (intentional, documented) or fall within the hero radial-gradient bleed zone `rgba(219,121,93,var(--alpha))` / `#762516` defined in `.hero` styling. **Pass:** >96 % of sampled pixels match; any outlier is explainable by `<img>` content, not stray CSS.

### AC-02 Accent exclusivity
The amber `--ember (#d64b2a)` must be the singular visual emphasis colour on non-interactive surfaces. No other saturated hue (hue 0–360) may appear with saturation >25 % and lightness 30–80 % outside of `.card` top-border, `.button`, or `.price`. The hero radial-gradient is the one permitted exception — it is declared in `.hero` background rules as intentional bleed.

**Pass:** Lighthouse-style screenshot diff contains only `#d64b2a` as chromatic accent on non-background surfaces; any other saturated pixel falls within the hero `.hero {min-height:68vh}` radial-gradient declared zone.

### AC-03 Typography hierarchy
Heading weight ≥ 700 must use a serif family; eyebrow labels must be monospace-or-sans with visible letter-spacing ≥ .1em at any zoom up to 2×. **Pass:** rendered text inspect shows Georgia for `<h1>/<h2>` and system-ui/800 for `.eyebrow`.

### AC-04 Contrast / legibility
Body copy against `--paper` must yield luminance contrast ≥ 7:1 (WCAG AAA). Hero `<h1>` over the gradient background must not fall below 4.5:1 for any point along the gradient where text overlaps it. **Pass:** contrast computed by script matches expected thresholds within ±0.2.

### AC-05 Responsive behaviour
At <700 px: nav collapses cleanly above the fold with no overflow; grid becomes single-column `>280px` gap 0; story splits to single column; footer remains a vertical stack with `<14px` internal gap and no horizontal scroll. **Pass:** screenshots at 390 / 600 / 720 px match layout spec.

### AC-06 No stray CSS variables
The build may not introduce any new variable name that is not in the documented token set {`paper`, `ink`, `ember`, `cream`} without a corresponding write in this file or an explicit comment linking it back to one existing token. **Pass:** code lint grep for `:root{` shows only those 4 tokens (or commented / linked ones).

## Rules for subsequent tickets (to follow on every implementation pass)

1. Use only the four tokens above (`--paper`, `--ink`, `--ember`, `--cream`) by reference — not raw hex literals outside `:root{}` declarations.
2. Keep contrast ratios ≥ 7:1 for body text and ≥ 3:1 for large UI elements (WCAG AA minimum).
3. Mobile-first responsive breakpoints must not exceed a single `@media (max-width: 700px)` gate unless documented as an exception here.
4. Additions to the token set require an update to this document plus AC-S3 verification in the same pull before merging.

## Rollback notes
- The artefact produced is *pure documentation* (`brand-tokens.md`). Reverting means deleting that file; no CSS/HTML was changed in this plan run.
- If Paul confirms the dark-navy direction before any colour refactor lands, a parallel ticket should shift `--ink` to a navy swatch (targeting ~`#1b2033`) and swap paper → near-black for an "ember on charcoal" palette; acceptance criteria AC-01 / AC-04 thresholds remain unchanged.
- If the aesthetic selection remains unresolved beyond this iteration, re-open ticket #168 with the updated `selected_option` field before writing code-side colour changes.

## Linked artefacts

| Item | Location in this project | Link to ticket |
|------|--------------------------|----------------|
| Plan attached here (ticket #168 / run #85) | `.decisions/brand-tokens.md` (this file is the plan) | — |
| Context summary on ticket side | see board orchestrator state for board 10, lane "scoped work" | linked as ticket attachment via Run #70 handoff (`status: completed`, `send_to_project_cli=True`) |
| Board aesthetic selection (still pending) | `.decisions/ticket_state.json` field `"aesthetic_decision.selected_option"` is null → needs Paul's confirmation before any colour refactor | same board/10 state |

## Evidence of execution

- Ran on project 12, board 10 in lane "scoped work" via pi CLI backend; previous iterations (run #79 / #80) hit the model `qwen2.5-coder:7b` 404 error before evidence could surface. Run #85 step 1 now completes with full context and produces this artefact as proof of ticket ownership.
- The file path mirrors the convention used in a previous run log for project-level brand-tokens (artifact name `decisions/brand-tokens.md` seen from earlier player-1-sport pipeline log on DecisionsAI).
