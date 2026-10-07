# Generic Page Skeleton (Next.js)

For `--type` ≠ `landing` / `portfolio`. Minimal scaffold; user / page-purpose decides sections. Mandates (custom icons, HSL tokens, centralized copy) preserved from landing/portfolio skeletons.

## Scope

- Drop-in for any generic-tier page (blog, about, pricing, contact, coming-soon, error, legal, dashboard, admin, e-commerce, custom)
- Do not use for `landing` (see [`landing-skeleton.md`](landing-skeleton.md)) or `portfolio` (see [`portfolio-skeleton.md`](portfolio-skeleton.md))

## File Tree

```
app/
├── layout.tsx               (fonts via next/font, metadata, html/body, theme provider)
├── page.tsx                 (compose page sections — start empty; build per page-purpose)
├── globals.css              (Tailwind + text-wrap balance + off-black/off-white)
├── components/
│   ├── icons/               (custom SVG only — see ../references/custom-icon-pipeline.md)
│   │   └── index.ts
│   ├── ui/                  (primitives: Button, Card — customized, not default shadcn)
│   └── sections/            (built per page-purpose; do NOT prescribe filenames)
└── lib/
    ├── content.ts           (ALL copy in one place; never inline strings in components)
    └── (type-specific)      (e.g. lib/posts.ts for blog, lib/products.ts for e-commerce)

public/
└── {type}/                  (assets folder named per page-type, e.g. public/blog/)
```

## Tailwind Config Essentials

```ts
// tailwind.config.ts
export default {
  theme: {
    extend: {
      colors: {
        bg: 'hsl(var(--bg) / <alpha-value>)',
        ink: 'hsl(var(--ink) / <alpha-value>)',
        accent: 'hsl(var(--accent) / <alpha-value>)',
        surface: 'hsl(var(--surface) / <alpha-value>)',
      },
      fontFamily: {
        display: ['var(--font-display)'],
        body: ['var(--font-body)'],
      },
      maxWidth: {
        container: '1280px',
      },
    },
  },
}
```

- Never inline hex colors in JSX — always tokens
- Never inline font names — always variable through `next/font`

## globals.css Essentials

```css
:root {
  --bg: 30 30% 96%;       /* off-white cream — set per locked palette */
  --ink: 24 8% 9%;        /* off-black ink */
  --accent: 8 38% 54%;    /* single accent, ≤ 10% surface */
  --surface: 30 18% 92%;
}

h1, h2, h3 { text-wrap: balance; }
p { text-wrap: pretty; }

body {
  background: hsl(var(--bg));
  color: hsl(var(--ink));
  font-family: var(--font-body);
}
```

## Optional Section Imports (uncomment in `page.tsx` as needed)

```tsx
// app/page.tsx — start empty, add sections matching page-purpose
import Nav from '@/components/sections/nav'                  // if multi-page site
import Footer from '@/components/sections/footer'            // if marketing-style
// ... section imports created per page-purpose

export default function Page() {
  return (
    <>
      {/* <Nav /> */}
      <main>
        {/* compose sections here per page-purpose */}
      </main>
      {/* <Footer /> */}
    </>
  )
}
```

## Bundle Targets

- JS ≤ 200KB gzipped (excluding intentional 3D / effect layer)
- LCP ≤ 2.5s
- CLS ≤ 0.1
- Lighthouse mobile performance ≥ 90 — relax to ≥ 80 if page is data-heavy app surface

## Mandates Preserved from Landing/Portfolio Skeletons

- Custom SVG icons only (no library imports)
- HSL token-driven Tailwind (no inline hex)
- Centralized copy in `app/lib/content.ts`
- `next/font/local` or `next/font/google` for the locked display + body pair
- Single accent token, ≤ 10% surface area
- `prefers-reduced-motion` respected on every motion entry

## What This Skeleton Does NOT Prescribe

- Section order
- Hero presence
- CTA layout / count
- Page chrome density
- Sidebar / topbar / split layout

All determined by the Page-Purpose Exercise (see [`../references/generic-page-anatomy.md`](../references/generic-page-anatomy.md) § Page-Purpose Exercise).

## Cross-References

- [`../references/generic-page-anatomy.md`](../references/generic-page-anatomy.md)
- [`../references/custom-icon-pipeline.md`](../references/custom-icon-pipeline.md)
- [`../references/visual-direction-guide.md`](../references/visual-direction-guide.md)
- [`../references/motion-patterns.md`](../references/motion-patterns.md)
- [`landing-skeleton.md`](landing-skeleton.md) (for landing-type sites)
- [`portfolio-skeleton.md`](portfolio-skeleton.md) (for portfolio-type sites)
