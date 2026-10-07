# Motion Patterns

How to add intentional motion (entrance animations, hover micro-interactions, scroll-triggered effects, smooth scroll) to a landing or portfolio without drifting into AI-slop "fade-up everywhere" territory.

## Core Principle

**Motion intensity is locked at design time, scaled by vibe, escalated by need.**

Evidence (from `plans/260509-ai-vs-human-analysis/synthesis.md`):
- 4/7 human-crafted landings used motion (gsap+lenis confirmed)
- 3/7 used minimal/no motion and worked beautifully (paperclip, owo, augen)
- AI-generated pages had ZERO motion choreography — but ALSO defaulted to "render and done" with generic fade-ups when motion was added

**Conclusion:** Motion alone is not a craft signal. INTENTIONAL motion paced to vibe IS.

## Tier System (Library Stack)

Escalate only when prior tier insufficient.

| Tier | Tool | Bundle | Use case |
|------|------|--------|----------|
| **1 — CSS** | CSS animations + transitions | 0 KB | Hover, focus, button states, simple fades |
| **2 — Framer Motion** | `framer-motion` | ~25 KB gz | Component entrance/exit, stagger, viewport-triggered reveal |
| **3 — Lenis** | `@studio-freight/react-lenis` | ~10 KB gz | Smooth scroll baseline (only for vibes that benefit) |
| **4 — GSAP + ScrollTrigger** | `gsap` + `ScrollTrigger` plugin | ~50 KB gz | Complex scroll choreography, timeline sequencing, scroll-linked transforms |

**Default decision:** Try CSS first. Add Framer Motion only when component needs viewport-triggered reveal. Add Lenis only when vibe budget ≥ 2/3. Add GSAP only when timeline sequencing genuinely required.

**Total motion bundle target: ≤100 KB gz combined.**

## Vibe × Motion Intensity Matrix

The default intensity per vibe. User can override during Phase 2e (logged if mismatch).

| Vibe | Default | Stack | Typical pattern |
|------|---------|-------|-----------------|
| Minimal | 1/3 | CSS | Hover lift, fade-in on first scroll only |
| Editorial | 1-2/3 | CSS + FM | Stagger display type reveal, slow scroll-linked transforms |
| Brutalist | 0-1/3 | CSS | Hard-edge instant reveals, NO easing on most elements |
| Retro-futuristic | 3/3 | FM + Lenis + GSAP | Heavy choreography, scroll-driven, sequential reveals |
| Organic | 2/3 | FM + Lenis | Soft flowing motion, slow easing |
| Luxury | 1-2/3 | CSS + Lenis | Restrained, premium pacing, smooth scroll |
| Playful | 2-3/3 | FM | Spring physics, bouncy hover, reveal sequences |
| Industrial | 0-1/3 | CSS | Mechanical hard reveals, no organic motion |
| Art-deco | 1-2/3 | CSS + FM | Geometric reveal, slow rotation |
| Glass-tech | 2-3/3 | FM + Lenis (+ GSAP) | Smooth flowing reveals, scroll-linked |
| Hand-crafted | 0-1/3 | CSS | Subtle, no smoothness — paper aesthetic |

## Motion Personalities (Phase 2.6)

Select ONE archetype per project. Independent of intensity (0-3/3). Personality drives character (easing + duration + entrance pattern). Intensity drives amount (how much motion).

### The 4 personalities

| Personality | Signature easing | Duration palette (quick / standard / slow) | Entrance pattern | Best for vibes |
|-------------|------------------|--------------------------------------------|------------------|----------------|
| **Playful** | `ease-out-back` (10-20% overshoot) — `cubic-bezier(0.34, 1.56, 0.64, 1)` | 150ms / 250ms / 400ms | Spring bounce | Playful, Organic |
| **Premium** | `cubic-bezier(0.4, 0, 0.2, 1)` (Material Design standard) | 250ms / 400ms / 600ms | Subtle fade-up | Minimal, Editorial, Luxury, Hand-crafted |
| **Corporate** | `cubic-bezier(0.2, 0, 0, 1)` (sharp deceleration) | 200ms / 300ms / 400ms | Crisp slide | Industrial, Art-deco |
| **Energetic** | `ease-out-expo` (15-30% overshoot) — `cubic-bezier(0.16, 1, 0.3, 1)` | 100ms / 200ms / 350ms | Quick translate | Brutalist, Retro-futuristic, Glass-tech |

### Brand Motion Identity (3 locked constants)

Once personality is picked at Phase 2.6, 3 constants are LOCKED for the entire project:

1. **Signature easing** — one cubic-bezier curve used in 80% of animations. Other 20% may use neutral `ease-out` for utility transitions.
2. **Duration palette** — 3 values (quick / standard / slow). NEVER use arbitrary durations like 312ms or 583ms. All animations snap to one of these 3.
3. **Entrance pattern** — consistent reveal style. Don't mix fade-up + slide + scale randomly across the page. Pick one entrance pattern, use it everywhere.

### Personality × Intensity matrix

Personality locks character. Intensity locks scope. Both axes locked independently at Phase 2e (intensity) + Phase 2.6 (personality).

|   | Intensity 0/3 (no motion) | Intensity 1/3 (minimal) | Intensity 2/3 (moderate) | Intensity 3/3 (full) |
|---|---------------------------|--------------------------|--------------------------|----------------------|
| **Playful** | hover only | + 1 entrance per section | + scroll-linked bounce | + spring choreography |
| **Premium** | hover only | + subtle fade-up entries | + Lenis smooth scroll | + GSAP timeline reveals |
| **Corporate** | hover only | + crisp slide entrance | + scroll-linked slide | + multi-element sequences |
| **Energetic** | hover only | + quick translate snaps | + scroll-driven overshoots | + bold choreography |

### Per-vibe personality defaults

| Vibe | Default personality | Override allowed |
|------|---------------------|-------------------|
| Minimal | Premium | Yes (Energetic for tech-startup feel) |
| Editorial | Premium | Yes |
| Brutalist | Energetic | No (Corporate would betray vibe) |
| Retro-futuristic | Energetic | No |
| Organic | Playful | Yes (Premium for wellness brands) |
| Luxury | Premium | No (other personalities cheapen) |
| Playful | Playful | No |
| Industrial | Corporate | Yes (Energetic for tech-industrial) |
| Art-deco | Corporate | Yes (Premium for luxury heritage) |
| Glass-tech | Energetic | Yes (Premium for restraint) |
| Hand-crafted | Premium | Yes (Playful for whimsical) |

User confirms or overrides at Phase 2.6. Mismatches logged in `plans/{date}-{slug}/overrides.md`.

### Relationship to per-vibe easing (existing § Easing Library)

Pre-v2.4: each of 11 vibes had its own paired cubic-bezier in § Easing Library. Post-v2.4: personality drives that pairing. Each vibe gets a default personality (table above); user can override. The § Easing Library section is preserved for backward compatibility and now documents the per-personality easing curves.

## Motion Intensity Scale (0-3)

| Level | Description | Patterns allowed |
|-------|-------------|------------------|
| **0/3** | Static — no motion at all | Only `:focus-visible` + accessibility transitions |
| **1/3** | Minimal — CSS hover only | Hover lift, button transforms, link underline; first-scroll fade-in once |
| **2/3** | Moderate — entrance + smooth scroll | FM entrance animations on key sections (stagger ≤ 200ms total); Lenis smooth scroll; cursor-reactive accent |
| **3/3** | Full choreography — scroll-linked timeline | GSAP ScrollTrigger timelines; scroll-driven shader uniforms; coordinated multi-element sequences |

## Decision Tree per Category

### A. Entrance animations (element appears in viewport)

```
Element entering viewport?
├─ Intensity = 0/3 → No animation. Static render.
├─ Intensity = 1/3 → CSS keyframe one-shot at first paint only
│                    (no IntersectionObserver, no library)
├─ Intensity = 2/3 → Framer Motion `whileInView` with stagger
│                    Cap: ≤30% of sections animate; never body copy
└─ Intensity = 3/3 → FM `whileInView` + GSAP ScrollTrigger for choreographed
                     reveals across multiple elements
```

### B. Hover / focus micro-interactions

```
Element supports hover?
├─ Always → CSS only (`:hover`, `:focus-visible`).
│           No library needed regardless of intensity.
└─ Mobile/touch → No hover state. Use `:active` for touch feedback.
```

### C. Scroll-triggered effects (parallax, scroll-linked transforms)

```
Need scroll-linked motion?
├─ Intensity ≤ 1/3 → Don't add. Skip.
├─ Intensity = 2/3 → FM `useScroll` + `useTransform` for single property
│                    (e.g., header opacity, hero scale)
└─ Intensity = 3/3 → GSAP ScrollTrigger with timeline + scrub
                     for multi-element choreography
```

### D. Smooth scroll baseline

**Authoritative decision tree + per-tier setup + guards + audit moved to `smooth-scroll-flow.md`.** This section is the brief recap; consult flow file for full 5-tier ladder, escalation criteria, anchor link routing per tier, modal lock patterns, and Phase 8 audit checklist.

```
Want smooth scroll? Type-gated default at Phase 2e.1 (v2.5.1+) — apply smooth-scroll-flow.md decision matrix.

Hard guards (always force Tier 1 native scroll regardless of authored tier):
├─ prefers-reduced-motion: reduce
├─ (hover: none) and (pointer: coarse)  ← touch-primary devices
├─ motion intensity 0/3 (user explicit no-motion)
└─ CDN / module failure at runtime

Then by PAGE TYPE:

LANDING or PORTFOLIO (special tier) — TYPE-GATED auto-Lenis:
├─ Intensity 1/3 → Tier 3 (Lenis)
├─ Intensity 2/3 → Tier 3 (Lenis) — default landing/portfolio surface
└─ Intensity 3/3 → Tier 4 (Lenis + GSAP ScrollTrigger sync via gsap.ticker)
     └─ Tier 5 (ScrollSmoother) escalation if ≥2 of:
         • 3+ parallax data-speed/data-lag sections planned
         • 4+ pinned scroll-scrub sections planned
         • Club GreenSock license confirmed (PAID — always log override)

GENERIC TIER (blog / about / pricing / dashboard / admin / e-commerce / legal / coming-soon / custom):
├─ Intensity ≤ 1/3 → Tier 1 (native) — Tier 2 (CSS smooth) if anchor-heavy
├─ Intensity 2/3:
│    ├─ Atmospheric (luxury / glass-tech / organic / retro-futuristic / art-deco / playful)
│    │   → Tier 3 (Lenis)
│    └─ Restraint (minimal / editorial / brutalist / industrial / hand-crafted)
│        → Tier 1 (native) — dashboards/admin/data tables benefit from native momentum
└─ Intensity 3/3:
     ├─ Atmospheric → Tier 4
     └─ Restraint → Tier 1 (override required)

Override path: --scroll=native|css|lenis|lenis-st|smoother flag, inline brief mention,
or mid-phase AskUserQuestion when mismatch detected. All overrides logged in
plans/{slug}/overrides.md § Smooth Scroll Override.
```

**See `smooth-scroll-flow.md` for:** full setup code (vanilla + React/Next.js) per tier, Lenis + ScrollTrigger sync wiring, anchor link routing per tier, modal/dropdown stop pattern, common pitfalls (mobile rubber-band, ScrollTrigger desync, iframe blocking, sticky flicker), bundle budget table, ScrollSmoother license + override protocol, Phase 8 audit checklist (11 items).

## Approved Patterns

### A1. CSS hover lift (Tier 1 — universal)

```css
.btn {
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.btn:hover {
  transform: translateY(-2px);
}
.btn:active {
  transform: translateY(0);
  transition-duration: 0.1s;
}
```

### A2. CSS one-shot entrance (Tier 1 — intensity 1/3)

```css
@keyframes fadeUpOnce {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
.hero h1 {
  animation: fadeUpOnce 0.8s cubic-bezier(0.16, 1, 0.3, 1) both;
  /* Plays once on initial render, no IntersectionObserver */
}
```

### B1. Framer Motion entrance (Tier 2 — intensity 2/3)

```tsx
'use client';
import { motion } from 'framer-motion';

export function Section({ children }: { children: React.ReactNode }) {
  return (
    <motion.section
      initial={{ y: 40, opacity: 0 }}
      whileInView={{ y: 0, opacity: 1 }}
      viewport={{ once: true, margin: '-100px' }}
      transition={{
        duration: 0.8,
        ease: [0.16, 1, 0.3, 1], // custom easing matching vibe
      }}
    >
      {children}
    </motion.section>
  );
}
```

### B2. Framer Motion stagger reveal (Tier 2 — intensity 2-3/3)

```tsx
'use client';
import { motion } from 'framer-motion';

const containerVariants = {
  visible: { transition: { staggerChildren: 0.08 } },
};

const childVariants = {
  hidden: { y: 20, opacity: 0 },
  visible: { y: 0, opacity: 1, transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] } },
};

export function Headline() {
  return (
    <motion.h1
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true }}
      variants={containerVariants}
    >
      {'Specific value statement'.split(' ').map((word, i) => (
        <motion.span
          key={i}
          variants={childVariants}
          style={{ display: 'inline-block', marginRight: '0.25em' }}
        >
          {word}
        </motion.span>
      ))}
    </motion.h1>
  );
}
```

### C1. Lenis smooth scroll baseline (Tier 3 — intensity 2-3/3)

```tsx
// app/components/smooth-scroll.tsx
'use client';
import { ReactLenis } from '@studio-freight/react-lenis';

export function SmoothScroll({ children }: { children: React.ReactNode }) {
  return (
    <ReactLenis
      root
      options={{
        duration: 1.2,
        easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
        smoothWheel: true,
        smoothTouch: false, // do NOT smooth on touch — feels broken
      }}
    >
      {children}
    </ReactLenis>
  );
}
```

Wrap in `app/layout.tsx`:
```tsx
<body>
  <SmoothScroll>{children}</SmoothScroll>
</body>
```

### D1. Framer Motion scroll-linked transform (Tier 2 — intensity 2/3)

```tsx
'use client';
import { useScroll, useTransform, motion } from 'framer-motion';
import { useRef } from 'react';

export function ScrollLinkedHero() {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ['start start', 'end start'],
  });
  const y = useTransform(scrollYProgress, [0, 1], ['0%', '50%']);
  const opacity = useTransform(scrollYProgress, [0, 0.8], [1, 0]);

  return (
    <section ref={ref} className="relative min-h-[100dvh]">
      <motion.div style={{ y, opacity }}>
        {/* Hero content drifts up + fades on scroll */}
      </motion.div>
    </section>
  );
}
```

### D2. GSAP ScrollTrigger timeline (Tier 4 — intensity 3/3 only)

```tsx
'use client';
import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

if (typeof window !== 'undefined') {
  gsap.registerPlugin(ScrollTrigger);
}

export function ScrollChoreography() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ctx = gsap.context(() => {
      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: '#section-2',
          start: 'top 80%',
          end: 'bottom 20%',
          scrub: 1, // scrub to scroll
        },
      });

      tl.from('.element-a', { x: -100, opacity: 0, duration: 1 })
        .from('.element-b', { x: 100, opacity: 0, duration: 1 }, '-=0.5')
        .to('.element-c', { rotation: 360, duration: 2 });
    }, ref);

    return () => ctx.revert();
  }, []);

  return <div ref={ref}>{/* sections with .element-a etc. */}</div>;
}
```

## Forbidden Patterns

### F1. Generic fade-up on every element
**Why bad:** AI default. No character. Becomes invisible noise.
**Cap:** ≤30% of sections animate at intensity 2/3. ≤50% at 3/3. NEVER on body copy paragraphs.

### F2. `ease-in-out` 0.3s as universal duration
**Why bad:** Generic. Spring physics or custom cubic-beziers feel intentional.
**Replace:** `cubic-bezier(0.16, 1, 0.3, 1)` for elegant out, `cubic-bezier(0.34, 1.56, 0.64, 1)` for playful spring.

### F3. Motion on body text content
**Why bad:** Distracting. Hurts readability. Visitor can't read while it animates in.
**Rule:** Body `<p>` text never animates. Only headings, hero CTAs, section dividers.

### F4. Library imported but used <50%
**Why bad:** Bundle bloat. If you import GSAP for one timeline, that's 50KB for one effect.
**Rule:** Each tier requires ≥3 use cases. If you only need ONE scroll-triggered effect, use FM `useScroll` (Tier 2), not GSAP (Tier 4).

### F5. Multiple competing scroll libraries
**Why bad:** Fights itself, breaks scroll position, bloats bundle.
**Forbidden combos:** Lenis + Locomotive Scroll, Lenis + native smooth-scroll CSS.

### F6. Ignoring `prefers-reduced-motion`
**Why bad:** Accessibility violation. Vestibular-disorder users get sick.
**Rule:** Always check + degrade to opacity-only or skip animation.

### F7. Auto-playing video / heavy animation on mobile without throttle
**Why bad:** Battery drain, data drain, performance.
**Rule:** Mobile (<768px) auto-degrades intensity by 1 step (3→2, 2→1).

### F8. Motion library imported in landing without single use
**Why bad:** 25KB FM for nothing. Common AI generation mistake.
**Audit:** grep for `framer-motion` imports without corresponding `motion.` usage.

## Performance Guardrails

- **Bundle:** total motion JS ≤100KB gzipped (CSS = 0)
- **`prefers-reduced-motion`:**
  - Use FM's `useReducedMotion()` hook OR vanilla `matchMedia('(prefers-reduced-motion: reduce)')`
  - Degrade to opacity-only OR skip animation entirely
- **Mobile auto-degrade:** intensity − 1 at <768px viewport
- **Don't animate hero LCP element** in (push LCP past 2.5s on mobile)
- **Use `transform` + `opacity` only** (cheap GPU props). Avoid `width`, `height`, `top`, `left` animation.
- **`will-change`** sparingly — only on actively animating elements, remove after animation
- **Pause off-screen** — IntersectionObserver pause for non-essential motion when section out of viewport

## prefers-reduced-motion Implementation

```tsx
// app/components/section.tsx
'use client';
import { motion, useReducedMotion } from 'framer-motion';

export function Section({ children }: { children: React.ReactNode }) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.section
      initial={shouldReduceMotion ? false : { y: 40, opacity: 0 }}
      whileInView={shouldReduceMotion ? false : { y: 0, opacity: 1 }}
      viewport={{ once: true }}
      transition={{ duration: shouldReduceMotion ? 0 : 0.8 }}
    >
      {children}
    </motion.section>
  );
}
```

CSS equivalent:
```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

## Implementation Checklist

Before shipping any motion:
- [ ] Motion intensity locked in `visual-direction.md` (Phase 2e)
- [ ] Stack matches tier system (CSS first, escalated only as needed)
- [ ] No generic fade-up on every element (≤30% sections at 2/3 intensity)
- [ ] Custom easing or spring physics (NOT `ease-in-out` 0.3s universally)
- [ ] No motion on body `<p>` text
- [ ] `prefers-reduced-motion` respected (FM `useReducedMotion` or CSS `@media`)
- [ ] Mobile auto-degrades intensity by 1 step
- [ ] Bundle ≤100KB gz combined motion libraries
- [ ] No competing scroll libraries
- [ ] All imported motion libraries have ≥3 use cases (no single-use 50KB additions)
- [ ] LCP element NOT animated in
- [ ] Only `transform` + `opacity` for animations (cheap GPU props)

## Easing Library (paired with vibes)

| Vibe | Recommended easing | Reason |
|------|--------------------|--------|
| Minimal | `cubic-bezier(0.16, 1, 0.3, 1)` | Calm, restrained out |
| Editorial | `cubic-bezier(0.22, 1, 0.36, 1)` | Refined, paper-like |
| Brutalist | `linear` or `steps(3, end)` | Hard, mechanical |
| Retro-futuristic | `cubic-bezier(0.65, 0, 0.35, 1)` | Punchy, decisive |
| Organic | `cubic-bezier(0.4, 0, 0.6, 1)` | Slow, flowing |
| Luxury | `cubic-bezier(0.33, 1, 0.68, 1)` | Smooth, premium |
| Playful | spring physics (FM `spring` config) | Bouncy |
| Industrial | `linear` | Mechanical |
| Art-deco | `cubic-bezier(0.86, 0, 0.07, 1)` | Geometric snap |
| Glass-tech | `cubic-bezier(0.16, 1, 0.3, 1)` | Smooth, refractive |
| Hand-crafted | `cubic-bezier(0.4, 0, 0.6, 1)` or `linear` | Imperfect, paper |

## Anti-Slop Audit Checklist (motion-specific)

Run during Phase 8:

```bash
# Motion library imports
grep -rE "framer-motion|@studio-freight/react-lenis|gsap" app/ package.json

# Generic ease-in-out
grep -rE 'ease-in-out|ease-out|"easeInOut"|"easeOut"' app/components/  # flag, replace with cubic-bezier

# Default 0.3s duration (often AI default)
grep -rE 'duration:\s*0\.3|duration-300' app/  # spot-check, may be intentional

# prefers-reduced-motion respect
grep -rE 'useReducedMotion|prefers-reduced-motion' app/  # must have ≥1 hit if motion library imported
```

## GSAP skill integration (v2.4.1+)

When motion intensity hits 3/3 OR user brief contains GSAP-specific keywords (`GSAP` / `ScrollTrigger` / `scroll choreography` / `scrub` / `pin`), perfect-ui can auto-invoke official gsap-* skills (8 skills: gsap-core, gsap-scrolltrigger, gsap-react, gsap-timeline, gsap-plugins, gsap-performance, gsap-frameworks, gsap-utils) for implementation guidance.

Skills are OPTIONAL — if not installed at `~/.claude/skills/gsap-*`, perfect-ui falls back to inline patterns documented in `gsap-integration.md § Fallback inline patterns`.

Detection happens at Phase 7 entry (see `workflow-implement.md § Step 2.5`). Skill selection (which gsap-* to invoke) follows the table in `gsap-integration.md § The 8 gsap skills`.

### Quick reference

| Use case | gsap-* skill |
|----------|-------------|
| Basic tweens, easing | `gsap-core` |
| Scroll-driven animation | `gsap-scrolltrigger` |
| React / Next.js | `gsap-react` |
| Vue / Svelte | `gsap-frameworks` |
| Multi-step choreography | `gsap-timeline` |
| Flip / Draggable / SplitText / MorphSVG | `gsap-plugins` |
| Performance optimization | `gsap-performance` |
| Math / array helpers | `gsap-utils` |

For full integration logic, invocation pseudo-code, and fallback patterns, see `gsap-integration.md`.

## Cross-Reference

- Vibe palettes + typography: `visual-direction-guide.md`
- Visual effects (shaders, particles — NOT motion): `visual-effect-patterns.md`
- Anti-slop motion patterns (Tier 2): `anti-slop-rules.md` § Visual Effects + Motion
- Phase 2e Motion Intensity Lock: `workflow-phases.md`
