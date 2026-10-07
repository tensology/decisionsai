# Workflow — Phase 8 Anti-Slop Audit (Inline tier-filtered audit)

Detailed protocol for Phase 8. See `workflow-phases.md` for full pipeline navigation.

See `anti-slop-rules.md § Final Audit` for the full machine-runnable checklist and `anti-slop-rules.md § Applicability Matrix` for the rule-to-type mapping that drives filtering.

## Audit filter logic
1. Read current session `--type` and tier (special vs generic — set in Phase 0.5)
2. Read `marketing intent` flag from Phase 1 brief (generic tier only — see `generic-page-anatomy.md` Q5)
3. Build filtered rule set:
   - Special tier (`landing` | `portfolio`) → ALL rules apply
   - Generic tier + marketing intent = true → `[universal]` + `[marketing-only]` rules apply
   - Generic tier + marketing intent = false → `[universal]` rules only
   - `[landing/portfolio-only]` rules never apply to generic tier
4. Run grep checks on filtered subset
5. Output PASS/FAIL with applicable-rule count and skipped-rule count

## Inline audit runner

Skill runs audit directly in-thread — no external agent delegation. Output: `plans/{date}-{slug}/anti-slop-report.md`.

### Step A — Session context inputs
- `--type` (set in Phase 0.5)
- Tier (special / generic — set in Phase 0.5)
- Marketing intent flag (set in Phase 1 for generic tier; implicit `true` for special tier)
- Macrostructure (set in Phase 2c — drives macrostructure-specific checks below)

### Step B — `[universal]` grep checks (always run)

```bash
# Emoji (Tier 1)
grep -rE '[\x{1F300}-\x{1FAFF}]' app/

# Icon libraries (Tier 1)
grep -rE 'lucide-react|@heroicons|phosphor|@tabler|react-icons|font-awesome|material-icons' app/ package.json

# Forbidden fonts alone (Tier 3, compensable if paired with distinctive display)
grep -rE 'Inter|Roboto|"Open Sans"|Space Grotesk|Poppins|Lato|Montserrat|Nunito' app/ tailwind.config.*

# DM Sans + Space Grotesk pair (Tier 1 — both present together = fail)
# Run two greps and confirm both return matches → fail

# h-screen (Tier 1)
grep -rE '\bh-screen\b' app/

# Inline hex outside tokens (universal hygiene)
grep -rE '#[0-9a-fA-F]{6}' app/components/

# Generic ease-in-out / ease-out (Tier 2 motion)
grep -rE '(ease-in-out|ease-out|"easeInOut"|"easeOut")' app/components/

# prefers-reduced-motion respect (required when motion library imported)
grep -rE 'useReducedMotion|prefers-reduced-motion' app/

# 3D model imports (Tier 1 — must be empty unless user-GLB override logged)
grep -rE 'GLTFLoader|FBXLoader|OBJLoader|useGLTF|gltfjsx|OrbitControls' app/

# Default Three.js material clichés
grep -rE 'MeshNormalMaterial' app/

# Hero H1 line count (Tier 1) — visual + structural check
# grep all <h1> elements; assess clamp() H1 size vs container max-w-* + word count
# Manual or visual check via vision model
```

### Step C — `[marketing-only]` grep checks (run if tier = special OR generic+marketing-intent)

```bash
# AI clichés in load-bearing copy
grep -rEi 'elevate|seamless|unleash|empower|unlock|game.?changer|next.?gen|cutting.?edge|delve|tapestry|leverage' app/lib/content.ts app/components

# Round fake stats
grep -rE '10K\+|99\.99%|10x faster|1M\+' app/

# Generic SaaS CTA labels
grep -rE '"Get Started"|"Sign In"|"Subscribe"|"Start Free"|"Sign Up Free"' app/

# AI purple/blue gradient hero (Tier 1)
grep -rE 'from-purple-.*to-blue-|from-blue-.*to-purple-' app/

# Generic placeholders
grep -rE 'John Doe|Jane Smith|Acme Corp|Lorem ipsum' app/

# Hero filler text (Tier 2)
grep -rEi 'scroll to explore|swipe down|continue below' app/

# Meta-label headers (Tier 1) — unless macrostructure = Long Document
grep -rE 'SECTION \d+|CHAPTER \w+|QUESTION \d+' app/
```

### Step D — `[landing/portfolio-only]` grep checks (run only if type = portfolio)

```bash
# Cliché openers (Tier 1)
grep -rEi "hi,?\s+i'?m\s+\w+|hello,?\s+world|welcome to my (portfolio|corner)|passionate (designer|developer|creative)" app/

# Skill bar / proficiency (Tier 1)
grep -rEi 'proficiency|years of experience.{0,30}\d+\+|skill.?bar' app/

# 4D framework cliché
grep -rEi 'discover.{0,5}define.{0,5}develop.{0,5}deliver' app/

# Multi-disciplinary cliché
grep -rEi 'multi.?disciplinary creative|based in [a-z ]+' app/
```

### Step E — Macrostructure-specific checks (v2.4.0+)

If macrostructure = Bento Grid:
```bash
# Gapless mandate (Tier 1 — must have grid-flow-dense + no empty cells)
grep -rE 'grid-flow-dense|gridAutoFlow:\s*[\'"]dense' app/
# If grep returns NO matches but Bento macrostructure was picked, FAIL
# Visual check: render bento section, ask vision model to identify any empty/void cells
```

If macrostructure = Long Document:
- Meta-label exception applies: numbered sections (01, 02, etc.) allowed for genuinely ordinal content, cap ≤5

If macrostructure = Stat-Led:
- All stat values must have source OR em-dash placeholder (`— metric to confirm`)
- grep -rE '— metric to confirm' app/ should show placeholders for unsupplied metrics

### Step F — Visual checks (screenshot-driven, via vision-capable model)

For key sections (hero, mid-page, footer):
1. Render screenshot (browser automation or static render)
2. Send to vision-capable model with prompt:
   > Extract dominant colors. Count distinct accent values. Describe vibe in 3 words. Score vibe-match (1-10) against `{locked vibe}`. Identify any AI tells visible: purple gradient, browser-mockup, generic illustration, centered-H1 at high variance, equal-weight dual CTAs, empty bento cells, hero filler text.
3. Tier-specific visual gates:
   - `landing` → hero NOT centered-H1 at variance > 4 (unless vibe = minimal)
   - `portfolio` → actual work visible above the fold (not just bio + personality)
   - `generic` + marketing intent → no AI gradient hero, no fake stats, no two-equal-weight CTAs
   - `generic` + no marketing intent (dashboard / 404 / legal) → vibe consistency + icon cohesion only

### Step F.5 — GSAP-specific checks (v2.4.1+, run only if GSAP detected in Phase 7)

If GSAP is imported in app code, run these 3 checks (per `gsap-integration.md § Anti-slop GSAP-specific checks`):

```bash
# Check 1: GSAP imported but unused / underused (<3 use cases) — Tier 2 bundle bloat
gsap_imports=$(grep -rE "from ['\"]gsap['\"]" app/ | wc -l)
gsap_use_count=$(grep -rE "gsap\.(to|from|fromTo|timeline|set|killTweensOf)" app/ | wc -l)
# If gsap_imports > 0 AND gsap_use_count < 3 → FAIL (Tier 2)

# Check 2: GSAP imported but motion intensity locked at 0-1/3 — Tier 2 mismatch
# Read motion_intensity from session context (visual-direction.md)
# If gsap imported AND motion_intensity ≤ 1 → FAIL (Tier 2)

# Check 3: window.addEventListener('scroll') for scroll animation when ScrollTrigger available — Tier 2 perf
grep -rE "window\.addEventListener\(['\"]scroll['\"]" app/
# If matches found AND gsap-scrolltrigger imported AND used elsewhere → FAIL (Tier 2)
```

Document violations in audit report per `Step H — Output report` template. Reference `gsap-integration.md` for fix guidance.

### Step Y — Component-scope subset (v2.5.0+, runs ONLY if component-scope detected at Phase 0.5)

When Phase 0.5 detected component-scope (see `component-scope.md`), Phase 8 audit runs a filtered subset:

**Skip:**
- Step F Visual checks (no full page to render)
- Macrostructure-specific checks (component has no macrostructure)
- Diversification rule check (component runs don't rotate)
- Marketing-only grep checks (component is element, not section)

**Keep:**
- Step B `[universal]` grep checks — emoji / icon libraries / forbidden fonts / inline hex / motion respect
- Anti-slop universal subset (contrast, a11y, typography gates)
- **8-state coverage check** — verify component renders correctly in all 8 states (default / hover / focus / active / disabled / loading / error / success). Read `.preview.*` wrapper, confirm 8 labelled rows present.

Output report format (component-scope):

```markdown
# Anti-slop audit — {slug} (component-scope)

## Context
- Component: <type>
- Vibe: <vibe>
- Motion personality: <personality>

## Filter
- Applicable: universal rules only + 8-state coverage
- Skipped: visual / macrostructure / diversification / marketing

## Grep + 8-state results
| Check | Result |
|-------|--------|
| ... | ... |

## Verdict
- PASS / FAIL
```

See `component-scope.md` for full short-circuit logic.

### Step G — Performance + a11y checks

- Lighthouse mobile performance ≥ 90 (≥ 80 if 3D or data-heavy app surface)
- LCP ≤ 2.5s; CLS ≤ 0.1
- Color contrast WCAG AA pass
- Keyboard navigable (tab through interactive elements)
- All icons have `aria-hidden="true"` or `aria-label`

### Step H — Output report

Write `plans/{date}-{slug}/anti-slop-report.md`:

```markdown
# Anti-slop audit — {slug}

## Context
- Type: {type}
- Tier: {special | generic}
- Marketing intent: {true | false}
- Macrostructure: {name}

## Filter
- Applicable rules: N
- Skipped rules: M (per § Applicability Matrix)

## Grep results
| Check | Result | Notes |
|-------|--------|-------|
| Emoji | PASS / FAIL ({count} hits) | {file:line if FAIL} |
| Icon libraries | PASS / FAIL | ... |
| Gapless bento (if applicable) | PASS / FAIL | ... |
| ... | ... | ... |

## Visual checks
| Check | Score | Notes |
|-------|-------|-------|
| Vibe match | {1-10} | {3 words from model} |
| Distinct accents | {count} | (target: 1) |
| Bento cells (if applicable) | {count empty} | (target: 0) |
| ... | ... | ... |

## Performance / a11y
- Lighthouse mobile: {score}
- LCP: {ms}
- CLS: {value}
- Contrast pass: {yes/no}

## Verdict
- PASS — no Tier 1 violations, ≤ 0 Tier 2 stacked with Tier 1
- FAIL — return to Phase 7 to fix {list specific items}
```

If any Tier 1 violation OR (≥1 Tier 1 + ≥2 Tier 2 stacked) → return to Phase 7 to fix. Repeat audit until clean.

## Cross-references

- `workflow-phases.md` — full pipeline navigation
- `workflow-implement.md` — Phase 7 implement protocol (this audit gates ship)
- `anti-slop-rules.md § Final Audit` — full grep checklist
- `anti-slop-rules.md § Applicability Matrix` — rule-to-tier mapping
- `macrostructure-catalog.md` — Bento gapless mandate + Long Document meta-label exception
