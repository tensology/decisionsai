# Next.js Landing Skeleton (Reference Template)

Skeleton file tree and starter content for a Next.js 14+ App Router landing. Use as a reference during Phase 7 implementation. Adjust paths to match the project.

## File Tree

```
landing/
├── app/
│   ├── layout.tsx
│   ├── page.tsx
│   ├── globals.css
│   ├── opengraph-image.tsx          # OG image generator
│   ├── components/
│   │   ├── icons/                   # Custom SVG icons
│   │   │   ├── icon.tsx             # Base wrapper
│   │   │   └── index.ts
│   │   ├── three/                   # 3D components (if applicable)
│   │   │   └── scene.tsx
│   │   ├── sections/
│   │   │   ├── hero.tsx
│   │   │   ├── social-proof.tsx
│   │   │   ├── features.tsx
│   │   │   ├── how-it-works.tsx
│   │   │   ├── testimonials.tsx
│   │   │   ├── pricing.tsx
│   │   │   ├── faq.tsx
│   │   │   ├── final-cta.tsx
│   │   │   └── footer.tsx
│   │   └── ui/                      # shadcn primitives + customizations
│   │       ├── button.tsx
│   │       └── ...
│   └── lib/
│       └── content.ts               # All copy strings in one place
├── public/
│   ├── landing/                     # AI-generated visual assets
│   │   ├── hero.webp
│   │   ├── texture-grain.png
│   │   └── og.webp
│   └── fonts/                       # Self-hosted display fonts
├── tailwind.config.ts
├── next.config.mjs
└── package.json
```

## tailwind.config.ts

```ts
import type { Config } from 'tailwindcss';

export default {
  content: ['./app/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // FROM visual-direction.md — replace with locked palette
        bg: {
          DEFAULT: 'hsl(var(--bg))',
          muted: 'hsl(var(--bg-muted))',
        },
        ink: {
          DEFAULT: 'hsl(var(--ink))',
          muted: 'hsl(var(--ink-muted))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          soft: 'hsl(var(--accent-soft))',
        },
      },
      fontFamily: {
        display: ['var(--font-display)', 'serif'],
        sans: ['var(--font-body)', 'sans-serif'],
      },
      maxWidth: {
        container: '1280px',
      },
    },
  },
} satisfies Config;
```

## app/globals.css

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    /* FROM visual-direction.md — replace with locked palette */
    --bg: 38 30% 94%;
    --bg-muted: 38 25% 88%;
    --ink: 20 10% 9%;
    --ink-muted: 20 5% 35%;
    --accent: 6 35% 54%;
    --accent-soft: 6 30% 85%;
  }

  body {
    @apply bg-bg text-ink font-sans antialiased;
    font-feature-settings: 'ss01', 'ss02';
  }

  h1, h2, h3 {
    @apply font-display;
    text-wrap: balance;
  }

  p {
    text-wrap: pretty;
  }
}

/* Subtle grain overlay (optional) */
.grain {
  position: relative;
}
.grain::after {
  content: '';
  position: absolute;
  inset: 0;
  background-image: url('/landing/texture-grain.png');
  opacity: 0.04;
  mix-blend-mode: multiply;
  pointer-events: none;
}
```

## app/layout.tsx

```tsx
import type { Metadata } from 'next';
import localFont from 'next/font/local';
import './globals.css';

// Replace with locked typography from visual-direction.md
const fontDisplay = localFont({
  src: '../public/fonts/Migra-Italic.woff2',
  variable: '--font-display',
  display: 'swap',
});

const fontBody = localFont({
  src: '../public/fonts/Sohne-Regular.woff2',
  variable: '--font-body',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Product — Tagline',
  description: 'One sentence description.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${fontDisplay.variable} ${fontBody.variable}`}>
      <body>{children}</body>
    </html>
  );
}
```

## app/page.tsx

```tsx
import { Hero } from '@/components/sections/hero';
import { SocialProof } from '@/components/sections/social-proof';
import { Features } from '@/components/sections/features';
import { HowItWorks } from '@/components/sections/how-it-works';
import { Testimonials } from '@/components/sections/testimonials';
import { Faq } from '@/components/sections/faq';
import { FinalCta } from '@/components/sections/final-cta';
import { Footer } from '@/components/sections/footer';

export default function Page() {
  return (
    <main>
      <Hero />
      <SocialProof />
      <Features />
      <HowItWorks />
      <Testimonials />
      <Faq />
      <FinalCta />
      <Footer />
    </main>
  );
}
```

## app/lib/content.ts (centralized copy)

```ts
export const content = {
  hero: {
    h1: 'Specific value statement, plainly written.',
    sub: 'One sentence elaborating who it serves and how.',
    cta: 'Start in 30 seconds',
    ctaHref: '/signup',
  },
  socialProof: {
    label: 'Trusted by teams at',
    logos: ['linear', 'vercel', 'arc'], // not stock startup names
  },
  features: [
    {
      icon: 'feature-speed',
      headline: 'Reach decisions in minutes',
      body: 'Concrete benefit, not abstract.',
    },
    // ...
  ],
  testimonials: [
    {
      quote: 'Specific outcome they achieved, in their voice.',
      name: 'Real-feeling Name',
      role: 'Specific role',
      company: 'Real-feeling company',
      avatar: '/landing/avatar-1.webp',
    },
    // ...
  ],
  // ... etc
};
```

## Section Skeleton: Hero (Asymmetric Editorial)

```tsx
// app/components/sections/hero.tsx
import Image from 'next/image';
import { content } from '@/lib/content';
import { CtaArrow } from '@/components/icons';

export const Hero = () => (
  <section className="grain min-h-[100dvh] relative overflow-hidden">
    <div className="max-w-container mx-auto px-8 pt-32 pb-24">
      <div className="grid grid-cols-12 gap-8 items-end">
        <div className="col-span-12 md:col-span-7">
          <h1 className="font-display text-7xl md:text-9xl leading-[0.9] tracking-tight">
            {content.hero.h1}
          </h1>
          <p className="mt-8 text-lg max-w-prose text-ink-muted">
            {content.hero.sub}
          </p>
          <a
            href={content.hero.ctaHref}
            className="inline-flex items-center gap-3 mt-12 px-6 py-4 bg-ink text-bg rounded-md hover:bg-accent transition-colors"
          >
            {content.hero.cta}
            <CtaArrow size={18} />
          </a>
        </div>
        <div className="col-span-12 md:col-span-5 md:col-start-8">
          <Image
            src="/landing/hero.webp"
            alt=""
            width={800}
            height={1000}
            priority
            className="w-full h-auto"
          />
        </div>
      </div>
    </div>
  </section>
);
```

## Mobile Reflow Reminder

Every section MUST work at 390px viewport:
- Asymmetric grid → stack vertically
- Hero image → above OR below text (no side-by-side under 768px)
- 3D scene → degrade to poster image at <768px
- Pricing 3-up → 1-up stacked

## Performance Budget

- Total JS gzipped: ≤ 200KB (excluding 3D)
- Total 3D bundle: ≤ 200KB additional
- Total images optimized: ≤ 1MB
- LCP target: ≤ 2.5s on mobile
- CLS target: ≤ 0.1

## What this Skeleton is NOT

This is a starting structure. Never copy the H1 / colors / fonts here. ALL of those come from `visual-direction.md` and `lib/content.ts` for the specific project.
