# Anti-Slop Rules — What to Avoid

The defaults LLMs gravitate to. Treat as forbidden unless user explicitly overrides twice (and log the override).

## Tier System (Cumulative Detection)

Tells are CUMULATIVE — a single rule break is sometimes compensable, but stacked tells = AI fingerprint. This system was derived from direct comparison of 5 AI-generated landings vs 7 human-crafted landings (see `plans/260509-ai-vs-human-analysis/synthesis.md`).

| Tier | What it is | Enforcement |
|------|-----------|-------------|
| **Tier 1** | Strong, unambiguous AI tells. Each one is a serious flag. | ≥1 tier-1 hit = MUST FIX. ≥2 tier-1 hits = AI fingerprint, refuse to ship. |
| **Tier 2** | AI compositional tendencies. Common in AI work but not exclusive. | 1 tier-1 + 2+ tier-2 = drift toward AI; MUST FIX before ship. |
| **Tier 3** | Style preferences. Compensable with strong visual craft. | OK alone IF cohesion is high (see `visual-direction-guide.md` § Commitment Audit). |

**Strict default:** treat all tiers as MUST FIX. Only allow tier-3 break with logged override + visual proof of compensation.

### Tier 1 — Strong AI tells (MUST FIX, no compensation)

1. **DM Sans + Space Grotesk Google Fonts pairing** — direct evidence: present in 3/5 AI pages, 0/7 human. Forbidden combo even though individual fonts are softer flags.
2. **AI purple/blue gradient hero** (`from-purple-500 to-blue-500`, `from-purple-100 to-purple-200`, etc.) + **gradient highlight on H1 keyword** (e.g., "Confidence" highlighted in cyan-blue gradient).
3. **Icon library imports detected** (`lucide-react`, `@heroicons`, `@phosphor-icons`, `@tabler/icons`, `react-icons`, `font-awesome`). Confirmed in 2/5 AI pages.
4. **Two equal-weight CTAs in hero** (e.g., "Start Free" + "View Demo" both as primary buttons). Confirmed 5/5 AI pages.
5. **`h-screen` utility class** present (must use `min-h-[100dvh]`).
6. **3-column equal feature card grid** — confirmed 6 hits in single AI page; 0 in human.
7. **Heavy Tailwind utility class density (>200 in initial HTML)** — snapstory: 383, exgen: 379. Human max: 64. Indicator of utility-spam without committed CSS architecture.
8. **Generic SaaS CTA labels in hero**: "Get Started", "Sign In", "Subscribe", "Start Free", "Sign Up Free" — without product-specific framing.
9. **AI-generated 3D model as hero subject**. Default Octane render aesthetic — glossy plastic shading, balanced studio lighting, generic primitive arrangement. Includes `.glb`/`.gltf` imports rendered real-time + AI-generated 3D blobs/characters/scenes. **Use static 3D render → 2D image (Augen pattern) OR shader effect OR 2D illustration instead.** Direct evidence: 0/7 human-crafted landings used real-time AI-generated 3D models.
10. **OrbitControls enabled in landing/portfolio** — signals "viewer demo" not designed page. Visitor doesn't want to "explore 3D."
11. **Hero H1 line count exceeds vibe-specific limit** — see `visual-direction-guide.md` § Hero H1 line range column. Universal ceiling: 4+ lines is catastrophic failure regardless of vibe. Enforcement: container `max-w-5xl` / `max-w-6xl` + H1 `clamp(3rem, 5vw, 5.5rem)`. If headline copy exceeds 90 chars, rewrite shorter; never break the line cap by reducing font below `--text-display-s`.
12. **Meta-label headers** — "SECTION 01" / "QUESTION 05" / "ABOUT US" / "CHAPTER THREE" / numbered eyebrows / uppercase mono-cap section labels. No exception even for ordinal content. Vibe-paired typography hierarchy (display weight, color) communicates section identity instead. **Exception:** Long Document macrostructure (see `macrostructure-catalog.md` § Long Document) permits numbered sections for genuinely ordinal content, cap ≤5.
13. **Empty Bento Grid cells / missing corners / voids** — when using Bento Grid macrostructure (see `macrostructure-catalog.md` § Bento Grid), `grid-flow-dense` is MANDATORY + col-span/row-span values must mathematically interlock. Empty cells = templated AI feel. Use `auto-flow: dense` + verify with visual inspection.

### Tier 2 — AI compositional tendencies (MUST FIX in combination)

11. **Generic browser-mockup as right-half of split hero** (laptop frame with fake UI inside). Confirmed pattern in snapstory + exgen.
12. **Friendly bullet checkbox reassurance row** under hero CTA: "✓ No credit card required ✓ 7-day free trial ✓ Cancel anytime" (or similar checkmark trio).
13. **Round fake stats** in hero: "10K+ users", "2M+ downloads", "1M+ stories", "99.99% uptime", "10x faster".
14. **AI-generated cute illustration** as hero subject (cartoon animal with laptop/coffee, generic 3D blob characters, "diverse team smiling at laptop").
15. **Centered hero with centered H1** at high DESIGN_VARIANCE. Compensable only when vibe is genuinely minimal AND visual carries the page.
16. **3D model used purely as decoration** without narrative purpose (e.g., rotating cube/torusKnot in middle of viewport). Even with user-provided GLB exception, model must serve the product story, not be eye-candy.
17. **Style mixing across 2D illustrations** in same site (silkscreen poster hero + synthwave gradient mid-section). All illustrations must share style language per `2d-illustration-catalog.md` § Asset Cohesion Rules.
18. **Hero filler text** — "Scroll to explore" / "Swipe down" / "Continue below" / similar prompt-text in hero. Hero composition must communicate "more below" without typed instructions. Icons (↓, chevron, bouncing arrow) are OK — they're visual hints, not text filler. Aggressive bouncing animation = additional Tier 2 hit (generic motion-on-everything rule).

### Tier 3 — Style preferences (compensable with visual craft)

14. Forbidden font names appearing alone: `Inter`, `Roboto`, `Open Sans`, `Helvetica`, `Poppins`, `Lato`, `Montserrat`, `Nunito` — *without* a distinctive paired display font.
   - **Compensable:** Inter as body text IS acceptable when paired with a distinctive display font (Instrument Serif, PP Neue Montreal, Greed, etc.). Confirmed in 3/7 human pages.
15. AI cliché phrases: Elevate / Seamless / Unleash / Empower / Unlock / Game-changer / Cutting-edge / Next-gen / Delve / Tapestry / Leverage. Confirmed 5/7 human pages contain at least one — but in non-load-bearing copy positions, with strong visual craft. Refuse them in headlines and primary CTAs unconditionally.
16. Generic startup names in placeholders: Acme, Globex, Initech.
17. Title Case Headers (sentence case is more contemporary).

### How to use these tiers

- During Phase 8 audit, classify each finding as Tier 1 / 2 / 3.
- Tier 1: ALWAYS fix.
- Tier 2: count alongside tier 1. ≥1 tier-1 + 2+ tier-2 = block ship.
- Tier 3: allow IF visual craft cohesion ≥ 8/10 (see `visual-direction-guide.md` § Commitment Audit).
- Override path: user must request twice + reason logged in `plans/{date}-{slug}/overrides.md`.


## Applicability Matrix

Phase 8 audit filters rules by `--type` tier. Two tiers:

- **Special tier** (`--type landing` | `--type portfolio`) → all rules apply (full set below)
- **Generic tier** (any other `--type` — blog, about, pricing, contact, coming-soon, dashboard, admin, e-commerce, custom) → `[universal]` rules apply; `[marketing-only]` rules apply only when the page has explicit marketing intent (see [`generic-page-anatomy.md`](generic-page-anatomy.md) § Page-Purpose Exercise question 5); `[landing/portfolio-only]` rules skipped

### Tag table

| Rule | Tag | Tier |
|------|-----|------|
| DM Sans + Space Grotesk pairing | `[universal]` | 1 |
| Icon library imports (lucide / heroicons / phosphor / tabler / react-icons / font-awesome / material-icons) | `[universal]` | 1 |
| `h-screen` utility | `[universal]` | 1 |
| Tailwind utility density > 200 in initial HTML | `[universal]` | 1 |
| AI purple/blue gradient hero + gradient highlight on H1 keyword | `[marketing-only]` | 1 |
| Two equal-weight CTAs in hero | `[marketing-only]` | 1 |
| 3-column equal feature card grid | `[marketing-only]` | 1 |
| Generic SaaS CTA labels ("Get Started" / "Sign In" / "Subscribe") | `[marketing-only]` | 1 |
| AI-generated 3D model as hero subject | `[marketing-only]` | 1 |
| `OrbitControls` enabled | `[marketing-only]` | 1 |
| Hero H1 line count exceeds vibe-specific limit (see `visual-direction-guide.md`) | `[universal]` | 1 |
| Meta-label headers ("SECTION 01" / "CHAPTER THREE" / numbered eyebrows) | `[universal]` | 1 |
| Empty bento grid cells (missing corners / voids; `grid-flow-dense` missing on Bento macrostructure) | `[universal]` | 1 |
| Hero filler text ("Scroll to explore" / "Swipe down") — icons OK | `[marketing-only]` | 2 |
| Fabricated metrics / testimonials / logos / case-study counts — see § Honest Copy Mandate | `[universal]` | (positive guidance — flag in audit when fabrication detected) |
| Generic browser-mockup right-half hero | `[marketing-only]` | 2 |
| Friendly bullet checkbox reassurance row under CTA | `[marketing-only]` | 2 |
| Round fake stats (10K+ / 99.99% / 10x faster) | `[marketing-only]` | 2 |
| AI-generated cute illustration as hero subject | `[marketing-only]` | 2 |
| Centered hero + centered H1 at variance > 4 | `[marketing-only]` | 2 |
| 3D model as pure decoration (no narrative) | `[marketing-only]` | 2 |
| Style mixing across 2D illustrations in same site | `[universal]` | 2 |
| Forbidden fonts alone (Inter / Roboto / Open Sans / Helvetica / Poppins / Lato / Montserrat / Nunito) | `[universal]` | 3 |
| AI cliché phrases (elevate / seamless / unleash / empower / unlock / game-changer / cutting-edge / next-gen / delve / tapestry / leverage) in headlines / primary CTAs | `[marketing-only]` | 3 |
| Generic startup placeholder names (Acme / Globex / Initech) | `[universal]` | 3 |
| Title Case headers | `[universal]` | 3 |
| Forbidden color patterns (pure `#000` / `#FFF` / saturation > 80% / mixed warm+cool grays / >1 accent) | `[universal]` | layout |
| Forbidden layout patterns (all-centered / equal card heights forced / missing max-width / uniform border-radius) | `[universal]` | layout |
| Emoji as icons | `[universal]` | icons |
| Mixed icon styles in same set | `[universal]` | icons |
| Cliché icon metaphors (rocket / shield / lightning / lightbulb / gear) | `[universal]` | icons |
| Neon outer glows / custom mouse cursors / glassmorphism without inner-border | `[universal]` | effects |
| Generic fade-up on every element | `[universal]` | motion |
| `ease-in-out` 0.3s as universal duration | `[universal]` | motion |
| Motion on body `<p>` text | `[universal]` | motion |
| Multiple competing scroll libraries | `[universal]` | motion |
| Ignoring `prefers-reduced-motion` | `[universal]` | motion |
| `will-change` left on after animation | `[universal]` | motion |
| Landing copy clichés (Elevate / Seamless / Take your X to the next level / The future of...) | `[marketing-only]` | copy |
| Portfolio openers ("Hi I'm X passionate..." / "Hello world" / "Welcome to my portfolio") | `[landing/portfolio-only]` (portfolio) | copy |
| Portfolio personality padding (multi-disciplinary creative / pixel-perfect / crafting beautiful / "I love coffee and dogs") | `[landing/portfolio-only]` (portfolio) | copy |
| Portfolio "Years of experience" counter | `[landing/portfolio-only]` (portfolio) | copy |
| Portfolio vague availability ("Available for new opportunities" w/o date) | `[landing/portfolio-only]` (portfolio) | copy |
| Forbidden placeholder data (John Doe / Jane Smith / Project 01 / generic role+company) | `[universal]` | copy |
| Unstyled default shadcn components | `[universal]` | components |
| Pill "New" / "Beta" badges everywhere | `[universal]` | components |
| Newsletter footer takeover | `[marketing-only]` | components |
| 4D framework cliché (Discover-Define-Develop-Deliver) | `[landing/portfolio-only]` (portfolio) | copy |
| Skill bars / proficiency percentages | `[landing/portfolio-only]` (portfolio) | copy |

### Audit logic (Phase 8)

1. Read current session `--type`
2. Determine tier — special vs generic
3. Build filtered rule set:
   - special tier → all rules
   - generic tier → `[universal]` rules + (`[marketing-only]` rules iff marketing intent = true from page-purpose exercise)
   - generic tier → never `[landing/portfolio-only]` rules
4. Run grep checks on filtered subset (see § Final Audit Checklist at end of this file)
5. Output PASS/FAIL with applicable-rule count and skipped-rule count

### Worked examples

- `--type landing` → ~45 rules apply (full set)
- `--type portfolio` → ~45 rules apply (full set including portfolio-specific copy clichés)
- `--type pricing` with marketing intent = true → ~38 rules apply (skips portfolio-only ~4 rules)
- `--type dashboard` with marketing intent = false → ~25 rules apply (skips marketing-only ~16 + portfolio-only ~4)
- `--type blog` with marketing intent = true → ~38 rules apply
- `--type 404` with marketing intent = false → ~25 rules apply

Untagged rules in this file default to `[universal]` — safest assumption.


## Honest Copy Mandate (universal)

If the user did not supply a metric / testimonial / logo / case-study count, the skill does NOT invent one. Three accepted paths:

1. **Em-dash placeholder + label (default)** — `— metric to confirm` rendered as a visible grey block. Layout reserves space; user fills in later. HTML/JSX pattern:
   ```tsx
   <span className="placeholder bg-bg-muted px-2 rounded text-ink-muted">
     — metric to confirm
   </span>
   ```
2. **Pick a different macrostructure** — if a stat-led hero requires N metrics and only M < N are available, switch to a non-stat hero (typography-led, image-led).
3. **Refuse the section entirely** — if a "trusted by 50,000+ teams" logo bar has 0 real logos to show, do not include the logo bar. Honest absence beats fabricated presence.

Forbidden fabrications (already in tier matrix; restated here for context):
- "+47% conversion", "trusted by 50,000+ teams", "10× faster" with no source
- John Doe / Jane Smith testimonials with realistic-looking avatars
- Generic startup logos (Acme / Globex / Initech / Nexus)
- "1M+ users", "99.99% uptime", round-fake numbers
- Fabricated case-study counts (8 case studies displayed when user has 2)

Phase 7 implementation must use placeholder rendering; Phase 8 audit greps for forbidden numbers + names per `[marketing-only]` rules.


## Diversification Rule (cross-run)

For projects with multiple perfect-ui runs (tracked in `.perfect-ui/log.json`), each new run must avoid replicating recent picks:

- **Macrostructure** (hard rule) — must differ from the last 3 entries. See `macrostructure-catalog.md § Diversification rule`.
- **Vibe + wildcard** combo (soft warning) — should differ from last entry; user override allowed.
- **DESIGN_VARIANCE + VISUAL_DENSITY** dials (soft warning) — at least one dial should differ ≥3 points from last entry.
- **Motion personality** (soft warning) — should differ from last entry.

Read at Phase 0.5; surface as one-line summary ("Last 3 builds: ..."). Hard rule violations block (user override + log entry required); warnings allow override. See `workflow-brainstorm.md` § Phase 0.5 for read logic.


## Typography

### Forbidden fonts (default reject)
- `Inter` — the AI fingerprint
- `Roboto`, `Arial`, `Open Sans`, `Helvetica` — browser defaults
- `Space Grotesk` — overused tech-startup tell
- `Poppins`, `Lato`, `Montserrat`, `Nunito` — generic SaaS

### Forbidden patterns
- Only weights 400 + 700 (use 500/600 for subtler hierarchy)
- All-caps subheaders everywhere (try sentence case, italic, small-caps)
- Title Case On Every Header (sentence case is more contemporary)
- Orphaned words on H1 (always `text-wrap: balance`)
- Serif fonts on data UIs (reserve serif for editorial)

### Approved alternatives
| Need | Pick from |
|------|-----------|
| Clean grotesk | `Söhne`, `Geist`, `GT America`, `Neue Haas Unica` |
| Display character | `Cabinet Grotesk`, `PP Neue Machina`, `Migra`, `Editorial New` |
| Editorial serif | `Lyon Text`, `Reckless Neue`, `GT Sectra`, `Tobias` |
| Monospace | `JetBrains Mono`, `Geist Mono`, `Söhne Mono`, `IBM Plex Mono` |
| Variable fonts | `Outfit`, `Plus Jakarta Sans`, `General Sans` |

## Color

### Forbidden patterns
- AI purple/blue gradient hero (`from-purple-500 to-blue-500`) — THE most common AI fingerprint
- Pure `#000000` (use `#0a0a0a` or tinted dark)
- Pure `#FFFFFF` for backgrounds (use cream / off-white)
- Saturation > 80% on accents (desaturate to ≤80%)
- Gradient text on body copy or large headers
- Mixed warm + cool grays (pick one family, stick to it)
- More than one accent color (max 1)

### Approved patterns
- Single accent, used <10% of total surface area
- Tinted shadows (dark navy shadow on navy bg, not pure black)
- Subtle noise/grain over flat colors (not sterile flat)
- Off-blacks: `#0A0A0A`, `#111111`, `#1A1715`, Zinc-950
- Off-whites: `#FAFAF7`, `#F5F1E8`, `#F8F6F1`

## Layout

### Forbidden patterns
- 3-column equal-card feature row (THE generic AI layout)
- Centered hero with centered H1 at DESIGN_VARIANCE > 4
- `h-screen` (always `min-h-[100dvh]` — iOS Safari viewport bug)
- All sections centered and symmetrical
- Equal card heights forced by flexbox (allow variable or use masonry)
- Uniform border-radius everywhere (vary by hierarchy)
- Missing max-width (always constrain to ~1200-1440px)
- Hero with two equal-weight CTA buttons

### Approved patterns
- Asymmetric grid: `grid-cols-12` with deliberate offset placement
- 60/40 split heroes for editorial
- Zig-zag 2-col features (image left → image right alternating)
- Masonry for testimonials
- Variable border-radius: tighter on inner elements (4px), softer on containers (16px)
- Single primary CTA in hero, optional ghost button as secondary

## Icons

### Forbidden
- Any `npm install` of icon library (lucide-react, @heroicons/react, @phosphor-icons, @tabler/icons-react, react-icons, font-awesome, material-icons)
- Emoji as icons (`✨`, `🚀`, `🔒`, `⚡`)
- Mixed icon styles in the same set (some outlined, some filled)
- Cliché metaphors (rocket=launch, shield=security, lightning=speed, lightbulb=idea, gear=settings)
- Library icon "just for now" — never gets replaced

### Approved
- Custom SVG components in `app/components/icons/`
- Single stroke weight, single corner family, single fill style
- Fresh metaphors tied to product (folded paper for privacy, arc-with-momentum-dot for speed)
- AI-generated + traced for ornate icons
- Direct hand-written SVG for simple geometric icons

## Visual Effects

### Forbidden
- Neon outer glows (`box-shadow: 0 0 40px ...`) — "modern" cliché
- Custom mouse cursors (hurts a11y, dated)
- `backdrop-blur` glassmorphism without inner border + refraction shadow
- Auto-playing video heros with loud audio

### Approved
- Inner-border + tinted shadow combos for elevation
- Subtle noise/grain layer over flat surfaces

## Motion (Tier 2 — must be vibe-scaled per `motion-patterns.md`)

### Forbidden
- **Generic fade-up on every element** — AI default, becomes invisible noise. Cap ≤30% sections animate at intensity 2/3.
- **`ease-in-out` 0.3s as universal duration** — no character. Use vibe-paired cubic-bezier or spring physics.
- **`ease-out` / `ease-in-out` named keywords** in `transition` / Framer Motion — AI default. Replace with `cubic-bezier(...)` or spring.
- **Motion on body `<p>` text** — distracting, hurts readability.
- **Motion library imported but used <50%** — bundle bloat. If only ONE animation, use a lighter tier.
- **Multiple competing scroll libraries** (Lenis + Locomotive + native CSS smooth-scroll).
- **Ignoring `prefers-reduced-motion`** — accessibility violation.
- **Auto-playing heavy animations on mobile** without intensity-1 degrade at <768px.
- **Motion intensity mismatched with vibe** without logged override (e.g., 3/3 motion on minimal vibe).
- **`will-change` left on after animation completes** — kills perf.

### Approved
- Spring physics motion (Framer Motion `spring` config) for playful vibes
- Custom cubic-beziers paired to vibe (see `motion-patterns.md` § Easing Library)
- CSS-only hover transitions (Tier 1 — universal)
- FM `whileInView` with stagger ≤200ms total, `viewport={{ once: true }}` (no replay)
- Lenis smooth scroll baseline at intensity ≥2/3 for luxury / glass-tech / organic vibes
- GSAP ScrollTrigger ONLY at intensity 3/3 with timeline `scrub` for choreographed reveals
- `prefers-reduced-motion` respected via FM `useReducedMotion()` or CSS `@media`
- Orchestrated page-load sequence > scattered micro-interactions

## Copy

### Forbidden — landing-specific
- Elevate, Seamless, Unleash, Empower, Unlock
- Game-changer, Next-gen, Cutting-edge, Revolutionary
- Delve, Tapestry, Embark, Leverage, Synergy
- Robust, Comprehensive, Holistic
- "Take your X to the next level"
- "Designed for the modern [audience]"
- "The future of [thing]"
- "Lorem ipsum"

### Forbidden — portfolio-specific
- "Hi, I'm [Name], a passionate {designer | developer | creative}..."
- "Hello, world!" greeting
- "Welcome to my corner of the internet"
- "Welcome to my portfolio"
- "Multi-disciplinary creative based in {city}"
- "Crafting beautiful digital experiences"
- "Pixel-perfect" / "pixel pusher" self-descriptors
- "I love coffee and dogs" / personality-padding bio
- "Years of experience" prominent counter
- "Available for new opportunities" with no concrete date
- "Let's create magic together" / "Let's chat"
- "Drop a line"

### Forbidden placeholder data
- Names: John Doe, Jane Smith, Sarah Chan, Acme Corp, Globex, Initech
- Round fake numbers: 99.99%, 50% off, $100.00, 10x faster, 1M+ users
- Generic role + company combos that scream stock
- Portfolio: "Project 01", "Project 02" generic project titles
- Portfolio: testimonials from "Director of Awesome at Generic Studio"

### Approved (both types)
- Real specific numbers: 47.2%, $99, 3.4x, 12,400
- Realistic diverse names tied to actual demographics
- Specific outcome statements ("cut deploy time from 14min to 2min")
- Sentence case headers ("How it works" not "How It Works")
- Confident success messages ("Saved" not "Saved!")
- Direct error messages ("Connection failed. Try again." not "Oops!")

### Portfolio-approved
- "{Name} — {specific craft for specific audience}." Example: "Sarah Chen — Brand identity for early-stage tech."
- "I design healthcare apps. 6 years, 3 platforms, 12 launches." (concrete proof)
- "Available for projects starting June 2026." (concrete date)
- "Selectively booking design partnerships through Q3."
- Specific named process phases tied to your craft (NOT Discover-Define-Develop-Deliver)

## Components

### Forbidden
- Unstyled / default shadcn components (always customize)
- Generic card (white bg + border + shadow) at high VISUAL_DENSITY
- Pill "New" / "Beta" badges everywhere
- Avatar circles exclusively (try squircles, rounded squares)
- 3-card carousel testimonials with dots
- Newsletter footer takeover

### Approved
- Customized shadcn with palette tokens, custom radii, custom shadows
- Mixed shapes: avatar squircles for human, circles for icons
- Single rotating quote testimonial OR masonry wall
- Magazine-style spread testimonials for editorial vibe

## 3D-Specific Anti-Slop

### Forbidden — 3D models (Tier 1 escalation)
- **AI-generated `.glb` / `.gltf` as hero subject** — default Octane render aesthetic, generic plastic shading
- **Rotating product GLB at center of viewport** — "viewer demo" not landing
- **AI-generated 3D characters / blobs / mascots** — generic AI fingerprint
- **`OrbitControls` enabled** in landing/portfolio context

### Forbidden — Three.js defaults
- `MeshNormalMaterial` rainbow (default Three.js render)
- Default Three.js lighting (`AmbientLight` only at 0.5)
- Stock physically-correct grass / water shaders
- Stats overlay shipped to production
- `frameloop="always"` on static scenes
- Generic "particle field with bloom" without vibe match
- "Cube/sphere/torusKnot rotates in middle of viewport" stock demo

### Approved — visual effects only
- Custom-tuned shader material that matches locked palette
- Particle fields driven by shader uniforms (not CPU loop)
- Displacement plane shader (single plane, vertex displacement)
- Refractive shader for glass-tech vibe
- No camera controls (camera is fixed; user doesn't navigate scene)
- `frameloop="demand"` for static-state shaders
- Static 3D render exported as PNG → used as `<Image>` (handled in Phase 4, NOT Phase 5)

### User-provided real-product GLB exception
Only allowed when:
- User explicitly provides a GLB file
- Model shows a real shippable product (not generic shape)
- Override logged in `plans/{date}-{slug}/overrides.md` with reason
- All standard Three.js perf guardrails apply (Draco compression, dpr cap, Suspense fallback)
- Studio HDRI from `drei` for luxury / glass-tech vibe
- `frameloop="demand"` for static / scroll-only
- Lazy-loaded with `dynamic({ ssr: false })` + poster fallback

## Final Audit Checklist

Run BEFORE declaring complete. Each must PASS or be fixed.

### Source-code checks (grep) — universal (both types)
```bash
# Emoji
grep -rE '[\x{1F300}-\x{1FAFF}]' app/  # must be empty

# Icon libraries
grep -rE 'lucide-react|@heroicons|@phosphor-icons|@tabler/icons|react-icons|font-awesome|material-icons' app/ package.json  # must be empty

# Forbidden fonts
grep -rE 'Inter|Roboto|"Open Sans"|Space Grotesk|Poppins|Montserrat|Lato|Nunito' app/ tailwind.config.* next.config.*  # must be empty (unless logged override)

# h-screen
grep -rE '\bh-screen\b' app/  # must be empty (must use min-h-[100dvh])

# Inline hex colors (should be Tailwind tokens)
grep -rE '#[0-9a-fA-F]{6}' app/components/ | grep -v 'tailwind.config' | grep -v 'globals.css'
# Should be empty or only inside SVG icon paths

# 3D model imports (Tier 1 — must be empty unless user-GLB override logged)
grep -rE 'GLTFLoader|FBXLoader|OBJLoader|useGLTF|gltfjsx' app/  # must be empty unless override
find public/ -name '*.glb' -o -name '*.gltf' 2>/dev/null  # must be empty unless override

# OrbitControls in production (Tier 1)
grep -rE 'OrbitControls' app/  # must be empty for landing/portfolio

# Default Three.js material clichés
grep -rE 'MeshNormalMaterial' app/  # must be empty

# Motion library imports — must align with locked Phase 2e intensity
grep -rE "from ['\"]framer-motion['\"]|from ['\"]@studio-freight/react-lenis['\"]|from ['\"]gsap['\"]" app/

# Generic ease-in-out / ease-out (motion anti-pattern — Tier 2)
grep -rE '(ease-in-out|ease-out|"easeInOut"|"easeOut")' app/components/  # flag, replace with cubic-bezier()

# Generic 0.3s duration (often AI default)
grep -rE 'duration:\s*0\.3|duration-300\b' app/  # spot-check, may be intentional but verify

# Motion on body text (forbidden)
grep -rE '<motion\.p\b' app/  # body <p> shouldn't have motion wrapper

# prefers-reduced-motion respect (must have ≥1 hit if motion library imported)
grep -rE 'useReducedMotion|prefers-reduced-motion' app/  # required when FM/Lenis/GSAP imported

# Multiple competing scroll libraries (forbidden)
grep -rE 'locomotive-scroll' app/ package.json  # if Lenis already imported, this is forbidden
```

### Source-code checks — if type = landing
```bash
# AI cliché copy
grep -rEi 'elevate|seamless|unleash|empower|unlock|game.?changer|next.?gen|cutting.?edge|delve|tapestry|leverage' app/lib/content.ts app/components  # must be empty

# Generic placeholder data
grep -rE 'John Doe|Jane Smith|Acme Corp|99\.99|Lorem ipsum' app/  # must be empty
```

### Source-code checks — if type = portfolio
```bash
# Cliché openers
grep -rEi "hi,?\s+i'?m\s+\w+|hello,?\s+world|welcome to my (portfolio|corner)" app/  # must be empty

# Personality padding
grep -rEi 'passionate (designer|developer|creative)|multi.?disciplinary creative|pixel.?perfect|crafting beautiful' app/  # must be empty

# Skill bars / proficiency
grep -rEi 'proficiency|skill.bar|years of experience.{0,30}\d+\+' app/  # must be empty

# 4D framework cliché
grep -rEi 'discover.{0,5}define.{0,5}develop.{0,5}deliver' app/  # must be empty

# Generic project titles
grep -rE 'Project 0?[1-9]|Project Title|Untitled Project' app/  # must be empty

# Vague availability
grep -rEi 'available for new opportunities|let’?s create magic|drop a line' app/  # must be empty
```

### Visual checks (both types)
- [ ] Single accent color enforced (count distinct accent values in render)
- [ ] No AI purple/blue hero gradient
- [ ] Custom icon set is consistent (same stroke weight, corner family, fill style)
- [ ] All text wrap balanced on H1/H2 (`text-wrap: balance`)

### Visual checks — if type = landing
- [ ] Hero is NOT centered-H1-at-variance>4 (unless vibe = minimal)
- [ ] Testimonial avatars don't look like stock photos
- [ ] Two equal-weight CTAs in hero — flag (single primary CTA only)

### Visual checks — if type = portfolio
- [ ] Actual work visible above the fold (not just bio / personality)
- [ ] Project tiles are NOT iPhone-mockup-holding-the-work
- [ ] Project tiles do NOT all force same aspect ratio
- [ ] Email + concrete availability date visible in contact section
- [ ] No hover-effect overload on work grid (max 1-2 hover changes)
- [ ] Skill bars / proficiency percentages absent

### Performance checks
- [ ] Lighthouse mobile performance ≥ 90 (≥ 80 if 3D layer)
- [ ] LCP ≤ 2.5s
- [ ] CLS ≤ 0.1
- [ ] Bundle size: JS ≤ 200KB gzipped (excluding 3D), 3D ≤ 200KB additional

### Accessibility checks
- [ ] Color contrast AA passes for all text
- [ ] All icons have `aria-hidden="true"` or `aria-label`
- [ ] Keyboard navigable (tab through all interactive elements)
- [ ] No motion-only state changes (always pair with color/text)
- [ ] `prefers-reduced-motion` respected on 3D and animations

### Override Logging
If ANY forbidden pattern is intentionally used (user requested twice):
- Add to `plans/{date}-{slug}/overrides.md`
- Format: `- {Forbidden pattern} — overridden because {user's reason}`
- This documents the deliberate violation so it doesn't look like an oversight

## How to Refuse Slop in the Moment

When during implementation Claude is tempted to:
- Reach for `lucide-react` → STOP. Open `references/custom-icon-pipeline.md`. Generate the icon.
- Use Inter "because it's installed" → STOP. Pick from `references/visual-direction-guide.md` typography pairs.
- Write "Elevate your workflow" → STOP. Open `lib/content.ts`. Write what the product actually does.
- Add a purple gradient — STOP. Use the locked accent at <10% surface area.
- Skip the 3D proposal — STOP. SKILL.md mandates always proposing.

**Refusal language:** When user requests a forbidden pattern, respond:
> I'm avoiding {pattern} because it's a known AI-default that breaks vibe cohesion. The locked direction calls for {alternative from visual-direction.md}. If you want to override, confirm twice and I'll log it.
