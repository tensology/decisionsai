# Stencil: Funnel (landing → convert) — SYNTHESIZED

## Purpose
Marketing/lead/purchase funnel: hero → value → proof → CTA → convert.

## Layout requirements
- Single primary CTA above the fold; secondary optional.
- Sections in intentional order: hero, benefits, social proof, objection crush, final CTA.
- `min-h-[100dvh]` hero; max content width constrained; mobile-first stack.
- No competing nav traps mid-funnel (sticky CTA OK).

## Components
- Hero (headline, subcopy, CTA, optional product visual)
- Feature grid (CSS grid, 1/2/3 cols)
- Testimonial / logo strip
- Pricing or offer block (if commerce)
- Final CTA + footer trust links
- Form fields: label, error, disabled, loading submit

## Validation / AC
- [ ] One clear conversion action measurable
- [ ] Form validation visible inline; keyboard accessible
- [ ] Lighthouse-ish basics: contrast, tap targets, no horizontal scroll
- [ ] Premium gate checklist green
- [ ] Empty/error network states for submit
