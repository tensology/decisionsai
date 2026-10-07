# Generic Page Anatomy

For any page type that is NOT `landing` or `portfolio`. Examples: `blog`, `about`, `pricing`, `contact`, `coming-soon`, `error-page` (404/500), `legal`, `dashboard`, `admin`, `e-commerce`, or anything custom.

Universal toolkit (vibe + palette + typography + custom icons + 2D illustrations + visual effects + motion) still applies in full. What differs from landing/portfolio: **no prescribed section stack**. Sections are driven by page-purpose, not template.

## Scope and Honest Caveat

- Use this anatomy when `--type` is anything other than `landing` / `portfolio`
- Evidence base for the skill (12 marketing landings analyzed — see `plans/260509-ai-vs-human-analysis/synthesis.md`) covers marketing-style sites. For dashboard / admin / e-commerce / app-surface types, output is **best-effort universal craft** — vibe lock, custom icons, motion rules, universal anti-slop subset all apply, but section-level conversion patterns won't transfer
- No refusals. If user asks, skill builds it

## Page-Purpose Exercise (Phase 1 amendment)

Before vibe-lock and before any code, answer these for the page being built:

1. **Job:** what's this page's ONE primary job? Pick one:
   - Inform (blog post, about, FAQ, legal)
   - Convert (pricing, signup, contact, coming-soon)
   - Navigate (index, hub, table-of-contents)
   - Display data (dashboard, admin, analytics)
   - Collect input (form, survey, settings)
   - Tell a story (case-study standalone, manifesto)
2. **Audience:** who lands here and from where? (organic search? in-app nav? marketing referral?)
3. **Primary action:** what should they do? Is there even one? Some pages (404, legal) have no action
4. **Success:** what does success look like? (metric or qualitative)
5. **Marketing intent:** does this page have marketing intent? (true → most marketing anti-patterns apply; false → only universal subset applies)

Hand the answers into the Phase 1 brief alongside the standard vibe / inspirations questions.

## Section Pattern Library

Do **not** prescribe a stack. Pick from this library based on page-purpose. Reorder freely.

| Section | Use when | Skip when |
|---------|----------|-----------|
| Nav / header | site has multiple pages | true single-page (coming-soon, error) |
| Hero | page needs a strong opener | 404 / 500 / legal (use a quieter banner instead) |
| Body / content block | always — this is the substance | never |
| CTA block | page has conversion intent | informational-only |
| FAQ | reduces objections | not applicable |
| Footer | marketing-style site, multi-page | app surface (use minimal in-app chrome instead) |
| Sidebar | dashboard, doc-style page | landing-style page |
| Toolbar / filter bar | data-display page | content page |
| Empty-state | dashboard / admin / app surface | static content page |

Body / content block sub-patterns: long-form prose (blog, legal) · data table (dashboard) · form (contact, settings) · feature list (pricing) · image gallery (case study) · timeline (about) · map (contact).

## Anti-Patterns (link to applicability matrix)

The full anti-slop tier system lives in [`anti-slop-rules.md`](anti-slop-rules.md). The **§ Applicability Matrix** at the top of that file tags each rule as `[universal]`, `[marketing-only]`, or `[landing/portfolio-only]`.

For generic-tier pages:

- **Always apply `[universal]` rules** — no emoji, no icon libraries, no Inter/Roboto alone, no DM Sans + Space Grotesk, no `h-screen`, custom SVG cohesion, respect `prefers-reduced-motion`, no generic `ease-in-out 0.3s` defaults, no AI cute decoration illustrations
- **Conditionally apply `[marketing-only]` rules** — only when answer to Question 5 (marketing intent) above was true. Example: a pricing page has marketing intent → "no two equal-weight CTAs in hero" applies. A 404 page has no marketing intent → that rule is irrelevant
- **Skip `[landing/portfolio-only]` rules** — these are anti-clichés specific to portfolio openers and landing hero patterns; they don't apply

## Mobile + Accessibility

- Baseline viewport: 390 × 844 (iPhone 14/15)
- Tap target ≥ 44 × 44 px
- Color contrast WCAG AA for body text (4.5:1), AAA for small text where possible
- Motion auto-degrades intensity − 1 step at < 768 px viewport
- `prefers-reduced-motion` → force intensity 0/3 regardless of locked Phase 2e value
- Keyboard navigable; visible focus rings; skip-to-content link when nav present

## Validation Pre-Ship

- Commitment audit (vibe ≥ 48/60 — see [`visual-direction-guide.md`](visual-direction-guide.md) § Commitment Audit) — still required
- Phase 8 audit runs filtered subset per § Applicability Matrix (see [`anti-slop-rules.md`](anti-slop-rules.md))
- Lighthouse mobile performance ≥ 90 (relax to ≥ 80 if page is data-heavy app surface)
- LCP ≤ 2.5s, CLS ≤ 0.1
- Bundle: JS ≤ 200KB gzipped (excluding intentional effect / 3D layer)

## What This Anatomy Does NOT Do

- Prescribe section order
- Prescribe hero presence (some pages have none)
- Prescribe CTA placement
- Prescribe page chrome density

All four are driven by the Page-Purpose Exercise above. Resist the temptation to template — that's how slop happens.

## Strategic Omissions — what AI typically forgets

Audit before ship. Items conditional on Page-Purpose Exercise answer.

### Universal (any page type)

- Privacy policy + terms-of-service links in footer `[always]` (skip on legal pages — they ARE the policy)
- Page metadata (`<title>`, description, OG image, social cards) `[always]`
- "Skip to main content" a11y link `[always]`
- Honest copy — no fabricated metrics / testimonials; see [`anti-slop-rules.md § Honest Copy Mandate`](anti-slop-rules.md) `[always]`

### Conditional on page-purpose

- **Purpose = convert / collect:** Form validation `[when form present]` · Cookie consent `[EU jurisdiction + data persistence]` · Working unsubscribe `[when email capture]`
- **Purpose = display data (dashboard, admin):** Empty / loading / error states `[always for data UI]` · Keyboard nav for grids / tables `[always]`
- **Purpose = navigate (index, hub):** Sitemap visible OR clean hierarchical IA `[when multi-page]`
- **Purpose = tell a story (case-study standalone, manifesto):** Reading progress indicator OR clear section navigation `[when long-form]`
- **Purpose = inform (blog, about, legal):** Last-updated date `[always for time-sensitive content]` · Author + bio `[when blog]`

Tag vocabulary — same as [`landing-anatomy.md § Strategic Omissions`](landing-anatomy.md) (single definition shared across anatomy files).

## Cross-References

- [`workflow-phases.md`](workflow-phases.md) § Phase 0.5 (type detection routes here)
- [`workflow-phases.md`](workflow-phases.md) § Phase 6 (generic-tier plan prompt)
- [`visual-direction-guide.md`](visual-direction-guide.md) (vibe lock, commitment audit)
- [`anti-slop-rules.md`](anti-slop-rules.md) § Applicability Matrix
- [`custom-icon-pipeline.md`](custom-icon-pipeline.md)
- [`motion-patterns.md`](motion-patterns.md)
- [`../assets/nextjs-skeleton/generic-page-skeleton.md`](../assets/nextjs-skeleton/generic-page-skeleton.md)
