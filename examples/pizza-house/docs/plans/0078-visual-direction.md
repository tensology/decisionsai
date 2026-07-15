# plan: Define visual direction and acceptance criteria (ticket #78)

## Goal

Establish an initial visual baseline for the Ember & Crust Pizza House site so that all feature work in ticket #76–#82 builds against a consistent, warm-restaurant aesthetic. No new components or features are delivered by this plan — only design decisions, tokens, and acceptance criteria are produced here. The plan is explicitly iterative: feedback replaces it on each pass and the visual baseline must remain editable until confirmed.

---

## Constraints (from ticket #74 brief)

1. **Primary audience**: customers ordering pizzas (walkers + diners in). No admin / seller surfaces.
2. **Device first**: mobile-first, responsive breakpoints per `web/tailwind.config.js`.
3. **Performance budget** (per README):
   - LCP ≤ 2s on mid-tier mobile (3G simulation)
   - CLS ≤ 0.1 on desktop+mobile
4. **Accessibility**: WCAG 2.1 AA minimum for text, images, interactive controls and colour contrast (≥ 4.5:1 for body copy; ≥ 3:1 for large UI).

---

## Slice 1 — Color system (shadcn `--color-*` tokens)

| Token | Value | Role |
|-------|-------|------|
| `primary` (brand)   | `#b42025` warm crimson | Header logo mark, sale / hot-seller badges |
| `secondary` (CTA)   | `#f97316` orange — Tailwind amber-500 | Primary CTA (Add to Cart), highlight text on hero |
| `accent`            | `#84cc16` emerald | Success states, free-delivery banner |
| bg / surface        | use shadcn `bg-background`, `bg-card`, `border-border` | Base surfaces for Header/Footer/Navbar/Card components already in place |
| text                | inherit from shadcn `text-muted-foreground`; heading override to `text-foreground` only | Contrast must survive on both white and dark backgrounds |

**Rollback**: Revert to Tailwind default palette if contrast ratios fail — swap `bg-gradient-to-br from-orange-50 via-white to-red-50` (Hero) back out for plain white.
Affected files: `web/src/app/globals.css`, any new CSS file introduced by this plan.

---

## Slice 2 — Typography baseline

| Role | Font     | Size               | Weight             | Tailwind class |
|------|----------|--------------------|--------------------|----------------|
| Header H1 / Hero   | system   | `3xl`              | Bold (700)         | `font-bold tracking-tight text-4xl md:text-5xl` on Heading component |
| Body paragraphs    | **Inter** via Next.js `<Link>` preload of Google Fonts — fallback stack `system-ui, sans-serif`; import URL `display=swap` to avoid CLS; do not bundle locally |
| Nav links          | 14px regular | Tailwind default | `font-small tracking-wide text-muted-foreground hover:text-primary transition-colors duration-200` on Navbar (already present) |

**Rollback**: If Inter preload conflicts with existing font setup — strip `<link>` import from `globals.css`, fall back to `system-ui`.
Affected files:
* [`web/src/app/layout.tsx`](web/src/app/layout.tsx): Google Fonts preconnect / stylesheet link
* [`web/src/components/ui/Heading.tsx`](web/src/components/ui/Heading.tsx): H1 size + weight override

---

## Slice 3 — Layout & spacing conventions

| Rule | Detail |
|------|--------|
| **Grid system** | Mobile: full-width hero / image; Card grid `grid-cols-1 sm:grid-cols-2 lg:grid-cols-4` for Menu and Item components. Sidebar / sticky-panel layout on desktop per existing Navbar pattern. |
| **Spacing scale** | Use Tailwind 8px multiples (0, 1, 2, 3 → px, sm, md, lg in Tailwind defaults). Card padding fixed at `p-4`. Hero section vertical rhythm uses `py-16` for desktop / `py-8` mobile. |
| **Breakpoints** (from tailwind.config.js) | Mobile ≤768px, tablet ≥768px, desktop ≥1024px — use Tailwind prefixed classes (`md:`, `xl:`); no arbitrary values in component code unless absolutely required and flagged in ticket feedback. |
| **Image handling** | Hero `placeholder.png` (existing in `public/images/`) uses `<img>` with natural aspect ratio; on mobile falls back to native 450×280 px placeholder sizing via explicit width/height attributes — prevents CLS shift. No object-fit: scale-down for Hero, only `object-cover` for Card thumbnail images when cropping is required — flag in code comments and note as acceptable risk against CLS target. |

**Rollback**: Remove the `<Image>` component import for Hero; fall back to plain `<img>`.
Affected files:
* [`web/src/components/ui/Footer.tsx`](web/src/components/ui/Footer.tsx): existing image sizing review
* [`web/src/components/Item/Card/index.tsx`](web/src/components/Menu/Card/index.tsx) (Menu version — check filename): apply `object-cover` only for card thumbnails, not hero — mark clearly.

---

## Slice 4 — Visual acceptance criteria (testable by plan completion)

| # | Criterion | How it is measured | Target for first pass |
|---|-----------|--------------------|------------------------|
| AC-1 | **Colour contrast** passes WCAG AA at both normal and large text scales | Automated: `@axe-core/react` run as part of existing test, colour ratios computed by plugin against tokens above | body ≥ 4.5:1 / headings ≥ 3:1 |
| AC-2 | **LCP ≤ 2 s** on Lighthouse `mobile — 3G / mid-tier device (Pixel 5 simulated)` for both desktop view at ≥1024 and mobile view at 375px | Full Lighthouse run (`lighthouse --output=json`) captured once with first plan implementation; target set to ≤2.5s on initial attempt, then tightened to ≤2s as baseline is refined | ≤ 2s (initial benchmark relaxed) |
| AC-3 | **CLS ≤ 0.1** across Hero and main menu grid layouts | Full Lighthouse `core web-vitals` check plus a screenshot diff — CLS score from run below threshold; no image height missing → explicit `width/height` attributes required on all `<img>` tags | ≤ 0.1 (target) |
| AC-4 | **Hero layout parity** between desktop and mobile: Hero uses `placeholder.png`, text overlays legible, CTA visible at both breakpoints | Mobile vs Desktop screenshot check with Lighthouse viewport emulation; confirm `py-8` on mobile / `py-16` on tablet-and-up in Tailwind classes for Hero component | visual diff passes — green baseline |
| AC-5 | **Card grid responsiveness**: Menu renders as single-column stack at 375px, two columns at ≤768, four columns ≥1024; no overflow or horizontal scrollbar | Viewport resize test via Playwright assertions against Card grid class and content overflow checks — existing Lighthouse performance budget already covers it on smaller screens but explicit DOM check required | responsive breakpoints confirmed |
| AC-6 | **Navigation integrity**: Navbar / Footer components still render correctly after any layout change, all links (existing + hero CTA) reachable without hover requirement | Playwright clickability assertion on Navbar link(s), Footer social icons list items — confirm footer has three columns: logo, quick-links (menu → /menu, contact → /contact), accessibility notes; ensure no `:hover` for required interactive states | all visible & accessible below touch target (48px min) for touch targets |
| AC-7 | **No CLS from image loading**: Hero and Item thumbnails have `width/height` defined or `aspectRatio[0] CSS rule set` — Lighthouse Image-size check passes 100% with no CLS contributions flagged | Full Lighthouse run reports zero "Avoid significant layout shifts" warnings for images; screenshot captures confirm no shift between first paint and final state | lighthouse core.web-vitals.layout-shift ≤ 0.05 |

---

## Slice 5 — Test scope (linked to existing test infrastructure)

| File / tool | Purpose |
|------------|---------|
| `web/src/components/ui/Button.test.tsx` | Unit tests against Button token overrides; no new tests added here unless visual change affects behaviour |
| `web/components.json` | Confirm Tailwind config references new font — only edit if not already present in globals.css |
| Existing card/image screenshot harness (if any) | Compare before/after screenshots for Hero and Menu grids with existing test patterns noted on README TODO list: "Take before/after screenshots comparing against the selected visual baseline, including a concise happy-path flow summary." |

---

## Rollback notes

All slices above have an explicit rollback in their respective section. Key rollbacks are grouped here for clarity:

1. **Colour system** — fall back to Tailwind default palette (no brand tokens)
2. **Typography** — remove Google Fonts import, use system-ui stack only
3. **Layout** — drop explicit width/height on `<img>`; fall back to `object-fit: contain` with explicit height for Hero fallback

If any one rollback is triggered because the original goal cannot be met safely (e.g., CLS target impossible without breaking a layout), document why in a follow-up ticket rather than silently lowering the standard.

---

## Files affected by this plan
- `web/src/app/globals.css` — theme tokens, font import (Slice 1 & 2)
- `web/src/components/ui/Footer.tsx` — image sizing review + layout check (Slice 3)
- `web/src/components/ui/Heading.tsx` — H1 size/weight override (Slice 2)
- New: Hero component file if Hero feature module exists but no dedicated UI slot yet; document in PR and link to ticket #78

---

## Iterative improvement rules
- Review **after each slice** with the user / team.
- If a criterion fails on first pass, raise it as a separate refinement ticket — do not silently relax metrics (e.g., never change AC-2 target on this plan; escalate instead).
- This plan is a baseline and expects to be updated as feedback comes in; final state will be signed off through a follow-up ticket or comment block before work proceeds to implementation.
