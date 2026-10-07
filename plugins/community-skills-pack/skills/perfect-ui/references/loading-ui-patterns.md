# Loading UI Patterns

When and how to use loading screens / splash states on landings and portfolios. Derived from comparison of 12 real landings (see `plans/260509-ai-vs-human-analysis/synthesis.md`) — confirmed: 0/5 AI pages had intentional loading UI; 4/7 human pages had splash or scroll-triggered reveal.

## Core Principle

**A loading screen must EARN its place.** It exists to serve a function, not as decoration. AI-generated pages skip splash entirely (or use generic spinner); human-crafted pages use splash deliberately when content/assets justify it.

## Decision Tree — Should this site have a splash?

```
Does the page have ≥1 of these conditions?
├─ Heavy assets that take >1.5s to be ready
│   (3D scenes, hero video, large hero image, GLB models, web fonts)
│   → YES, use asset-masking splash
├─ Brand wants a "first impression" moment
│   (Marblex giant logotype on entry — sets vibe before any content)
│   → YES, use brand-moment splash
├─ Content needs expectation-setting
│   (Paperclip / Augen — complex products that need "wait, take this in")
│   → YES, use anticipation splash (quiet, brief)
├─ Portfolio needs animated reveal entrance
│   (Isadeburgh — scroll-triggered storytelling, splash sets vibe)
│   → YES, use intro splash
└─ None of the above
    → NO splash. Restraint. Page renders directly.
```

**Default = NO splash.** Restraint is more confident than decoration.

## Approved Patterns

### A1. Asset-masking splash
**When:** hero has heavy assets that aren't ready immediately (3D scene, hero video, large image, web font).

**Pattern:**
- Show vibe-consistent placeholder (logotype, monogram, brand color block)
- Auto-hide when `window.load` fires OR when critical asset emits `loaded` event
- Max duration: 2.5s. After that, force-hide and show whatever rendered.

**Implementation (Lenis + opacity fade):**
```tsx
'use client';
import { useEffect, useState } from 'react';

export const AssetMaskSplash = ({ children }: { children: React.ReactNode }) => {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const onLoad = () => setReady(true);
    if (document.readyState === 'complete') setReady(true);
    else window.addEventListener('load', onLoad);

    // Force-hide after max duration
    const timeout = setTimeout(() => setReady(true), 2500);

    return () => {
      window.removeEventListener('load', onLoad);
      clearTimeout(timeout);
    };
  }, []);

  return (
    <>
      <div
        className="fixed inset-0 z-[100] bg-bg flex items-center justify-center transition-opacity duration-700 pointer-events-none"
        style={{ opacity: ready ? 0 : 1, visibility: ready ? 'hidden' : 'visible' }}
        aria-hidden="true"
      >
        <BrandMark size={64} />
      </div>
      {children}
    </>
  );
};
```

### A2. Brand-moment splash
**When:** brand identity is striking enough that a 1-2s entry moment AMPLIFIES first impression. Marblex pattern — giant chunky logotype on light gray, full-viewport.

**Pattern:**
- 1-2s deliberate display of branded element (logotype, logomark, type)
- Cut or fade to main content
- NO spinner, NO progress bar, NO loading text
- Becomes part of brand language

**Implementation:**
```tsx
'use client';
import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

export const BrandMomentSplash = ({ children }: { children: React.ReactNode }) => {
  const [show, setShow] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setShow(false), 1400);
    return () => clearTimeout(t);
  }, []);

  return (
    <>
      <AnimatePresence>
        {show && (
          <motion.div
            className="fixed inset-0 z-[100] bg-bg flex items-center justify-center"
            initial={{ opacity: 1 }}
            exit={{ opacity: 0, transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] } }}
          >
            <div className="font-display text-[20vw] leading-none text-ink">
              {/* Brand logotype here — full viewport scale */}
              BRAND
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      {children}
    </>
  );
};
```

### A3. Anticipation / expectation-setting splash
**When:** complex product needs visitor to slow down before consuming content.

**Pattern:**
- Brief, quiet
- Often pairs with a single line of copy ("Loading [specific thing]...")
- Sets pace, not branding
- 0.8-1.5s

### A4. Scroll-triggered intro reveal
**When:** portfolio or editorial landing where the entrance IS the navigation.

**Pattern:**
- No traditional splash — page loads invisibly
- First scroll triggers GSAP / Lenis animation that introduces the page
- Each scroll movement reveals next content block
- Isadeburgh pattern

**Implementation outline (GSAP ScrollTrigger):**
```tsx
'use client';
import { useEffect } from 'react';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

export const useScrollIntro = () => {
  useEffect(() => {
    const tl = gsap.timeline();
    tl.from('[data-intro="word"]', {
      y: 40, opacity: 0, stagger: 0.08, duration: 0.8,
      ease: 'power3.out',
    });
    return () => { tl.kill(); };
  }, []);
};
```

## Forbidden Patterns

### F1. Generic spinner without context
- Default `<div class="loading">⟳</div>` rotating circle
- Material Design's circular progress indicator
- Bootstrap-style throbber
- Three-bouncing-dots animation

**Reason:** signals "I didn't think about this state." Use brand-consistent vis or skip splash.

### F2. Fake progress bar
- Bar that fills 0→100% in ~1s without representing real load progress
- Lies to the user about what's happening
- Worse than no splash

### F3. "Loading..." text alone
- Text-only "Loading..." or "Please wait..." with no visual
- Reads as broken state, not intentional design

### F4. Skeleton screens for the entire page
- Skeleton placeholders for hero (gray rectangles where images go)
- Appropriate for app-like surfaces (dashboards), NOT marketing landings
- A landing with full-page skeleton signals "this isn't ready"

### F5. Auto-playing splash video with audio
- Forces sound on first load → hostile to visitor
- Always muted by default if video used at all

### F6. Splash > 3 seconds without explicit reason
- After 3s, visitor questions if site is broken
- Hard cap; force-hide regardless of asset state

### F7. Splash blocking interaction without escape
- Visitor can't tab away, can't skip, can't dismiss
- Always allow click-to-skip or auto-hide on first interaction

## Patterns Observed in Real Sites (evidence-based)

| Site | Pattern observed | Why it works |
|------|-----------------|--------------|
| **Marblex** | Brand-moment splash (giant GT Maru Rounded logotype on light gray, full viewport) | Sets vibe before complex Web3 site loads; logotype IS the brand |
| **Cuimao** (portfolio) | Custom `<loader>` element with spinner + label, hides on load | Portfolio with heavy zine-style imagery; mask justifies splash |
| **Isadeburgh** (portfolio) | Scroll-triggered reveal entrance (GSAP + Lenis) — page loads silent, scroll initiates story | Portfolio narrative requires curated entrance |
| **Augen** | NO splash — page loads directly to elegant hero | Restraint matches elegant vibe; assets are optimized |
| **Paperclip** | NO splash — page loads directly | Quiet confidence; visual carries weight |
| **Owo** | NO splash — bold ransom-note hero loads instantly | Bold vibe = no waiting |
| **AI pages (5)** | NONE had intentional splash — but ALSO had no animation choreography | AI defaults to "render and done"; misses opportunity but at least doesn't slop the splash |

## Type Considerations

### For landing pages
- Asset-masking splash if hero has 3D / video / heavy media
- Brand-moment splash IF brand is bold enough to support it (most landings should NOT use brand-moment)
- Default to NO splash

### For portfolios
- Scroll-triggered intro reveal is most common human pattern
- Brand-moment splash only if owner's brand mark is genuinely strong
- Default to NO splash; let the work speak immediately

## Performance Guardrails

- Splash bundle size ≤ 30KB JS (excluding fonts)
- Splash element should NOT block hero asset loading — preload hero in parallel
- Set `aria-hidden="true"` on splash so screen readers skip
- Honor `prefers-reduced-motion` — fade instead of complex animation
- LCP measurement: splash should NOT count as LCP. Hide before hero text would be visible.

## Implementation Checklist

Before shipping any splash:
- [ ] Splash duration capped at 2.5s (asset-mask) or 1.4s (brand-moment)
- [ ] Force-hide timeout exists (no infinite splash if asset fails)
- [ ] `aria-hidden="true"` set
- [ ] `prefers-reduced-motion` respected
- [ ] First paint of splash within 200ms (no blank-then-splash flash)
- [ ] Hero LCP target unchanged (splash doesn't push LCP past 2.5s on mobile)
- [ ] Bundle includes splash code in critical CSS / inline JS (no waterfall)

## Decision Reference

```
About to add a loading state? Ask:
1. Is there a measurable asset that needs >1.5s to load? → Asset-mask
2. Does the brand have a moment-worthy mark? → Brand-moment (sparingly)
3. Is this a portfolio with curated narrative entrance? → Scroll-trigger intro
4. None of above? → SKIP. Page loads directly.
```

When in doubt: skip splash. AI pages skip it because they don't think about it; the best human pages also skip it because restraint is confident.
