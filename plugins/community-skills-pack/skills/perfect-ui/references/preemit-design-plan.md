# Pre-emit `<design_plan>` Block (v2.5.0+)

Mandatory pre-emit verification at Phase 7 entry. Forces structured plan output covering 10 verification fields. Catches drift between locked picks (Phases 1-6) and intended implementation (Phase 7) **BEFORE** code emission.

## When this applies

Phase 7 entry — before any code is written, before `phase-XX-*.md` files are populated.

Component-scope runs **MINIMAL subset** (skip macrostructure_diversification / bento_density / label_sweep fields — not applicable).

## 10-field block schema

```yaml
<design_plan>
  macrostructure_diversification:
    last_3: ["Marquee Hero", "Bento Grid", "Long Document"]
    pick: "Manifesto"
    differs_from_last_3: true
    diversification_rule_pass: true

  vibe_validity:
    anchor: "editorial"
    wildcard: "agrarian"
    contradiction: false
    valid: true

  dial_alignment:
    design_variance: 6
    visual_density: 4
    vibe_default_diff: 0
    macrostructure_within_pm_2: true

  motion_personality:
    name: "Premium"
    vibe_default_match: true
    override_logged: false

  hero_math:
    line_range_target: "1-3"
    container_class: "max-w-5xl"
    h1_font_class: "clamp(3rem, 5vw, 5.5rem)"
    projected_lines: 2
    universal_4plus_ban_pass: true

  bento_density:
    applicable: false
    # OR if Bento Grid macrostructure:
    # applicable: true
    # grid_flow_dense: true
    # span_interlock_verified: true

  label_sweep:
    meta_labels_found: 0
    long_document_exception: false
    pass: true

  button_contrast:
    eight_states_planned: ["default", "hover", "focus", "active", "disabled", "loading", "error", "success"]
    focus_ring_visible: true
    contrast_aa_pass: true

  honest_copy:
    fabricated_metrics: 0
    placeholders_required: 3
    em_dash_format: "— metric to confirm"

  gsap_decision:
    intensity: "2/3"
    gsap_needed: false
    skills_route: "n/a"
</design_plan>
```

## Verification logic

At Phase 7 entry:

1. Read locked picks from Phases 1-6 (brief.md, visual-direction.md, plan.md)
2. Populate 10 fields per schema above
3. Run validation per field (see field rules below)
4. **Collect all errors** (not fail-fast — user fixes once)
5. If any FAIL → return user to relevant phase to fix:
   - `macrostructure_diversification` fail → Phase 2.5
   - `vibe_validity` fail → Phase 1
   - `dial_alignment` fail → Phase 2
   - `motion_personality` fail → Phase 2.6
   - `hero_math` fail → Phase 2 + `visual-direction-guide.md § Hero H1 line range`
   - `bento_density` fail → Phase 2.5 or `macrostructure-catalog.md § Bento Grid`
   - `label_sweep` fail → `anti-slop-rules.md § Tier 1 rule #12`
   - `button_contrast` fail → component design (interaction-and-states)
   - `honest_copy` fail → `anti-slop-rules.md § Honest Copy Mandate`
   - `gsap_decision` fail → `gsap-integration.md`
6. If ALL PASS → block stamped, Phase 7 proceeds

## Field validation rules

- **macrostructure_diversification:** read `.perfect-ui/log.json` last 3 entries; pick must NOT match any
- **vibe_validity:** anchor + wildcard must not be obvious contradictions (e.g. "minimal + maximalist")
- **dial_alignment:** dials within ±2 of vibe defaults OR macrostructure adjustment within ±2 OR user override logged
- **motion_personality:** matches per-vibe default OR override logged in `plans/{date}-{slug}/overrides.md`
- **hero_math:** container max-w + H1 clamp() guarantees line_range_target; projected_lines ≤ vibe ceiling
- **bento_density:** if applicable, `grid_flow_dense: true` + span_interlock verified
- **label_sweep:** zero meta-labels OR Long Document macrostructure exception (≤5 ordinal)
- **button_contrast:** 8 states planned + focus ring visible + WCAG AA contrast
- **honest_copy:** zero fabricated metrics (placeholders match metric needs)
- **gsap_decision:** intensity 3/3 OR keyword → gsap_needed true; otherwise false

## Component-scope subset

Component-scope runs **MINIMAL block (4 fields):**

- `vibe_validity`
- `motion_personality`
- `button_contrast` (8 states applicable)
- `honest_copy`

**Skip:** macrostructure_diversification, dial_alignment, hero_math, bento_density, label_sweep, gsap_decision (none apply at component level).

## Stamp placements

Block stamped in **TWO locations**:

1. **CSS comment** at top of generated CSS file (durable record):

```css
/* perfect-ui · <design_plan> v2.5.0
 * (all 10 fields verified — see plans/{slug}/plan.md § Pre-emit verification)
 */
```

2. **plans/{date}-{slug}/plan.md § Pre-emit verification** (audit trail):
   Full 10-field block embedded in plan.md as fenced YAML.

## Cross-references

- `workflow-implement.md § Step 1.5` — pre-emit gate runs here
- `workflow-brainstorm.md` — `vibe_validity` sources from Phase 1 brief
- `visual-direction-guide.md` — `dial_alignment` + `hero_math` field sources
- `motion-patterns.md § Motion Personalities` — `motion_personality` source
- `macrostructure-catalog.md` — `bento_density` source
- `anti-slop-rules.md § Honest Copy Mandate` + `§ Diversification Rule` — `honest_copy` + `macrostructure_diversification` sources
- `gsap-integration.md` — `gsap_decision` source
- `component-scope.md` — subset block applies in component-scope runs
