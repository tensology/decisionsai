# Workflow Phases — Detailed Walkthrough

Self-contained 8-phase pipeline. The skill conducts every phase directly — no external orchestration skills are invoked. Phases branch by `--type` and tier (special / generic). For asset generation, the skill describes the *capability* required and uses whichever tool fits (text-to-image, vision-capable analysis, vector trace, React Three Fiber, etc.).

## Phase navigation

Detailed protocols for Phase 1, 6, 7, 8 live in dedicated sub-references:
- **Phase 1 (Discovery)** → `workflow-brainstorm.md`
- **Phase 6 (Plan)** → `workflow-plan.md`
- **Phase 7 (Implement)** → `workflow-implement.md`
- **Phase 8 (Audit)** → `workflow-audit.md`

This main file documents Phase 0, 0.1, 0.5, 2 (all sub-steps including new 2c + 2.6), 3, 4, 5.

## Phase 0 — Mode Detection

### Detection rules
```
if user supplies URL → redesign
elif user attaches screenshot/image of existing site → redesign
elif user says "redesign" / "rework" / "v2" / "iterate" → redesign
elif user supplies repo path with existing site → redesign
elif user describes content with no existing site → new
else → AskUserQuestion to disambiguate
```

### For redesign mode (extra step before Phase 0.5)
Run audit per `redesign-audit-checklist.md`. Output: `plans/{date}-{slug}/audit.md` with sections:
- **Current vibe** (one sentence)
- **Detected type** (landing | portfolio — informs Phase 0.5)
- **Keep** (3 elements that work)
- **Kill** (5 elements that don't)
- **Original conversion goal** (inferred)
- **Why redesign now** (user's stated reason)

Then proceed to Phase 0.5 with audit context attached.

### `--study <URL>` mode (NEW v2.5.0)

If `--study <URL>` flag passed OR user provides URL with intent keywords ("study this", "extract DNA", "use as reference"), enter Study Mode.

Routing:

1. Phase 0 detects `--study` mode
2. Run `study-mode.md § URL safety` refusal check
3. If passes: run `study-mode.md § URL fetch` + `§ DNA extraction`
4. Emit diagnosis report per `study-mode.md § Diagnosis report format`
5. Wait for user response (3-way branch):
   - "Build with this DNA" → continue to Phase 0.5 with extracted DNA as locked inputs
   - "Lock the DNA" → emit `design.md`, end session
   - Silence / "stop" → diagnosis IS deliverable, end session
6. If "build with DNA" → Phase 0.5 uses Studied-DNA input mode (`workflow-brainstorm.md § Studied-DNA input mode`); diversification suspended

See `study-mode.md` for full protocol.

---

## Phase 0.1 — Pre-flight scan (auto-detect)

See `preflight-scan.md` for full protocol. Auto-detect logic:

- If target directory has existing project files (`package.json` / `tailwind.config.*` / framework configs / `*.css` / `index.html`) → run scan, emit findings block before Phase 0.5
- If empty repo → silent, one-line note (`Pre-flight: no signals`), proceed to Phase 0.5
- Cache findings in `.perfect-ui/preflight.json` at project root; reuse unless user requests `refresh pre-flight` OR config mtimes are newer than cache

Preserved tokens / fonts / motion library are carried into Phase 2 visual direction dialog. perfect-ui only introduces what's missing. If user explicitly says `ignore existing project` / `fresh start`, skip scan and proceed.

For redesign mode (Phase 0 set `redesign`), pre-flight runs IN ADDITION to `redesign-audit-checklist.md` — preflight scans tokens, audit assesses visual design.

---

## Phase 0.5 — Type Detection (No Refusals)

Two tiers based on `--type`:
- **Special tier** — `landing` | `portfolio`. Rich anatomy + skeleton + section archetypes per vibe + full anti-slop audit.
- **Generic tier** — any other type. Generic anatomy + generic skeleton + universal anti-slop subset.

### Step 1 — Parse `--type` flag
If `--type` is passed, accept any string. No validation against an enum:
- `landing` | `portfolio` → special tier
- anything else → generic tier (carry the supplied string as type name)

### Step 2 — Auto-detect from input description (when no flag)
Match keywords (case-insensitive). First match wins:

```
landing | marketing page | sales page | hero page | funnel | conversion → landing (special)
portfolio | hire-me page | work showcase | personal site | hire me      → portfolio (special)
blog | article | post                                                    → blog (generic)
about | team | company info                                              → about (generic)
pricing | plans | tiers                                                  → pricing (generic)
contact | reach out | get in touch                                       → contact (generic)
coming soon | waitlist | early access                                    → coming-soon (generic)
404 | error page | not found                                             → error-page (generic)
legal | terms | privacy                                                  → legal (generic)
dashboard | admin panel | admin dashboard | internal tool                → dashboard (generic) ⚠ disclose
e-commerce | storefront | product catalog | online store | shop          → e-commerce (generic) ⚠ disclose
full app | SaaS app | user dashboard                                     → app (generic) ⚠ disclose
```

Other matches → use the matched keyword as type name in generic tier.

### Step 3 — AskUserQuestion fallback (when no flag and no detection)
```
Header: "Page Type"
Question: "What type of page are you designing?"
Options:
- Landing page (marketing / conversion)
- Portfolio / personal site
- Blog / article
- About / team page
- Pricing page
- Contact / coming-soon / waitlist
- Dashboard / admin
- E-commerce / store
- Other (free text → generic tier)
```

### Step 4 — Tier routing
- **Special tier** (`landing` | `portfolio`):
  - Phase 1: type-specific brief template (landing or portfolio variant below)
  - Phase 6: type-specific plan template (landing or portfolio output below)
  - Phase 7: `assets/nextjs-skeleton/landing-skeleton.md` or `portfolio-skeleton.md`
  - Phase 8: full anti-slop audit — all rules per § Applicability Matrix apply
- **Generic tier** (any other type):
  - Phase 1: generic brief template (vibe / inspirations + Page-Purpose Exercise from `generic-page-anatomy.md`)
  - Phase 6: generic plan template (sections driven by page-purpose, not template — see Phase 6 § Generic tier plan output below)
  - Phase 7: `assets/nextjs-skeleton/generic-page-skeleton.md`
  - Phase 8: filtered audit per § Applicability Matrix — `[universal]` rules + `[marketing-only]` rules only when marketing intent = true

### Step 5 — Evidence-base disclosure (generic tier, app-surface types)
When detected type ∈ {`dashboard`, `admin`, `e-commerce`, `app`} OR user-supplied free text suggests app surface, log this notice once at start of session:

> Note: skill's evidence base (12 marketing landings analyzed — see `plans/260509-ai-vs-human-analysis/synthesis.md`) does NOT cover dashboard / admin / e-commerce / app-surface patterns directly. Universal craft toolkit (vibe lock, custom icons, motion rules, anti-slop universal subset) still applies. Output quality is best-effort, not evidence-backed.

### Carry type and intent into all downstream phases
Type, tier, AND the `marketing intent` flag from generic-anatomy Page-Purpose Exercise (Q5) are carried into every subsequent phase prompt. Phase 8 uses the intent flag to decide whether `[marketing-only]` rules apply for generic-tier pages.

### Step 6 — Read project memory log (NEW v2.4.0)

Read `.perfect-ui/log.json` at project root if it exists. Schema documented in `workflow-brainstorm.md § Phase 0.5`.

Surface diversification check as one-line summary before proceeding to Phase 1:

> "Last 3 builds: Marquee Hero (Tracejam, editorial+warm) · Long Document (Maple, editorial+cool) · Bento Grid (Foundry, glass-tech).
> Diversification rule: macrostructure must NOT be {Marquee Hero, Long Document, Bento Grid}.
> Vibe / dials / motion personality should differ from last entry (soft warnings)."

If log.json doesn't exist, silent (first build for this project). Will be created at Phase 7-end.

See `macrostructure-catalog.md § Diversification rule` for full hard + soft rules.

### Step 7 — Component-scope detection (NEW v2.5.0)

After type detection, check component-scope signals (per `component-scope.md § When this applies`):

1. Multi-signal check: brief ≤30 words + UI element keyword + `--component` flag
2. If 2+ signals fire → component scope confirmed. State explicitly: *"Component-scope: 2 signals matched (short brief + 'button' keyword). Skipping macrostructure / nav / footer / hero enrichment."*
3. If only 1 signal fires (ambiguous) → ask via `AskUserQuestion`: *"One component or whole page?"*. Default to component if user doesn't engage
4. If 0 signals fire OR page-flow signals dominate → stay in page scope (current behavior)

Component-scope routing:
- Skip Phase 2.5 macrostructure pick, Phase 2c spatial language, hero enrichment
- Keep Phase 2 vibe/palette/typography, Phase 2.6 Brand Motion Identity
- Phase 7 collapses page-level emission (see `workflow-implement.md § Step 1.6`)
- Phase 8 runs subset (see `workflow-audit.md § Step Y`)
- Skip `.perfect-ui/log.json` write (component runs don't rotate)

See `component-scope.md` for full protocol + signal list.

### No refusals
The skill does NOT refuse any `--type` value. Dashboard / admin / SaaS-app / e-commerce are accepted with the evidence-base disclosure above. When another skill is genuinely a better fit (full app architecture, full Shopify backend, exact screenshot replication), skill suggests via SKILL.md § Beyond — never force-redirects.

---

## Phase 1 — Discovery (inline brainstorm protocol)

Full protocol in `workflow-brainstorm.md`. Summary:
- Skill conducts brainstorm directly via `AskUserQuestion` (no external delegation)
- Output: `plans/{date}-{slug}/brief.md`
- Type-branched question scripts (landing / portfolio / generic)
- Phase 0.5 log.json read happens here — surfaces diversification check
- Approval gate before Phase 2

See `workflow-brainstorm.md` for full step-by-step + brief schema.

---

## Phase 2 — Visual Direction

### 2a. Color palette dialog
Use `AskUserQuestion` with header "Palette".

Derive 3 candidate palettes from the vibe in brief:

```
Vibe: editorial luxury → candidates:
1. Cream base (#F5F1E8) + ink (#1A1715) + dusk-rose accent (#B8635A)
2. Bone (#EDE6D6) + charcoal (#221F1B) + olive accent (#7A8A4A)
3. Off-white (#F8F6F1) + deep navy (#0E1A2B) + ochre accent (#C89A3A)
```

Show 3 candidates. User picks one. Lock it.

### 2b. Typography pair dialog
Use `AskUserQuestion` with header "Typography".

Same drill: 3 candidate display+body pairs from vibe.

```
Vibe: brutalist → candidates:
1. Display: PP Neue Machina Inktrap | Body: PP Neue Montreal
2. Display: Migra | Body: GT America Mono
3. Display: Hubot Sans | Body: Söhne
```

Validate forbidden list: refuse if user picks Inter/Roboto/Arial/Open Sans/Space Grotesk (unless they push back twice — log the override).

### 2c. Spatial language
Single `AskUserQuestion`:
- Asymmetric editorial
- Minimal grid
- Brutalist density
- Atmospheric (gradients + grain)

### 2d. Visual Effect Layer (always proposed)
Single `AskUserQuestion` with the visual-effect options from SKILL.md Phase 2d. **Note:** No 3D-model option — that decision is forbidden by Hard Rule 4.

### 2e. Motion Intensity Lock (always asked)
Single `AskUserQuestion` with header "Motion Budget":
- 0/3 — no motion (CSS hover only)
- 1/3 — minimal (CSS + light entrance)
- 2/3 — moderate (FM entrance + Lenis smooth scroll)
- 3/3 — full choreography (FM + Lenis + GSAP timelines)

Default = vibe matrix from `motion-patterns.md` § Vibe × Motion Intensity. If user picks intensity that mismatches vibe (e.g., 3/3 for minimal), flag with confirmation prompt and log override in `plans/{date}-{slug}/overrides.md`.

### 2.5. Macrostructure pick (NEW v2.4.0)

After palette + typography + spatial language + effect layer + motion intensity locked, pick **macrostructure** — page-shape archetype independent of vibe. See `macrostructure-catalog.md` for the 7 macros (Marquee Hero / Bento Grid / Long Document / Manifesto / Stat-Led / Workbench / Letter).

```
AskUserQuestion header: "Macrostructure"
Question: "Pick the page-shape archetype that fits the brief?"
Options: Marquee Hero · Bento Grid · Long Document · Manifesto · Stat-Led · Workbench · Letter
Default = vibe-suggested (varies)
```

**Diversification rule (hard):** macrostructure pick must NOT match any of the last 3 entries in `.perfect-ui/log.json` (read at Phase 0.5). State which macrostructures are eligible explicitly:

> "Last 3 macrostructures: Marquee Hero · Bento Grid · Long Document.
> Eligible this run: Manifesto · Stat-Led · Workbench · Letter."

If user demands a repeat, log override in `plans/{date}-{slug}/overrides.md` with explicit reason.

### 2.6. Brand Motion Identity (NEW v2.4.0)

After motion intensity (2e) and macrostructure (2.5) locked, pick **motion personality** — drives 3 locked motion constants (signature easing + duration palette + entrance pattern). See `motion-patterns.md § Motion Personalities`.

```
AskUserQuestion header: "Brand Motion Identity"
Question: "Pick motion personality (drives signature easing + duration + entrance pattern)?"
Options: Playful · Premium · Corporate · Energetic
Default = vibe-derived (see motion-patterns.md § Per-vibe personality defaults)
```

Independent of intensity — Premium personality can be 1/3 OR 3/3 intensity. Locked constants apply throughout Phase 7 implementation.

### Output artifact
`plans/{date}-{slug}/visual-direction.md`:
```markdown
# Visual Direction
- Vibe: {anchor} + {wildcard}
- Palette: {role: hex} × 4
- Tailwind tokens: {snippet}
- Display font: {name}, weights [...]
- Body font: {name}, weights [...]
- Spatial language: {choice}
- Motion intensity: {0/3 | 1/3 | 2/3 | 3/3} (Phase 2e)
- Motion stack: {CSS only | CSS + FM | CSS + FM + Lenis | FM + Lenis + GSAP}
- Easing: {cubic-bezier values from motion-patterns.md § Easing Library}
- 3D layer: {none | hero-scene | scroll-triggered | interactive-accent}
- Forbidden: Inter, Roboto, AI purple gradient, centered hero (unless minimal)
```

---

## Phase 3 — Custom Icon Set

See `custom-icon-pipeline.md` for full decision tree. Quick reference:

### Icon inventory worksheet
For each icon, fill: `name | section | metaphor | complexity | method`

```
nav-logo-mark      | header     | mountain peak (brand-specific) | high  | AI gen + trace
feature-speed      | features   | arc + dot (momentum)           | low   | direct SVG
feature-secure     | features   | folded paper (private)         | med   | direct SVG (avoid shield cliché)
feature-flow       | features   | ribbon path                    | med   | direct SVG
cta-arrow          | hero CTA   | thin arrow with custom angle    | low  | direct SVG
social-twitter     | footer     | hand-drawn X                   | med   | AI gen + trace
social-github      | footer     | hand-drawn cat silhouette      | med   | AI gen + trace
testimonial-quote  | testimonials | open quote mark              | low   | direct SVG
```

### Cohesion check (before generating)
All icons in the set MUST share:
1. **Stroke weight** — pick one: 1px (thin), 1.5px (default), 2px (bold)
2. **Corner radius** — sharp / rounded / mixed-by-rule
3. **Fill style** — outlined / filled / duotone (one only)
4. **Visual metaphor language** — handcrafted / geometric / organic / pixel

If a needed icon can't fit the cohesion, redesign the metaphor — don't break cohesion.

### Generation
- **Direct SVG:** Claude writes inline. Target viewBox `0 0 24 24` for system icons, `0 0 48 48` for hero glyphs.
- **AI gen + trace:** use a vector icon design pipeline (text-to-SVG, or text-to-image with high-quality palette + lighting + composition control, followed by vector tracing via Inkscape Trace Bitmap or equivalent vectorizer). Mention the trace step to user; don't auto-trace.

### Output structure (Next.js)
```
app/components/icons/
├── index.ts              (re-exports)
├── icon.tsx              (base wrapper: size, color, stroke props)
├── nav-logo-mark.tsx
├── feature-speed.tsx
├── feature-secure.tsx
└── ...
```

Each icon component:
```tsx
import { Icon, type IconProps } from './icon';
export const FeatureSpeed = (props: IconProps) => (
  <Icon {...props}>
    <path d="M..." />
  </Icon>
);
```

---

## Phase 4 — 2D Visual Assets

**2D craft is the default visual language.** Pick illustration style from `2d-illustration-catalog.md` based on locked vibe.

### Pre-step: pick illustration style from catalog
Open `references/2d-illustration-catalog.md` § Vibe → Style Mapping table. Find row matching locked vibe. Pick primary style — use secondary only if primary fails to deliver hero.

### Asset checklist
- [ ] Hero illustration / scene / static 3D render exported as PNG (1)
- [ ] Section dividers / accents (2-4)
- [ ] Background texture (1, tileable)
- [ ] Open Graph image (1, 1200×630)
- [ ] Favicon source (1, square)
- [ ] Avatars / portraits (per testimonials, owner portrait if portfolio)

### Prompt template (apply to ALL prompts)
```
Subject: {what}
Style: {pick from 2d-illustration-catalog} (e.g., "silkscreen poster", "hand-drawn ink", "static 3D render")
Vibe: {vibe-anchor} + {wildcard}
Palette: {3 colors with hex from locked direction}
Lighting: {keyword matched to style}
Composition: {asymmetric | centered | rule-of-thirds}
Negative space: {where empty}
Style refs: {2-3 artistic references from catalog row}
Forbidden: AI purple/blue gradient, neon glow, generic tech illustration,
  Microsoft-clip-art aesthetic, gradient mesh, default Octane 3D render,
  AI-generated 3D blob characters, generic AI 3D-rendered scenes
Aspect ratio: {1:1 | 16:9 | 9:16}
```

Examples in `visual-asset-prompt-library.md`. Static 3D render templates in same file § Static 3D Render → 2D Image Templates.

### Capability routing (by style)
| Style | Capability needed | Notes |
|-------|-------------------|-------|
| Silkscreen / hand-drawn / cut-paper / risograph / watercolor | Text-to-image with style control (curated style prompt library) | Best style match from a catalog of style references |
| Engraved line-art / vintage patent | Text-to-image with creative direction freedom (wild / non-deterministic mode) | Useful for atmospheric / non-photographic outputs |
| Geometric flat (SVG) | Direct SVG generation by LLM (inline code) | Preferred for production-quality vector |
| Architectural schematic | Text-to-image with technical aesthetic, or direct SVG | Crisp lines + measurement annotations |
| Static 3D render → 2D | 3D modeling tool (Blender / Spline / KeyShot) exporting PNG/WebP — OR high-quality text-to-image with palette + lighting + composition control | Output is PNG/WebP, NEVER `.glb` |
| Photographic | Real photos preferred for portfolios with real work; AI fallback uses text-to-image with photorealism + anti-stock negatives | Avoid stock-photo aesthetic |
| Synthwave gradient | Text-to-image with style control (synthwave preset) | Retro-futuristic vibe ONLY |
| OG image | Multi-platform social image composition (HTML→screenshot or text-to-image) | Manual composition fallback |

### Forbidden in this phase
- AI-generated 3D models (`.glb`/`.gltf`) — even if "for the hero"
- Stock illustrations from `unDraw` / `Storyset` libraries
- Default Octane render aesthetic outputs
- Style mixing across assets (silkscreen hero + synthwave dividers)
- Palette drift (using colors not in locked palette)

### Validation loop
After every generation:
1. View image with a vision-capable model (analyze image content)
2. Check: does it match locked palette? (extract dominant colors, compare)
3. Check: does it match the chosen catalog style? (describe style in 3 words, compare to row)
4. Check: does it match vibe adjective from brief?
5. If drift > 30%, regenerate with stronger negative prompt
6. After ≥ 3 assets generated, run cross-asset cohesion audit (do they all feel one hand?)

---

## Phase 5 — Visual Effect Layer (conditional)

Skip entirely if Phase 2d returned "none". **Scope: shaders, particles, atmospheric layers — NOT 3D models.**

### Decision matrix
| Phase 2d choice | Effect scope | Stack |
|----------------|---------|-------|
| CSS-only atmosphere | Gradient + grain overlay + subtle CSS animation | CSS only (Tier 1) |
| Shader background | Procedural noise / fluid / displacement on fullscreen plane | RTF shader (Tier 3) |
| Particle field | 5000+ shader-driven points | RTF + bufferGeometry + shader (Tier 3) |
| Scroll-driven distortion | Lenis + GSAP ScrollTrigger drives shader uniform | Lenis + GSAP + RTF shader (Tier 2-3) |
| Cursor-reactive accent | Mouse coords drive CSS conic gradient OR shader uniform | CSS preferred (Tier 1), shader fallback (Tier 3) |
| Lottie animation | After-Effects-style 2D motion | Lottie (Tier 4) |

**Tier 1 = CSS preferred. Try CSS first, only escalate to WebGL when CSS proves insufficient.**

### Pattern lookup (only if shader/particle approach chosen)
See `visual-effect-patterns.md` for inline shader/particle patterns. Common targets:
- Fragment shader noise (background atmospheric layer)
- Particle field with GPU compute (~5000+ shader-driven points)
- Scroll-driven shader uniform (Lenis + GSAP ScrollTrigger driving displacement)
- Cursor-reactive shader (mouse coords → uniform)
- Postprocessing grain (CSS noise PNG preferred; shader fallback for procedural)

### Forbidden in this phase
- AI-generated `.glb`/`.gltf` as hero subject (use Phase 4 static 3D render instead)
- `OrbitControls` enabled (signals viewer demo)
- `MeshNormalMaterial` rainbow (Three.js default)
- Heavy bundle (>100KB) for what CSS can deliver
- Effect-for-effect's-sake (decorative without narrative)

### User-provided GLB exception (rare, logged)
If user explicitly provides real-product GLB:
1. Confirm model shows real shippable product (not generic shape)
2. Log override in `plans/{date}-{slug}/overrides.md`
3. Apply standard React Three Fiber guardrails (Draco compression, `<Suspense>` fallback, `dpr={[1, 2]}` cap, lazy-load with `dynamic({ ssr: false })`)

See `visual-effect-patterns.md` for full integration guide.

---

## Phase 6 — Plan (inline plan protocol)

Full protocol in `workflow-plan.md`. Summary:
- Skill writes `plans/{date}-{slug}/plan.md` + `phase-XX-*.md` files directly
- Tier-branched phase decomposition (landing / portfolio / generic)
- File ownership contracts for parallel-safe execution
- Approval gate before Phase 7

See `workflow-plan.md` for full step-by-step + plan.md schema + phase decomposition rules.

---

## Phase 7 — Implement (inline implement protocol)

Full protocol in `workflow-implement.md`. Summary:
- Skill implements from `plan.md` + `phase-XX-*.md` directly
- Per CLAUDE.md: NO auto-commit — user reviews + commits manually after each phase
- Per-phase constraints enforced throughout (imports / tokens / layout / copy / motion)
- Mid-implementation spot-checks after each section
- **At Phase 7-end, write entry to `.perfect-ui/log.json`** (read by Phase 0.5 on next run for diversification)

See `workflow-implement.md` for full step-by-step + constraints + log.json write schema.

---

## Phase 8 — Anti-Slop Review (Tier-Filtered)

Full protocol in `workflow-audit.md`. Summary:
- Read current session `--type`, tier (set in Phase 0.5), marketing intent flag, and macrostructure
- Build filtered rule set (special tier = all rules; generic tier = `[universal]` + conditionally `[marketing-only]`)
- Run grep checks + macrostructure-specific checks + visual checks via vision-capable model
- Output `plans/{date}-{slug}/anti-slop-report.md` with verdict
- If any Tier 1 OR (≥1 Tier 1 + ≥2 Tier 2 stacked) → return to Phase 7 to fix

See `workflow-audit.md` for full step-by-step + grep checklist + report template.
