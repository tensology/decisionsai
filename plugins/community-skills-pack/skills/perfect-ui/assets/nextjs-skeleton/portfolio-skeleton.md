# Next.js Portfolio Skeleton (Reference Template)

Skeleton file tree and starter content for a Next.js 14+ App Router portfolio. Use as a reference during Phase 7 implementation when `--type=portfolio`. Adjust paths to match the project.

## File Tree

```
portfolio/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                     # Composes all main sections
│   ├── globals.css
│   ├── opengraph-image.tsx          # OG image generator
│   ├── work/
│   │   └── [slug]/
│   │       ├── page.tsx             # Per-project case study page
│   │       └── opengraph-image.tsx  # Per-project OG
│   ├── components/
│   │   ├── icons/                   # Custom SVG icons
│   │   │   ├── icon.tsx
│   │   │   ├── nav-logo-mark.tsx    # Often a monogram
│   │   │   ├── project-link-arrow.tsx
│   │   │   ├── tag-glyph-brand.tsx
│   │   │   ├── tag-glyph-web.tsx
│   │   │   ├── tag-glyph-product.tsx
│   │   │   ├── contact-mark.tsx
│   │   │   ├── availability-indicator.tsx
│   │   │   ├── social-twitter.tsx
│   │   │   ├── social-instagram.tsx
│   │   │   └── index.ts
│   │   ├── three/                   # 3D (if applicable — usually accent only for portfolio)
│   │   │   └── monogram-3d.tsx
│   │   ├── sections/
│   │   │   ├── hero-intro.tsx
│   │   │   ├── work-grid.tsx
│   │   │   ├── featured-case-study.tsx
│   │   │   ├── about-bio.tsx
│   │   │   ├── process.tsx          # optional
│   │   │   ├── press.tsx            # optional
│   │   │   ├── contact-cta.tsx
│   │   │   └── footer.tsx
│   │   ├── work/
│   │   │   ├── project-card.tsx     # used in work-grid
│   │   │   └── project-page.tsx     # case study layout used by [slug]/page.tsx
│   │   └── ui/                      # shadcn primitives + customizations
│   └── lib/
│       ├── content.ts               # Bio, section copy
│       └── projects.ts              # Project metadata array
├── public/
│   ├── portfolio/
│   │   ├── og.webp                  # Default OG
│   │   ├── owner-portrait.webp      # if used
│   │   ├── texture-grain.png
│   │   └── projects/
│   │       ├── {project-slug}/
│   │       │   ├── cover.webp       # Used in work-grid + case-study hero
│   │       │   ├── 01.webp          # Case study images
│   │       │   ├── 02.webp
│   │       │   └── ...
│   └── fonts/
├── tailwind.config.ts
├── next.config.mjs
└── package.json
```

## tailwind.config.ts

Same pattern as landing — pull tokens from `visual-direction.md`. Portfolio palettes lean editorial / luxury / hand-crafted more often than landing.

```ts
import type { Config } from 'tailwindcss';

export default {
  content: ['./app/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
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
        prose: '65ch',          // case study reading width
      },
    },
  },
} satisfies Config;
```

## app/lib/projects.ts (centralized project metadata)

Source of truth for all project data. Used by work-grid and case study pages.

```ts
export type Project = {
  slug: string;
  title: string;
  client: string;
  year: number;
  role: string;
  tags: ('Brand' | 'Web' | 'Product' | 'Identity' | 'Editorial' | 'Type')[];
  cover: string;          // /portfolio/projects/{slug}/cover.webp
  featured: boolean;       // shown larger in bento, OR in featured-case-study section
  href: string | null;     // null = case study page exists; string = external link
  context?: string;        // 1-line context for tile
  caseStudy?: {
    summary: string;       // 2-3 sentence problem + your role
    images: { src: string; alt: string; caption?: string }[];
    outcome?: string;      // optional shareable outcome
    liveUrl?: string;
  };
};

export const projects: Project[] = [
  {
    slug: 'oscar-onboarding',
    title: 'Healthcare onboarding flow',
    client: 'Oscar Health',
    year: 2025,
    role: 'Lead product designer',
    tags: ['Product'],
    cover: '/portfolio/projects/oscar-onboarding/cover.webp',
    featured: true,
    href: null,
    context: 'Reduced first-week dropoff from 38% to 14%',
    caseStudy: {
      summary: 'Members were churning before completing benefits setup. I led research, IA, and visual design across 4 platforms over 14 weeks.',
      images: [
        { src: '/portfolio/projects/oscar-onboarding/01.webp', alt: 'Initial flow audit findings' },
        { src: '/portfolio/projects/oscar-onboarding/02.webp', alt: 'Final IA structure' },
        // ...
      ],
      outcome: 'First-week setup completion rose to 86%, sustained over 6 months.',
    },
  },
  // ... more projects
];

export const featuredProjects = projects.filter((p) => p.featured);
```

## app/lib/content.ts (centralized copy)

```ts
export const content = {
  hero: {
    name: 'Sarah Chen',
    craft: 'Brand identity for early-stage tech.',
    location: 'Brooklyn, NY',
    availability: 'Available for new partnerships from June 2026',
  },
  about: {
    bio: [
      "I've spent the last 4 years on healthcare product design — currently at Oscar, previously at One Medical.",
      "I work on the intersection of clinical workflows and consumer-grade UX.",
    ],
    clientList: ['Oscar Health', 'One Medical', 'Forward', 'Hims', 'Calm'],
  },
  process: [
    {
      title: 'Listen and observe',
      body: 'I start by sitting with users in real workflows — not on a Zoom interview. The brief always changes after week one.',
    },
    // ...
  ],
  contact: {
    email: 'sarah@chen.studio',
    invitation: 'For project inquiries, write me at',
  },
};
```

## Section Skeleton: Hero (Editorial Asymmetric)

```tsx
// app/components/sections/hero-intro.tsx
import { content } from '@/lib/content';
import { AvailabilityIndicator } from '@/components/icons';

export const HeroIntro = () => (
  <section className="grain min-h-[60dvh] relative overflow-hidden">
    <div className="max-w-container mx-auto px-8 pt-24 pb-12">
      <div className="grid grid-cols-12 gap-8 items-end">
        <div className="col-span-12 md:col-span-8">
          <h1 className="font-display text-7xl md:text-9xl leading-[0.9] tracking-tight">
            {content.hero.name}
          </h1>
          <p className="mt-6 text-2xl md:text-3xl font-display text-ink-muted">
            {content.hero.craft}
          </p>
        </div>
        <div className="col-span-12 md:col-span-4 md:text-right">
          <p className="text-sm text-ink-muted">{content.hero.location}</p>
          <p className="mt-3 inline-flex items-center gap-2 text-sm">
            <AvailabilityIndicator size={12} className="text-accent" />
            {content.hero.availability}
          </p>
        </div>
      </div>
    </div>
  </section>
);
```

## Section Skeleton: Work Grid (Bento)

```tsx
// app/components/sections/work-grid.tsx
import { projects } from '@/lib/projects';
import { ProjectCard } from '@/components/work/project-card';

export const WorkGrid = () => (
  <section className="py-24 md:py-32">
    <div className="max-w-container mx-auto px-8">
      <h2 className="font-display text-4xl mb-12">Selected work</h2>
      <div className="grid grid-cols-12 gap-6 md:gap-8 auto-rows-[minmax(200px,_auto)]">
        {projects.map((p, i) => (
          <ProjectCard
            key={p.slug}
            project={p}
            // bento sizing — featured items span more
            className={
              p.featured
                ? 'col-span-12 md:col-span-8 md:row-span-2'
                : 'col-span-12 md:col-span-4'
            }
          />
        ))}
      </div>
    </div>
  </section>
);
```

## Component: Project Card

```tsx
// app/components/work/project-card.tsx
import Link from 'next/link';
import Image from 'next/image';
import type { Project } from '@/lib/projects';
import { ProjectLinkArrow } from '@/components/icons';

type Props = {
  project: Project;
  className?: string;
};

export const ProjectCard = ({ project, className = '' }: Props) => {
  const href = project.href ?? `/work/${project.slug}`;
  const external = !!project.href;

  return (
    <Link
      href={href}
      target={external ? '_blank' : undefined}
      rel={external ? 'noopener noreferrer' : undefined}
      className={`group relative block overflow-hidden ${className}`}
    >
      <div className="relative h-full w-full">
        <Image
          src={project.cover}
          alt={`${project.title} cover`}
          fill
          className="object-cover"
          sizes="(min-width: 768px) 50vw, 100vw"
        />
        <div className="absolute inset-0 bg-ink/0 group-hover:bg-ink/10 transition-colors duration-300" />
      </div>
      <div className="mt-4 flex items-baseline justify-between gap-4">
        <div>
          <p className="font-display text-xl">{project.title}</p>
          <p className="text-sm text-ink-muted">
            {project.client} · {project.year}
          </p>
        </div>
        <ProjectLinkArrow
          size={16}
          className="text-ink-muted transition-transform duration-300 group-hover:translate-x-1"
        />
      </div>
    </Link>
  );
};
```

## Per-Project Case Study Page

```tsx
// app/work/[slug]/page.tsx
import { notFound } from 'next/navigation';
import Image from 'next/image';
import { projects } from '@/lib/projects';

export async function generateStaticParams() {
  return projects
    .filter((p) => p.caseStudy)
    .map((p) => ({ slug: p.slug }));
}

export default function CaseStudyPage({ params }: { params: { slug: string } }) {
  const project = projects.find((p) => p.slug === params.slug);
  if (!project || !project.caseStudy) notFound();

  return (
    <article className="max-w-container mx-auto px-8 py-24">
      <header className="mb-16">
        <p className="text-sm text-ink-muted mb-4">
          {project.client} · {project.year} · {project.role}
        </p>
        <h1 className="font-display text-6xl md:text-8xl tracking-tight">
          {project.title}
        </h1>
        <p className="mt-8 max-w-prose text-lg">
          {project.caseStudy.summary}
        </p>
      </header>

      <div className="space-y-12">
        {project.caseStudy.images.map((img, i) => (
          <figure key={i}>
            <Image
              src={img.src}
              alt={img.alt}
              width={1600}
              height={1000}
              className="w-full h-auto"
            />
            {img.caption && (
              <figcaption className="mt-3 text-sm text-ink-muted max-w-prose">
                {img.caption}
              </figcaption>
            )}
          </figure>
        ))}
      </div>

      {project.caseStudy.outcome && (
        <p className="mt-16 max-w-prose text-xl">
          {project.caseStudy.outcome}
        </p>
      )}
    </article>
  );
}
```

## Section Skeleton: Contact CTA

```tsx
// app/components/sections/contact-cta.tsx
import { content } from '@/lib/content';
import { ContactMark } from '@/components/icons';

export const ContactCta = () => (
  <section className="py-24 md:py-32 bg-bg-muted">
    <div className="max-w-container mx-auto px-8 text-center">
      <ContactMark size={48} className="mx-auto text-accent" />
      <h2 className="mt-8 font-display text-5xl md:text-7xl">
        {content.hero.availability}
      </h2>
      <p className="mt-6 text-lg">
        {content.contact.invitation}{' '}
        <a
          href={`mailto:${content.contact.email}`}
          className="font-mono underline-offset-4 hover:underline"
        >
          {content.contact.email}
        </a>
      </p>
    </div>
  </section>
);
```

## Mobile Reflow Reminder

- Hero: name + craft stack vertically, availability moves below
- Work grid bento: collapse to 1-col stack at <768px (project tiles span full width)
- Case study images: full bleed, no side padding at <768px
- Contact CTA: large display text wraps to 2-3 lines, email stays on its own line

## Performance Budget

- Total JS gzipped: ≤ 200KB (excluding 3D)
- Total 3D bundle: ≤ 200KB additional (only if portfolio uses interactive-accent 3D)
- Project covers: ≤ 200KB each, served as WebP, `sizes` attribute set correctly
- LCP target: ≤ 2.5s on mobile
- CLS target: ≤ 0.1

## What this Skeleton is NOT

This is a starting structure. Never copy the names, palette, or content here verbatim. ALL of those come from `visual-direction.md`, `lib/content.ts`, and `lib/projects.ts` for the specific portfolio.

## Common Portfolio Pitfalls (refuse during implementation)

- Auto-generated bio from "passionate" template — STOP, write real bio
- Hover-effects stack (zoom + tilt + glow + caption-slide) — keep to 1-2 effects max
- Forced-uniform aspect ratios on all project tiles — let real covers determine ratio
- iPhone-mockup-holding-the-work covers — use real shots / screens
- 30+ projects in grid — cut to 6-12, quality > quantity
- "Tools I use" tag cloud (Figma / Sketch / Photoshop) — remove
- Skill bars / proficiency percentages — remove
- "View all projects" pagination on a portfolio — bad signal; show your best in one view
