# Design Read, Dials & Honest Design Systems

> Source: taste-skill (Leon). How to read a brief before designing, tune three intensity dials, pick a real design system instead of faking one, and implement scroll choreography correctly.

## 0. BRIEF INFERENCE (Read the Room Before Anything Else)

Before touching code or tweaking dials, **infer what the user actually wants**. Most LLM design output is bad because the model jumps to a default aesthetic instead of reading the room.

### 0.A Read these signals first
1. **Page kind** - landing (SaaS / consumer / agency / event), portfolio (dev / designer / creative studio), redesign (preserve vs overhaul), editorial / blog.
2. **Vibe words** the user used - "minimalist", "calm", "Linear-style", "Awwwards", "brutalist", "premium consumer", "Apple-y", "playful", "serious B2B", "editorial", "agency-y", "glassy", "dark tech".
3. **Reference signals** - URLs they linked, screenshots they pasted, products they named, brands they're competing with.
4. **Audience** - B2B procurement panel vs. design-conscious consumer vs. recruiter scanning a portfolio. The audience picks the aesthetic, not your taste.
5. **Brand assets that already exist** - logo, color, type, photography. For redesigns, these are starting material, not optional input (see Section 11).
6. **Quiet constraints** - accessibility-first audiences, public-sector, regulated industries, trust-first commerce, kids' products. These constraints OVERRIDE aesthetic preference.

### 0.B Output a one-line "Design Read" before generating
Before any code, state in one line: **"Reading this as: \<page kind> for \<audience>, with a \<vibe> language, leaning toward \<design system or aesthetic family>."**

Example reads:
- *"Reading this as: B2B SaaS landing for technical buyers, with a Linear-style minimalist language, leaning toward Tailwind utilities + Geist + restrained motion."*
- *"Reading this as: solo designer portfolio for hiring managers, with an editorial / kinetic-type language, leaning toward native CSS + scroll-driven animation + custom typography."*
- *"Reading this as: redesign of a public-sector service site, with a trust-first language, leaning toward GOV.UK Frontend or USWDS."*

### 0.C If the brief is ambiguous, ask one question, do not guess
Ask exactly **one** clarifying question - never a multi-question dump - and only when the design read genuinely diverges. Example: *"Should this feel closer to Linear-clean or Awwwards-experimental?"*

If you can confidently infer from context, **do not ask**. Just declare the design read and proceed.

### 0.D Anti-Default Discipline
Do not default to: AI-purple gradients, centered hero over dark mesh, three equal feature cards, generic glassmorphism on everything, infinite-loop micro-animations everywhere, Inter + slate-900. These are the LLM defaults. Reach past them deliberately based on the design read.

---

## 1. THE THREE DIALS (Core Configuration)

After the design read, set three dials. Every layout, motion, and density decision below is gated by these.

* **`DESIGN_VARIANCE: 8`** - 1 = Perfect Symmetry, 10 = Artsy Chaos
* **`MOTION_INTENSITY: 6`** - 1 = Static, 10 = Cinematic / Physics
* **`VISUAL_DENSITY: 4`** - 1 = Art Gallery / Airy, 10 = Cockpit / Packed Data

**Baseline:** `8 / 6 / 4`. Use these unless the design read overrides them. Do not ask the user to edit this file - overrides happen conversationally.

### 1.A Dial Inference (design read → dial values)
| Signal | VARIANCE | MOTION | DENSITY |
|---|---|---|---|
| "minimalist / clean / calm / editorial / Linear-style" | 5-6 | 3-4 | 2-3 |
| "premium consumer / Apple-y / luxury / brand" | 7-8 | 5-7 | 3-4 |
| "playful / wild / Dribbble / Awwwards / experimental / agency" | 9-10 | 8-10 | 3-4 |
| "landing page / portfolio / marketing site (default)" | 7-9 | 6-8 | 3-5 |
| "trust-first / public-sector / regulated / accessibility-critical" | 3-4 | 2-3 | 4-5 |
| "redesign - preserve" | match existing | +1 | match existing |
| "redesign - overhaul" | +2 | +2 | match existing |

### 1.B Use-Case Presets
| Use case | VARIANCE | MOTION | DENSITY |
|---|---|---|---|
| Landing (SaaS, mainstream) | 7 | 6 | 4 |
| Landing (Agency / creative) | 9 | 8 | 3 |
| Landing (Premium consumer) | 7 | 6 | 3 |
| Portfolio (Designer / studio) | 8 | 7 | 3 |
| Portfolio (Developer) | 6 | 5 | 4 |
| Editorial / Blog | 6 | 4 | 3 |
| Public-sector service | 3 | 2 | 5 |
| Product UI / dashboard (Operate) | 3-5 | 3-4 | 5-7 |
| Redesign - preserve | match | match+1 | match |
| Redesign - overhaul | +2 | +2 | match |

### 1.C How the Dials Drive Output
Use these (or user-overridden values) as global variables. Cross-references throughout this document refer to these exact variable names - never invent aliases like `LAYOUT_VARIANCE` or `ANIM_LEVEL`.


---

## 2. BRIEF → DESIGN SYSTEM MAP

Once you have the design read (Section 0) and dials (Section 1), pick the right foundation. Do not invent CSS for things that have an official package. Do not pretend an aesthetic trend is an official system.

### 2.A When to reach for a real design system (use official packages)
| Brief reads as… | Reach for | Why |
|---|---|---|
| Microsoft / enterprise SaaS / dashboards | `@fluentui/react-components` or `@fluentui/web-components` | Official Fluent UI, Microsoft tokens, accessibility done |
| Google-ish UI, Material-flavored product | `@material/web` + Material 3 tokens | Official, theme-able via Material Theming |
| IBM-style B2B / enterprise analytics | `@carbon/react` + `@carbon/styles` | Official Carbon, mature data-density patterns |
| Shopify app surfaces | `polaris.js` web components / Polaris React | Required for Shopify admin UI |
| Atlassian / Jira-style product | `@atlaskit/*` + `@atlaskit/tokens` | Official Atlassian DS |
| GitHub-style devtool / community page | `@primer/css` or `@primer/react-brand` | Official Primer; Brand variant for marketing |
| Public-sector UK service | `govuk-frontend` | Legally / regulatorily expected |
| US public-sector / trust-first | `@uswds/uswds` | Same |
| Fast local-business / agency MVP | Bootstrap 5.3 | Boring, fast, works |
| Modern accessible React foundation | `@radix-ui/themes` | Primitives + polished theme |
| Modern SaaS where you own the components | shadcn/ui (`npx shadcn@latest add ...`) | You own the code, easy to customise; never ship default state |
| Tailwind-based modern SaaS / AI marketing | Tailwind v4 utilities + `dark:` variant | Default for indie + small team builds |

**Honesty rule:** if the brief reads as one of the systems above, install and use the **official** package. Do not recreate its CSS by hand. Do not import a system's tokens but then override 90% of them.

**One system per project.** Do not mix Fluent React with Carbon in the same tree. Do not import shadcn/ui components into a Material 3 app.

### 2.B When the brief is an aesthetic, not a system
For these directions, there is **no single official package**. Build with native CSS + Tailwind + a maintained component library. Be honest in code comments about what is borrowed inspiration vs. official material.

| Aesthetic | Honest implementation |
|---|---|
| Glassmorphism / "frosted glass" | `backdrop-filter`, layered borders, highlight overlays. Provide solid-fill fallback for `prefers-reduced-transparency`. |
| Bento (Apple-style tile grids) | CSS Grid with mixed cell sizes. No single library owns this. |
| Brutalism | Native CSS, monospace, raw borders. No library. |
| Editorial / magazine | Serif type, asymmetric grid, generous whitespace. No library. |
| Dark tech / hacker | Mono + accent neon, terminal motifs. No library. |
| Aurora / mesh gradients | SVG or layered radial gradients. No library. |
| Kinetic typography | Native CSS animations, scroll-driven animations, GSAP for hijacks. No library. |
| **Apple Liquid Glass** | Apple documents this for Apple platforms only. **There is no official `liquid-glass.css`.** Web implementations are approximations using `backdrop-filter` + layered borders + highlights. Label clearly as approximation. |

---

---

## 3. DEFAULT ARCHITECTURE & CONVENTIONS

Unless the design read picks a real design system (Section 2.A), these are the defaults when the brief is a React/Next app. For a different stack, translate the same conventions (styling approach, animation library, icon set) to that framework.

### 3.A Stack
* **Framework:** React or Next.js. Default to Server Components (RSC).
  * **RSC SAFETY:** Global state works ONLY in Client Components. In Next.js, wrap providers in a `"use client"` component.
  * **INTERACTIVITY ISOLATION:** Any component using Motion, scroll listeners, or pointer physics MUST be an isolated leaf with `'use client'` at the top. Server Components render static layouts only.
* **Styling:** **Tailwind v4** (default). Tailwind v3 only if the existing project demands it.
  * For v4: do NOT use `tailwindcss` plugin in `postcss.config.js`. Use `@tailwindcss/postcss` or the Vite plugin.
* **Animation:** **Motion** (the library formerly known as Framer Motion). Import from `motion/react` (`import { motion } from "motion/react"`). The `framer-motion` package still works as a legacy alias - prefer `motion/react` in new code.
* **Fonts:** Always use `next/font` (Next.js) or self-host with `@font-face` + `font-display: swap`. Never link Google Fonts via `<link>` in production.

### 3.B State
* Local `useState` / `useReducer` for isolated UI.
* Global state ONLY for deep prop-drilling avoidance - Zustand, Jotai, or React context.
* **NEVER** use `useState` to track continuous values driven by user input (mouse position, scroll progress, pointer physics, magnetic hover). Use Motion's `useMotionValue` / `useTransform` / `useScroll`. `useState` re-renders the React tree on every change and collapses on mobile.

### 3.C Icons
* **Allowed libraries (priority order):** `@phosphor-icons/react`, `hugeicons-react`, `@radix-ui/react-icons`, `@tabler/icons-react`.
* **Discouraged:** `lucide-react`. Acceptable only when the user explicitly asks for it or the project already depends on it. shadcn/ui projects keep `lucide-react` as the single icon family.
* **NEVER hand-roll SVG icons.** If a glyph is missing, install a second library or compose from primitives - do not draw icon paths from scratch.
* **One family per project.** Do not mix Phosphor with Lucide in the same component tree.
* **Standardize `strokeWidth` globally** (e.g. `1.5` or `2.0`).

### 3.D Emoji Policy
Discouraged by default in code, markup, and visible text. Replace symbols with icon-library glyphs. **Override:** allow emojis only when the user explicitly asks for a playful / chat-style / social-native vibe - and even then use them sparingly with intent.

### 3.E Responsiveness & Layout Mechanics
* Standardize breakpoints (`sm 640`, `md 768`, `lg 1024`, `xl 1280`, `2xl 1536`).
* Contain page layouts using `max-w-[1400px] mx-auto` or `max-w-7xl`.
* **Viewport Stability:** NEVER use `h-screen` for full-height Hero sections. ALWAYS use `min-h-[100dvh]` to prevent layout jumping on mobile (iOS Safari address bar).
* **Grid over Flex-Math:** NEVER use complex flexbox percentage math (`w-[calc(33%-1rem)]`). ALWAYS use CSS Grid (`grid grid-cols-1 md:grid-cols-3 gap-6`).

### 3.F Dependency Verification (mandatory)
Before importing ANY 3rd-party library, check `package.json`. If the package is missing, output the install command first. **Never** assume a library exists.

---

## 5. SCROLL CHOREOGRAPHY (Canonical Skeletons)

### 5.A Sticky-Stack - Canonical Skeleton

```tsx
"use client";
import { useRef, useEffect } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useReducedMotion } from "motion/react";

gsap.registerPlugin(ScrollTrigger);

export function StickyStack({ cards }: { cards: React.ReactNode[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();

  useEffect(() => {
    if (reduce || !ref.current) return;
    const ctx = gsap.context(() => {
      const cardEls = gsap.utils.toArray<HTMLElement>(".stack-card");
      cardEls.forEach((card, i) => {
        if (i === cardEls.length - 1) return;
        gsap.to(card, {
          scale: 0.92,
          opacity: 0.55,
          ease: "none",
          scrollTrigger: {
            trigger: cardEls[i + 1],
            start: "top bottom",
            end: "top top",
            scrub: true,
          },
        });
      });
    }, ref);
    return () => ctx.revert();
  }, [reduce]);

  return (
    <div ref={ref} className="relative">
      {cards.map((card, i) => (
        <div
          key={i}
          className="stack-card sticky top-0 min-h-[100dvh] flex items-center justify-center"
        >
          {card}
        </div>
      ))}
    </div>
  );
}
```

Critical points: CSS `sticky top-0` handles the pin (no GSAP `pin`, no standalone `ScrollTrigger.create`), and the scale/opacity transform on each card is driven by the NEXT card's scroll trigger (`start: "top bottom"`, `end: "top top"`, `scrub: true`), so the previous card shrinks as the next one arrives.

### 5.B Horizontal-Pan - Canonical Skeleton

```tsx
"use client";
import { useRef, useEffect } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useReducedMotion } from "motion/react";

gsap.registerPlugin(ScrollTrigger);

export function HorizontalPan({ children }: { children: React.ReactNode }) {
  const wrap = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();

  useEffect(() => {
    if (reduce || !wrap.current || !track.current) return;
    const ctx = gsap.context(() => {
      const distance = track.current!.scrollWidth - window.innerWidth;
      gsap.to(track.current, {
        x: -distance,
        ease: "none",
        scrollTrigger: {
          trigger: wrap.current,
          start: "top top",                              // pin starts when section top hits viewport top
          end: () => `+=${distance}`,                    // scroll distance = track width minus viewport
          pin: true,
          scrub: 1,
          invalidateOnRefresh: true,
        },
      });
    }, wrap);
    return () => ctx.revert();
  }, [reduce]);

  return (
    <section ref={wrap} className="relative overflow-hidden">
      <div ref={track} className="flex h-[100dvh] items-center">
        {children}
      </div>
    </section>
  );
}
```

Critical points: `start: "top top"`, `pin: true`, `end: "+=${distance}"` (scroll length = horizontal travel needed), `scrub: 1`. The wrapper is pinned, the inner track slides horizontally as the user scrolls vertically.

### 5.C Scroll-Reveal Stagger - Canonical Skeleton (lighter alternative)

For simple "items appear as they enter viewport" (no pinning), prefer Motion's `whileInView` over GSAP - lighter, no ScrollTrigger needed:

```tsx
"use client";
import { motion, useReducedMotion } from "motion/react";

export function RevealStagger({ items }: { items: string[] }) {
  const reduce = useReducedMotion();
  return (
    <ul className="grid gap-6">
      {items.map((item, i) => (
        <motion.li
          key={item}
          initial={reduce ? false : { opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{
            duration: 0.6,
            delay: i * 0.06,
            ease: [0.16, 1, 0.3, 1],
          }}
        >
          {item}
        </motion.li>
      ))}
    </ul>
  );
}
```

Use this for: feature lists, testimonial grids, logo walls, anything that just needs "enter on scroll." Save GSAP for actual pin/scrub work.

### 5.D Forbidden Animation Patterns

* **`window.addEventListener("scroll", ...)`** is banned. It runs on every scroll frame, jank-prone, no batching. Use Motion's `useScroll()`, GSAP's `ScrollTrigger`, IntersectionObserver, or CSS `scroll-driven animations` (`animation-timeline: view()`).
* **Custom scroll progress calculations using `window.scrollY`** in React state. Same reason. Re-renders on every frame.
* **`requestAnimationFrame` loops that touch React state.** Use motion values (`useMotionValue` + `useTransform`) instead.
* **Layout Transitions:** Use Motion's `layout` and `layoutId` props for visible state changes (re-ordering lists, expanding modals, shared elements between routes). Do not wrap static content in `layout` props "for safety" - it costs measurement work.
* **Staggered Orchestration:** Use `staggerChildren` (Motion) or CSS cascade (`animation-delay: calc(var(--index) * 100ms)`) for reveal moments where sequence matters. For `staggerChildren`, parent (`variants`) and children MUST share the same Client Component tree.

---

## 7. DIAL DEFINITIONS (Technical Reference)

### DESIGN_VARIANCE (Level 1-10)
* **1-3 (Predictable):** Symmetrical CSS Grid (12-col, equal fr-units), equal paddings, centered alignment.
* **4-7 (Offset):** `margin-top: -2rem` overlaps, varied image aspect ratios (4:3 next to 16:9), left-aligned headers over center-aligned data.
* **8-10 (Asymmetric):** Masonry layouts, CSS Grid with fractional units (`grid-template-columns: 2fr 1fr 1fr`), massive empty zones (`padding-left: 20vw`).
* **MOBILE OVERRIDE:** For levels 4-10, asymmetric layouts above `md:` MUST collapse to strict single-column (`w-full`, `px-4`, `py-8`) on viewports `< 768px`.

### MOTION_INTENSITY (Level 1-10)
* **1-3 (Static):** No automatic animations. CSS `:hover` and `:active` states only. `prefers-reduced-motion` is the default mode anyway.
* **4-7 (Fluid CSS):** `transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.3s cubic-bezier(0.16, 1, 0.3, 1)`. `animation-delay` cascades for load-ins. Focus on `transform` and `opacity`.
* **8-10 (Advanced Choreography):** Complex scroll-triggered reveals, parallax, scroll-driven animation (CSS `animation-timeline` or GSAP ScrollTrigger). Use Motion hooks. **NEVER use `window.addEventListener('scroll')`** - it is a hard ban, not a "prefer-not." See Section 5.D for the allowed alternatives.

### VISUAL_DENSITY (Level 1-10)
* **1-3 (Art Gallery):** Lots of white space. Huge section gaps (`py-32` to `py-48`). Expensive, clean.
* **4-7 (Daily App):** Standard web app spacing (`py-16` to `py-24`).
* **8-10 (Cockpit):** Tight paddings. No card boxes; 1px lines separate data. Mandatory: `font-mono` for all numbers.

---

## 11. REDESIGN PROTOCOL

This skill handles **greenfield builds AND redesigns**. Misclassifying the mode is the single biggest source of bad redesign output.

### 11.A Detect the Mode (first action)
* **Greenfield** - no existing site, or full overhaul approved. Dial baseline from Section 1.
* **Redesign - Preserve** - modernise without breaking the brand. Audit first, extract brand tokens, evolve gradually.
* **Redesign - Overhaul** - new visual language on top of existing content. Treat as greenfield for visuals; preserve content and IA.

If ambiguous, ask **once**: *"Should this redesign preserve the existing brand, or are we starting visually from scratch?"*

### 11.B Audit Before Touching
Document the current state before proposing changes:
* **Brand tokens** - primary / accent colors, type stack, logo treatment, radii.
* **Information architecture** - page tree, primary nav, key conversion paths.
* **Content blocks** - what exists, what's doing work, what's filler.
* **Patterns to preserve** - signature interactions, recognisable hero, copy voice.
* **Patterns to retire** - AI-slop tells, broken layouts, dead links, generic stock imagery, perf traps.
* **Dial reading of the existing site** - infer current `DESIGN_VARIANCE` / `MOTION_INTENSITY` / `VISUAL_DENSITY`. That's your starting point, not the baseline.
* **SEO baseline** - current ranking pages, meta titles, structured data, OG cards. **SEO migration is the #1 redesign risk.**

### 11.C Preservation Rules
* **Do not change information architecture** unless asked. Keep page slugs, anchor IDs, primary nav labels stable for SEO and muscle memory.
* **Extract brand colors before applying Section 4.2.** A brand that is already purple stays purple - apply the LILA RULE's override.
* **Preserve copy voice** unless asked for a rewrite. Visual modernisation ≠ content rewrite.
* **Honor existing accessibility wins.** Do not regress focus states, alt text, keyboard nav, contrast.
* **Respect existing analytics events.** Do not rename buttons, form fields, section IDs that downstream tracking depends on.

### 11.D Modernisation Levers (priority order)
Apply in order - stop when the brief is satisfied:
1. **Typography refresh** - biggest visual lift per unit of risk.
2. **Spacing & rhythm** - increase section padding, fix vertical rhythm.
3. **Color recalibration** - desaturate, unify neutrals, keep brand accent.
4. **Motion layer** - add `MOTION_INTENSITY`-appropriate micro-interactions to existing components.
5. **Hero & key-section recomposition** - restructure top-of-funnel using `reference/pre-flight.md`'s Addendum: Persuade hero boxes.
6. **Full block replacement** - only when the existing block is unsalvageable.

### 11.E Decision Tree: Targeted Evolution vs Full Redesign
* IA, content, and SEO sound → **targeted evolution** (Levers 1-4). ~70% of value at ~40% of risk.
* Visual debt is structural (broken IA, no design system, broken mobile) → **full redesign** with strict content preservation.
* Brand itself is changing → **greenfield**.

### 11.F What Never Changes Silently
Never modify without explicit user approval:
* URL structure / route slugs.
* Primary nav labels.
* Form field names or order (breaks analytics + autofill).
* Brand logo or wordmark.
* Existing legal / consent / cookie copy.

---

## Install Commands per Design System

```bash
# Material Web (Material 3)
npm install @material/web

# Fluent UI React (v9)
npm install @fluentui/react-components

# Fluent UI Web Components (framework-free)
npm install @fluentui/web-components @fluentui/tokens

# IBM Carbon
npm install @carbon/react @carbon/styles

# Radix Themes
npm install @radix-ui/themes

# shadcn/ui (open code, owned components)
npx shadcn@latest init
npx shadcn@latest add button card badge separator input

# Primer CSS (GitHub product/devtool UI)
npm install --save @primer/css

# Primer Brand (GitHub marketing UI)
npm install @primer/react-brand

# GOV.UK Frontend
npm install govuk-frontend

# USWDS (US Web Design System)
npm install @uswds/uswds

# Atlassian Design System (Atlaskit)
yarn add @atlaskit/css-reset @atlaskit/tokens @atlaskit/button @atlaskit/badge @atlaskit/section-message @atlaskit/card

# Bootstrap 5.3
npm install bootstrap

# Shopify Polaris Web Components (Shopify apps only)
# Add this to your app HTML head:
#   <meta name="shopify-api-key" content="%SHOPIFY_API_KEY%" />
#   <script src="https://cdn.shopify.com/shopifycloud/polaris.js"></script>
```
