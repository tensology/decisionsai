# Landing Page Anatomy

Sections, conversion patterns, and what NOT to include. Use during Phase 6 planning.

## Standard Section Stack

A landing usually contains 6–9 of these. Order matters — each section answers a question raised by the previous one.

| # | Section | Question it answers | Optional? |
|---|---------|---------------------|-----------|
| 1 | Hero | What is this and is it for me? | NO |
| 2 | Social proof (logo bar) | Should I trust this? | recommended |
| 3 | Features / Value props | What does it actually do? | NO |
| 4 | How it works | How will I use it? | recommended |
| 5 | Testimonials | Are people like me succeeding with this? | recommended |
| 6 | Pricing / Plan | Can I afford it? | only if applicable |
| 7 | FAQ | What about my objections? | recommended |
| 8 | Final CTA | OK, what now? | NO |
| 9 | Footer | Where do I go for X? | NO |

Avoid: integrations grid (clutter), "About us" on a landing (move to /about), team photos at top of fold.

## Section-by-Section Patterns

### 1. Hero

**Goal:** In 5 seconds, the visitor knows what + who + the next action.

**Required elements:**
- H1 — clear value statement (NOT clever)
- Subheadline — one sentence elaboration
- Primary CTA — single button (NOT two equal CTAs)
- Visual anchor — hero image, illustration, or 3D scene

**Layout patterns by vibe:**
| Vibe | Hero layout |
|------|------------|
| Minimal | Centered or 70/30 split, large negative space, single visual |
| Editorial | 60/40 split with display H1 left, image right, asymmetric |
| Brutalist | Full-bleed visual with H1 overlaid in raw type, 100dvh |
| Retro-futuristic | Full-canvas 3D background with H1 layer above |
| Luxury | Symmetrical centered with product photo dominant |
| Playful | Off-grid with floating elements, hand-feel |
| Glass-tech | 3D refractive form behind translucent H1 panel |

**Anti-patterns:**
- Two equal CTA buttons ("Get Started" + "Learn More" same size) — visitor freezes
- Centered H1 at high DESIGN_VARIANCE — generic
- "Empower your business with seamless..." — AI cliché
- Carousel hero — split attention, kills conversion

### 2. Social Proof / Logo Bar

**Goal:** Borrow trust quickly.

**Patterns:**
- Single row of 5-7 customer logos, grayscale or single-tint to match palette
- "Trusted by [N] teams at [logos]" — single-line presentation
- Press logos ("As seen in") — lower trust than customer logos but works for early stage

**Anti-patterns:**
- Generic startup logos (Acme, Globex) — lying erodes everything
- Logo grid of 50+ logos — desperate signal
- Animated logo carousel that auto-scrolls — distracting

### 3. Features / Value Props

**Goal:** Articulate distinct benefits, not exhaustive feature list.

**Patterns by spatial language:**
- Asymmetric editorial: 2-col zig-zag (image left → image right → image left)
- Minimal grid: 3-up bento-style cards with custom icon + headline + 1-line desc
- Brutalist density: 4-up tight cards with stark borders
- Atmospheric: Single full-width feature per scroll-stop with 3D / illustration

**Each feature row contains:**
- Custom icon from icon set (NEVER a library icon)
- Short headline (4-7 words)
- 1-2 sentence body
- Optional: small visual / animated demo

**Anti-patterns:**
- 3-column equal cards with generic icon + lorem text — most generic AI layout
- 12 features in a grid — paralyzes, dilutes
- Feature names as buttons — links go nowhere on a landing

### 4. How It Works

**Goal:** Reduce "I don't know how to start" friction.

**Patterns:**
- 3-step horizontal flow with custom icons between (NOT generic arrows)
- Animated diagram showing the user path
- Numbered steps with hand-drawn connectors

**Each step:**
- Step number (typographic, large)
- 3-5 word verb-led title ("Install in 30 seconds")
- 1 sentence

**Anti-patterns:**
- 7+ steps — kills conviction
- Steps all start with "Click" — focus on outcome not action
- Shopify-style timeline graphic — overused

### 5. Testimonials

**Goal:** Mirror the visitor's hesitation back as solved.

**Patterns by spatial language:**
- Asymmetric: single rotating quote, large pull-quote treatment
- Minimal grid: 3-up testimonial cards
- Atmospheric: full-width quote with avatar floating, blurred bg
- Editorial: magazine-style spread with quote + portrait + context

**Each testimonial contains:**
- Quote (specific, mentions actual outcome — NOT "love it!")
- Real-feeling name + role + company
- Avatar (custom-generated per `visual-asset-prompt-library.md`)
- Context line ("After 3 months of use")

**Anti-patterns:**
- "John Doe — CEO @ Acme" — fake names tank trust
- "Game-changer!" — vacuous testimonial
- Star ratings without source — meaningless

### 6. Pricing (only if applicable)

**Goal:** Remove cost objection.

**Patterns:**
- 2-3 tier comparison table
- Annual/monthly toggle prominent
- One tier visually emphasized (the recommended one)
- Feature checklist per tier with custom check icon

**Anti-patterns:**
- 4+ tiers — analysis paralysis
- "$99.99" — round prices read as fake
- "Contact us for pricing" without enterprise context — feels evasive
- Crossed-out "old" prices everywhere — feels QVC

### 7. FAQ

**Goal:** Answer the 5 questions blocking conversion.

**Patterns:**
- Accordion (single-open) of 5-7 Q&As
- 2-col grid of static Q&A cards (no accordion) for minimal vibe

**Each FAQ:**
- Real question someone asked (not marketing copy as question)
- Direct answer in 2-3 sentences
- Link to docs if needed

**Anti-patterns:**
- 20+ FAQs — should be in /docs not landing
- Marketing-speak questions ("How can I unleash my potential?")
- Accordion with all open by default — clutters

### 8. Final CTA

**Goal:** Re-pitch with conviction.

**Patterns:**
- Section with bold restatement of value + button
- Often mirrors hero's CTA
- Visual element matches hero composition

**Anti-patterns:**
- Different CTA from hero — confuses
- Newsletter signup as "final CTA" — usually too low-commitment for the spot
- Email-only signup with no preview of what they're getting

### 9. Footer

**Goal:** Functional navigation, brand reinforcement.

**Required:**
- Brand mark + tagline
- Nav links (product, company, resources, legal)
- Social links (using custom social icons from icon set, NOT library)
- Copyright + minimal legal

**Anti-patterns:**
- 6-column footer with 50 links — overwhelms
- Newsletter form as the visual centerpiece — distracts from primary CTA
- Generic social icon set from a library — breaks cohesion

## Conversion Heuristics

### The 5-Second Test
Show the hero for 5 seconds, then ask:
1. What does this product do?
2. Who is it for?
3. What should I do next?

If any answer is unclear, hero fails. Iterate.

### CTA Density
- Hero: 1 primary CTA
- Mid-page: 1 secondary CTA after features OR after how-it-works (not both)
- Final section: 1 primary CTA (matches hero)

Total: max 3 CTAs on page. Each links to the SAME destination.

### Above-the-Fold Discipline
On 1366×768 desktop and iPhone 14 (390×844):
- H1 must be visible
- Subheadline must be visible
- Primary CTA must be visible
- Hero visual element must be at least 50% visible

If any is hidden, restructure hero.

## Page Structure (Next.js App Router)

```
app/
├── page.tsx                       # Composes all sections
├── layout.tsx                     # Fonts, metadata
├── globals.css                    # Tailwind + theme tokens
├── components/
│   ├── icons/                     # Custom SVG icon set
│   ├── three/                     # 3D components (if any)
│   ├── sections/
│   │   ├── hero.tsx
│   │   ├── social-proof.tsx
│   │   ├── features.tsx
│   │   ├── how-it-works.tsx
│   │   ├── testimonials.tsx
│   │   ├── pricing.tsx
│   │   ├── faq.tsx
│   │   ├── final-cta.tsx
│   │   └── footer.tsx
│   └── ui/                        # shadcn primitives + custom
└── lib/
    └── content.ts                 # All copy in one file (easy iteration)
```

`page.tsx`:
```tsx
import { Hero } from '@/components/sections/hero';
import { SocialProof } from '@/components/sections/social-proof';
// ... etc

export default function Page() {
  return (
    <>
      <Hero />
      <SocialProof />
      <Features />
      <HowItWorks />
      <Testimonials />
      <Pricing />
      <Faq />
      <FinalCta />
      <Footer />
    </>
  );
}
```

## Section Removal Rules

If unsure whether to include a section, default to removing. A landing with 6 strong sections beats one with 9 mediocre sections.

Always remove:
- Integrations / partners grid (move to /integrations)
- Team photos (move to /about)
- Blog feed (move to /blog)
- "Our values" (no place on landing)

## Mobile Discipline

- Every section must work in 390px wide viewport
- 3-col grids → 1-col stacked
- Asymmetric heroes → reflow to stacked image-above-text or text-above-image
- 3D scenes → degrade to poster image at <768px (use `useMediaQuery`)
- Testimonial carousels → vertical stacked or single rotating

## Strategic Omissions — what AI typically forgets

Audit before ship. Each item tagged with applicability condition.

- Privacy policy + terms-of-service links in footer `[always]`
- Custom 404 page `[when multi-page site]`
- Form validation (client-side, inline) `[when form present]`
- "Skip to main content" a11y link (visually-hidden, focus-visible) `[always]`
- Cookie consent banner `[EU/UK/EEA jurisdiction]`
- "Back" navigation in any flow >1 step `[when multi-step flow]`
- Page metadata (`<title>`, description, OG image, social cards) `[always]`
- Sitemap link or visible site index `[when multi-page site]`
- Working unsubscribe link in email-capture flows `[when email capture]`
- Honest copy — no fabricated metrics; see `anti-slop-rules.md § Honest Copy Mandate` `[always]`

If item applies (per its tag condition) but is missing from output, flag in Phase 8 audit as Strategic Omission (Tier 2 severity).

### Tag vocabulary

- `[always]` — applies to every landing regardless of context
- `[EU/UK/EEA jurisdiction]` — applies when site serves users in those jurisdictions
- `[when form present]` — applies when page contains any form (signup, contact, etc.)
- `[when multi-page site]` — applies when site has >1 indexed page
- `[when multi-step flow]` — applies for funnels, signup wizards, multi-step CTAs
- `[when email capture]` — applies when page captures email addresses
