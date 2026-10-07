# Smooth Scroll Flow

Decision tree + implementation patterns for adding smooth scroll to generated landing/portfolio (and generic-tier) outputs. **Activated at Phase 2e.1 (after Motion Intensity Lock at Phase 2e); implemented at Phase 7; verified at Phase 8.**

Default behavior is **type-gated** — landing AND portfolio types auto-get Lenis regardless of vibe (intensity 1-3/3). Generic tier (blog / about / pricing / dashboard / admin / e-commerce / etc.) remains vibe-gated. Hard guards (prefers-reduced-motion, touch-primary, motion intensity 0/3) always force Tier 1 native. ScrollSmoother is paid (Club GreenSock) and only escalates when explicit criteria hit.

> **v2.5.1+ rule change:** Restraint-vibe gate (`minimal` / `editorial` / `brutalist` / `industrial` / `hand-crafted` → forced native) is REMOVED for landing/portfolio outputs. All landing + portfolio pages get Lenis. Generic tier still respects vibe restraint (dashboard/admin/blog with Lenis can feel weird on data-dense surfaces).

---

## 5-tier ladder

| Tier | Tool | Bundle (gz) | When |
|------|------|-------------|------|
| **1** | **Native scroll** | 0 KB | Hard-guard fallback. Touch-primary devices. `prefers-reduced-motion: reduce`. Motion intensity 0/3. Generic tier on restraint vibes. |
| **2** | **CSS `scroll-behavior: smooth`** | 0 KB | Generic tier intensity 1/3 + anchor-link-heavy site. No JS overhead. |
| **3** | **Lenis** | ~3.5 KB | **Landing / portfolio at intensity 1-2/3 (any vibe)**. Generic-tier atmospheric vibes at intensity 2/3+. Default smooth-scroll runtime. |
| **4** | **Lenis + GSAP ScrollTrigger sync** | +GSAP | Landing / portfolio at intensity 3/3. Generic-tier atmospheric at intensity 3/3. |
| **5** | **GSAP ScrollSmoother** (PAID, Club GreenSock) | ~7 KB + license | Intensity 3/3 with ≥2 of {3+ parallax `data-speed`/`data-lag` sections, 4+ pinned scroll-scrub, license confirmed}. |

**Type-gate principle (v2.5.1+):** Landing + portfolio are *the* surfaces where premium smooth feel reinforces conversion / brand impression. Skill ships Lenis on every landing/portfolio regardless of vibe (editorial coffee site demos this). Generic tier (blog/dashboard/etc.) still respects vibe restraint — restraint vibes on app surfaces benefit from native momentum scroll.

---

## Decision matrix (auto-applied at Phase 2e.1)

**Input:** page type (landing / portfolio / generic) + motion intensity (0-3/3 from Phase 2e) + vibe anchor (from Phase 1, generic tier only) + runtime detection.

### Step 1 — Hard guards (always force Tier 1 native)

These checks override any authored tier. Non-negotiable a11y/UX/intent guards:

1. `prefers-reduced-motion: reduce` → **Tier 1** (a11y)
2. `(hover: none) and (pointer: coarse)` → **Tier 1** (touch-primary devices use native momentum)
3. Motion intensity **0/3** → **Tier 1** (user explicitly chose no motion)
4. CDN / module failure at runtime → **Tier 1** (graceful fallback)

Forced into generated code, never bypassed.

### Step 2a — Landing OR Portfolio (special tier) — TYPE-GATED auto-Lenis

| Motion intensity | Tier |
|---|---|
| **0/3** | **1** (native) — hard guard fires first |
| **1/3** | **3** (Lenis) — minimal smooth feel, no GSAP needed |
| **2/3** | **3** (Lenis) — default landing/portfolio surface |
| **3/3** | **4** (Lenis + GSAP ScrollTrigger sync) — check Tier 5 escalation gate |

**Vibe is NOT gated here.** Editorial coffee landing, brutalist sales page, minimal portfolio — all get Lenis. Type alone decides.

### Step 2b — Generic tier (blog / about / pricing / contact / dashboard / admin / e-commerce / legal / coming-soon / custom) — VIBE-GATED

| Motion intensity | Vibe set | Tier |
|---|---|---|
| **0/3** | any | **1** (native) |
| **1/3** | any | **1** (native) — OR **2** (CSS smooth) IF anchor-link-heavy site (3+ in-page anchors) |
| **2/3** | atmospheric: `luxury` / `glass-tech` / `organic` / `retro-futuristic` / `art-deco` / `playful` | **3** (Lenis) |
| **2/3** | restraint: `minimal` / `editorial` / `brutalist` / `industrial` / `hand-crafted` | **1** (native) — dashboards / data tables / app surfaces benefit from native momentum |
| **3/3** | atmospheric | **4** (Lenis + ScrollTrigger sync), check Tier 5 escalation criteria |
| **3/3** | restraint | **1** (native) — override required |

### Step 2c — Component-scope (per `component-scope.md`)

Component-scope outputs skip Phase 7 macro-shell; no scroll runtime emitted regardless of tier. Component's parent page decides its own tier.

### Step 3 — ScrollSmoother (Tier 5) escalation gate

After landing on Tier 4, auto-suggest Tier 5 ONLY when **≥2 of these** hit during Phase 6 plan inspection:

1. **Parallax inventory** — Page plan includes 3+ sections with element-level `data-speed`/`data-lag` parallax requirements
2. **Pinned scroll-scrub** — 4+ sections planned with `ScrollTrigger { pin: true, scrub: true }`
3. **License confirmed** — User explicitly confirms Club GreenSock license available at Phase 1 brief

0-1 criteria hit → stay on Tier 4. Lenis + ScrollTrigger covers basic parallax + scroll-scrub without paid plugin.

### Step 4 — User override

Override the auto-applied tier at any phase via:
- CLI flag: `--scroll=native | css | lenis | lenis-st | smoother`
- Inline brief mention: "I have GSAP Business license, prefer ScrollSmoother"
- Mid-phase `AskUserQuestion` when skill detects tier mismatch (e.g., user requests smoother on brutalist)

All overrides logged in `plans/{date}-{slug}/overrides.md § Smooth Scroll Override` with reason.

### Step 5 — Save decision

Write final tier choice to `plans/{date}-{slug}/visual-direction.md`:

```markdown
## Smooth Scroll
- Tier: 3 (Lenis)
- Reason: motion 2/3 + atmospheric vibe (glass-tech)
- Guards: prefers-reduced-motion + touch-primary hard-skip
- Anchor routing: lenis.scrollTo with offset
```

---

## Tier 1 — Native scroll

**Hard-guard fallback** (touch + reduced-motion + intensity 0/3 + CDN failure). Also default for generic-tier restraint vibes. Zero JS, zero CSS overhead.

### Setup
- No init code
- HTML: `<a href="#section-id">` links use browser default
- CSS: add `scroll-margin-top` to anchor targets so sticky nav doesn't cover them

```css
/* Targets get scroll-margin so sticky nav clears */
[id] { scroll-margin-top: calc(var(--nav-height) + 12px); }
```

### Anchor link routing
- Pure native `<a href="#target">` — browser handles it
- For programmatic scroll: `target.scrollIntoView({ behavior: 'auto' })`

### When to pick this
- Editorial / brutalist / minimal / industrial / hand-crafted vibes (the curated default)
- Mobile / touch devices
- Users with `prefers-reduced-motion`
- Bundle-budget-constrained projects

---

## Tier 2 — CSS `scroll-behavior: smooth`

**Zero JS overhead.** Adequate when only need smooth anchor-link transitions, no programmatic scroll choreography.

### Setup

```css
html {
  scroll-behavior: smooth;
}

[id] {
  scroll-margin-top: calc(var(--nav-height) + 12px);
}

/* Honor reduced motion */
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
}
```

### Anchor link routing
- `<a href="#target">` — CSS handles smooth scroll

### Limitations
- Only smooths anchor-link clicks, NOT wheel/touch scroll
- No GSAP ScrollTrigger integration
- No element-level lag/parallax

### When to pick this
- Intensity 1/3 + anchor-heavy site (3+ in-page anchors) where wheel scroll smoothness isn't needed

---

## Tier 3 — Lenis

**Default for landing/portfolio at intensity 1-2/3 (any vibe).** Also default for generic-tier atmospheric vibes at intensity 2/3+. Lightweight (~3.5 KB gz), free, MIT.

### Install (vanilla HTML)

```html
<script src="https://cdn.jsdelivr.net/npm/lenis@1.1.13/dist/lenis.min.js"></script>
```

### Install (npm)

```bash
npm install lenis
```

### Setup code (vanilla — `js/lenis.js`)

```js
(function () {
  'use strict';

  // Hard guards
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.documentElement.classList.add('lenis-disabled');
    return;
  }
  if (window.matchMedia('(hover: none) and (pointer: coarse)').matches) {
    document.documentElement.classList.add('lenis-disabled');
    return;
  }
  if (typeof window.Lenis === 'undefined') {
    document.documentElement.classList.add('lenis-disabled');
    return;
  }

  const lenis = new Lenis({
    lerp: 0.1,             // subtle, premium feel
    duration: 1.2,
    smoothWheel: true,
    syncTouch: false,      // hard-skip touch
    easing: t => Math.min(1, 1.001 - Math.pow(2, -10 * t))
  });

  window.__lenis = lenis;

  function raf(time) {
    lenis.raf(time);
    requestAnimationFrame(raf);
  }
  requestAnimationFrame(raf);
})();
```

### Setup code (React / Next.js — `components/SmoothScrollProvider.tsx`)

```tsx
'use client';

import { useEffect } from 'react';
import Lenis from 'lenis';

export function SmoothScrollProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (window.matchMedia('(hover: none) and (pointer: coarse)').matches) return;

    const lenis = new Lenis({
      lerp: 0.1,
      duration: 1.2,
      smoothWheel: true,
      syncTouch: false
    });

    (window as any).__lenis = lenis;

    let rafId: number;
    function raf(time: number) {
      lenis.raf(time);
      rafId = requestAnimationFrame(raf);
    }
    rafId = requestAnimationFrame(raf);

    return () => {
      cancelAnimationFrame(rafId);
      lenis.destroy();
      delete (window as any).__lenis;
    };
  }, []);

  return <>{children}</>;
}
```

Wrap your app:
```tsx
// app/layout.tsx
<SmoothScrollProvider>{children}</SmoothScrollProvider>
```

### Required CSS

```css
html.lenis, html.lenis body { height: auto; }
.lenis.lenis-smooth { scroll-behavior: auto !important; }
.lenis.lenis-smooth [data-lenis-prevent] { overscroll-behavior: contain; }
.lenis.lenis-stopped { overflow: hidden; }
.lenis.lenis-scrolling iframe { pointer-events: none; }
html.lenis-disabled { scroll-behavior: smooth; }
```

### Anchor link routing

```js
document.querySelectorAll('a[href^="#"]').forEach(link => {
  link.addEventListener('click', e => {
    const hash = link.getAttribute('href');
    if (hash === '#' || hash.length < 2) return;
    const target = document.querySelector(hash);
    if (!target) return;
    e.preventDefault();

    if (window.__lenis?.scrollTo) {
      window.__lenis.scrollTo(target, { offset: -NAV_HEIGHT, duration: 1.4 });
    } else {
      window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - NAV_HEIGHT, behavior: 'smooth' });
    }
  });
});
```

### Modal / dropdown lock

Use MutationObserver to pause Lenis when modal opens:

```js
new MutationObserver(() => {
  if (document.body.classList.contains('scroll-locked')) {
    lenis.stop();
  } else {
    lenis.start();
  }
}).observe(document.body, { attributes: true, attributeFilter: ['class'] });
```

---

## Tier 4 — Lenis + GSAP ScrollTrigger sync

**For intensity 3/3 with scroll-driven motion** (pinned sections, scrub timelines, scroll-linked transforms).

### Setup

Same Lenis init as Tier 3, plus GSAP integration:

```js
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

// Drive Lenis from GSAP's ticker (single rAF, smoother sync)
gsap.ticker.add(time => lenis.raf(time * 1000));
gsap.ticker.lagSmoothing(0);

// Keep ScrollTrigger in sync with Lenis's virtual scroll position
lenis.on('scroll', ScrollTrigger.update);
```

After this wire-up:
- All `ScrollTrigger` instances fire correctly during smooth scroll
- Pinned sections work without jitter
- Scrub timelines stay in sync
- No double-rAF (single GSAP ticker drives both)

### Common pattern — scroll-linked reveal

```js
gsap.utils.toArray('.reveal').forEach(el => {
  gsap.from(el, {
    y: 40,
    opacity: 0,
    scrollTrigger: {
      trigger: el,
      start: 'top 85%',
      once: true
    }
  });
});
```

### When to escalate to Tier 5

After Phase 6 plan, count parallax sections + pinned scroll-scrub sections. If ≥2 of 3 criteria hit → propose Tier 5 to user.

---

## Tier 5 — GSAP ScrollSmoother (PAID, Club GreenSock)

**Escalation tier.** Requires Club GreenSock license. Provides element-level lag/parallax via attributes, scroll velocity tracking, normalizeScroll for cross-browser consistency.

### Mandatory pre-check

Skill MUST verify ≥2 escalation criteria before suggesting Tier 5. If not met, stay on Tier 4.

### Mandatory override log

Before code emission, write to `plans/{date}-{slug}/overrides.md`:

```markdown
## ScrollSmoother Escalation
- Criteria hit:
  - [x] 3+ parallax sections with data-speed/data-lag
  - [x] 4+ pinned scroll-scrub sections
  - [ ] License confirmed at brief
- License: Club GreenSock confirmed by user at Phase 1 (date)
- Bundle impact: +7 KB ScrollSmoother + ~25 KB GSAP core + 14 KB ScrollTrigger = 46 KB total (within ≤100 KB motion budget)
- Reviewed by user: yes
```

### Setup

```js
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { ScrollSmoother } from 'gsap/ScrollSmoother';

gsap.registerPlugin(ScrollTrigger, ScrollSmoother);

if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
if (window.matchMedia('(hover: none) and (pointer: coarse)').matches) return;

const smoother = ScrollSmoother.create({
  wrapper: '#smooth-wrapper',
  content: '#smooth-content',
  smooth: 1.2,             // smoothing duration
  effects: true,           // enables data-speed and data-lag
  normalizeScroll: true    // cross-browser scroll consistency
});

window.__smoother = smoother;
```

### HTML structure (mandatory wrapper)

```html
<body>
  <div id="smooth-wrapper">
    <div id="smooth-content">
      <!-- all page content goes here -->
      <header>...</header>
      <main>...</main>
      <footer>...</footer>
    </div>
  </div>
</body>
```

### Element-level lag/parallax

```html
<img data-speed="0.8" src="..." />     <!-- moves at 80% scroll speed (parallax up) -->
<img data-speed="1.2" src="..." />     <!-- moves at 120% (parallax down) -->
<h1 data-lag="0.5">Title</h1>          <!-- lags 0.5s behind scroll -->
<div data-speed="auto">                <!-- auto-calculated for smooth entry -->
  ...
</div>
```

### Anchor link routing

```js
ScrollSmoother.get().scrollTo(target, true /* smooth */);
```

### Limitations
- **Paid plugin** — cannot ship without confirmed license
- Adds ~7 KB to bundle
- Requires HTML restructure (wrapper + content divs)
- More complex teardown

---

## Mandatory guards (all JS tiers 3-5)

### 1. `prefers-reduced-motion` (a11y, non-negotiable)

```js
if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
```

Skip Lenis/ScrollSmoother init entirely. Native scroll fallback. No exceptions.

### 2. Touch-primary detection (UX)

```js
if (window.matchMedia('(hover: none) and (pointer: coarse)').matches) return;
```

Native momentum scroll on mobile feels better than Lenis. Hard-skip.

*Optional softer alternative:* Pass `syncTouch: false` to Lenis (Lenis only affects wheel scroll, touch stays native). Per skill default: hard-skip is recommended.

### 3. CDN / module failure

```js
if (typeof window.Lenis === 'undefined') return;        // Tier 3-4
if (typeof window.ScrollSmoother === 'undefined') return; // Tier 5
```

Graceful fallback to native scroll if module fails to load.

### 4. Modal / dropdown scroll-lock

Body scroll under open modals = bug. Pause smooth-scroll runtime:

```js
// Lenis (Tier 3-4)
if (modalOpen) lenis.stop(); else lenis.start();

// ScrollSmoother (Tier 5)
if (modalOpen) smoother.paused(true); else smoother.paused(false);
```

Or via MutationObserver watching `body.scroll-locked` class.

---

## Anchor link routing (per tier)

| Tier | Code | Notes |
|------|------|-------|
| **1** | Native `<a href="#x">` + CSS `scroll-margin-top` | No JS |
| **2** | Native + CSS `scroll-behavior: smooth` | No JS |
| **3** | `lenis.scrollTo(target, { offset: -NAV, duration: 1.4 })` | JS click handler |
| **4** | Same as Tier 3 — ScrollTrigger auto-syncs via `lenis.on('scroll')` | — |
| **5** | `ScrollSmoother.get().scrollTo(target, true)` | — |

---

## Common pitfalls

1. **Mobile rubber-band on Lenis** — `syncTouch: true` feels weird on iOS. **Always `syncTouch: false`** (or hard-skip).
2. **GSAP ScrollTrigger desync** — Forgot `lenis.on('scroll', ScrollTrigger.update)`. Pinned sections jitter, scrub timelines drift.
3. **Double rAF loop** — Created own `requestAnimationFrame` loop AND called `gsap.ticker.add(lenis.raf)`. Choose one (prefer ticker for sync).
4. **Modal scroll leak** — Modal opens, body still scrolls behind. Use `lenis.stop()` or `smoother.paused(true)`.
5. **Tab visibility drain** — Lenis keeps rAF in background tab → battery drain. Add `document.visibilityState !== 'visible'` check to pause.
6. **Iframe interaction blocked** — Lenis catches all wheel events. Add `data-lenis-prevent` to iframe wrapper OR style `iframe { pointer-events: auto }` during scroll.
7. **Sticky element flicker** — `position: sticky` can jitter with Lenis. Test; consider using ScrollTrigger pinning instead.
8. **ScrollSmoother wrapper missing** — Forgot `#smooth-wrapper > #smooth-content` HTML. Plugin silently fails.
9. **Lenis breaks `scroll-margin-top`** — Lenis virtual scroll doesn't honor CSS scroll-margin. Compute offset manually in `lenis.scrollTo({ offset: -NAV })`.
10. **Editorial vibe with Lenis** — Anti-slop violation. Editorial vibes default to Tier 1; user must explicitly override.

---

## Bundle budget

Per perfect-ui Hard Rule 7 (motion intensity respects budget) and motion-patterns.md § Stack escalation: total motion JS ≤ **100 KB gz** for any output.

| Tier | Tool | Bundle (gz) | Cumulative |
|------|------|-------------|------------|
| 1 | Native | 0 KB | 0 |
| 2 | CSS smooth | 0 KB | 0 |
| 3 | Lenis 1.1.x | ~3.5 KB | 3.5 |
| 4 | Lenis + GSAP core + ScrollTrigger | ~3.5 + 25 + 14 KB | 42.5 |
| 5 | + ScrollSmoother | +7 KB | 49.5 + license |

Even Tier 5 stays within 100 KB budget — but **PAID license requirement** is the gating factor, not bundle size.

---

## Phase 8 audit checklist

For any output where Phase 2e.1 chose Tier ≥ 2, verify at Phase 8:

- [ ] **Reduced-motion guard** present and tested with browser devtools emulation
- [ ] **Touch-primary guard** present (hard-skip OR `syncTouch: false`)
- [ ] **CDN failure fallback** — page works if smooth-scroll runtime fails to load
- [ ] **GSAP ScrollTrigger sync** (Tier 4-5) — pinned sections don't jitter, scrub timelines stay in sync
- [ ] **Anchor links** route through smooth-scroll API (`lenis.scrollTo` / `ScrollSmoother.scrollTo`), NOT raw `window.scrollTo`
- [ ] **Modal/dropdown** pauses smooth-scroll runtime when scroll-locked
- [ ] **Iframe** not blocking (test with embedded YouTube / map / etc.)
- [ ] **Bundle size** total motion JS ≤ 100 KB gz
- [ ] **ScrollSmoother license** (Tier 5 only) — logged in `plans/{slug}/overrides.md`
- [ ] **Sticky elements** don't flicker (manual visual test)
- [ ] **Visibility throttle** — lenis pauses in background tab (optional but recommended)

---

## Override protocol

### CLI flag
```bash
/perfect-ui ... --scroll=native | css | lenis | lenis-st | smoother
```

### Inline brief override
User mentions in brief: *"I have GSAP Business license, prefer ScrollSmoother"* → skill confirms criteria at Phase 6 plan, escalates if met.

### Mid-phase mismatch override
If user requests `smoother` on a generic-tier restraint vibe (e.g., dashboard + brutalist), skill runs `AskUserQuestion`:
> Smooth scroll on editorial vibe contradicts the curated default (0/7 human editorial landings analyzed use smooth scroll). Confirm override?
> - Yes — log override + reason
> - No — fall back to vibe-gated tier

### Override log format

`plans/{date}-{slug}/overrides.md`:

```markdown
## Smooth Scroll Override
- Auto-picked tier: 1 (native, editorial vibe)
- User-requested tier: 5 (ScrollSmoother)
- Reason: "Client has Club GreenSock license, wants premium feel for product launch"
- License confirmed: yes (date 2026-MM-DD)
- Criteria check: 1/3 hit (only license confirmed) — escalation criteria NOT independently met
- Approved: yes, by user request
```

---

## Cross-references

- Vibe matrix + motion intensity defaults: `references/motion-patterns.md`
- Brand Motion Identity (signature easing per personality): `references/motion-patterns.md § Motion Personalities`
- GSAP skill integration (auto-detect official gsap-* skills): `references/gsap-integration.md`
- Phase 7 implementation protocol: `references/workflow-implement.md`
- Phase 8 audit protocol: `references/workflow-audit.md`
- Anti-slop rules + Augen/Paperclip restraint pattern: `references/anti-slop-rules.md`
