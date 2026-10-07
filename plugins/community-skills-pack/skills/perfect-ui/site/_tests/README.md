# _tests

Manual visual + a11y test stubs for the site. Run before publishing changes.

## Visual

- [ ] Hero H1 wraps to ≤4 lines at 1440px, ≤4 lines at 1024px, ≤6 lines at 375px.
- [ ] All section headers display label + h2 + meta on desktop, stacked on mobile.
- [ ] Pipeline grid shows 4 columns at ≥900px, 2 columns at 600-900px, 1 column < 600px.
- [ ] Vibe rows collapse on mobile (header row hidden).
- [ ] Install command boxes have 6px hard shadow + hover lift.
- [ ] FAQ items expand/collapse via click + keyboard.
- [ ] Tabs cycle via ArrowLeft/ArrowRight/Home/End.

## a11y

- [ ] Skip link visible on focus.
- [ ] All buttons have visible focus ring (2px terracotta outline).
- [ ] All interactive elements reachable via Tab.
- [ ] All icons have `aria-hidden="true"` or accessible label.
- [ ] `prefers-reduced-motion: reduce` disables all GSAP animations + cursor accent.
- [ ] Image-equivalent SVG illustrations have `aria-hidden="true"` (decorative).

## Performance

- [ ] CSS total ≤ 30KB.
- [ ] No external icon library (grep `lucide` / `heroicons` / `tabler` / `phosphor` returns 0).
- [ ] No emoji in HTML / CSS / JS.
- [ ] GSAP loaded only on motion intensity ≥2/3 pages.

## Browsers tested

- [ ] Chromium 120+
- [ ] Firefox 120+
- [ ] Safari 17+
- [ ] Mobile Safari iOS 17

## Audit grep

```bash
# Forbidden patterns
grep -rn "lucide\|heroicons\|tabler\|phosphor\|fontawesome" site/
grep -rnP "[\x{1F300}-\x{1F9FF}]" site/  # emoji unicode block
grep -rn "Get Started\|Sign In\|Subscribe" site/  # generic CTAs (allowed in examples context)
grep -rn "purple-\|blue-.*gradient\|from-purple" site/  # AI gradient defaults
grep -rn "h-screen" site/  # tailwind h-screen
```
