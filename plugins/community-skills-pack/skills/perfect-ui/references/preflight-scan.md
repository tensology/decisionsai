# Pre-flight Scan

Auto-detect existing project state BEFORE Phase 2 (Visual Direction) locks palette / typography / motion. Preserves what's already there; introduces only what's missing. Runs on every new build OR redesign mode (in addition to redesign-audit-checklist.md).

## When this runs

Auto-detect mode. Scan triggers if ANY of these exist in target directory:

- `package.json` (Node project)
- `tailwind.config.{js,ts,mjs}` (Tailwind project)
- `next.config.{js,ts,mjs}`, `astro.config.{js,ts,mjs}`, `vite.config.{js,ts,mjs}`, `nuxt.config.{js,ts,mjs}` (framework configs)
- `app/`, `src/`, `pages/` directory containing any TS/JS/CSS files
- `index.html` with `<link rel="stylesheet">` or inline `<style>`
- `*.css` files in repo root

If none detected: silent. Emit one line — `Pre-flight: no signals — proceeding with full perfect-ui stack.` Then continue to Phase 0.5.

## What to scan — 6 signal sources

1. **Font stack** — check:
   - `package.json` for `next/font`, `@fontsource/*`, `expo-google-fonts`, `geist`
   - `<link rel="stylesheet" href="...fonts.googleapis.com/...">` in HTML / layout files
   - `tailwind.config.{js,ts}` `theme.extend.fontFamily`
   - `@import url("fonts.googleapis.com/...")` in any stylesheet

2. **Palette** — check:
   - OKLCH / HSL / hex values inside `:root` blocks
   - `tailwind.config` `theme.extend.colors`
   - Any `tokens.json`, `design-tokens.{json,yaml}`, or DTCG-shaped file

3. **Motion library** — `package.json` dependencies for `framer-motion`, `gsap`, `motion`, `lenis`, `lottie-react`, `@react-spring/*`, `auto-animate`. Any one = "motion-on" project. None = "motion-cut" project.

4. **Spacing scale** — Tailwind `theme.extend.spacing`; CSS `--space-*` custom-property pattern; 4-pt / 8-pt scale presence.

5. **Framework** — Next.js (`next` in deps), Astro (`astro`), Vue (`vue`), Svelte / SvelteKit (`svelte` / `@sveltejs/kit`), Remix (`@remix-run/*`), Nuxt (`nuxt`), or vanilla HTML.

6. **Existing icon library — FLAG for replacement** — `package.json` for `lucide-react`, `@heroicons/react`, `@phosphor-icons/react`, `@tabler/icons-react`, `react-icons`, `font-awesome`, `material-icons`. ANY one detected = forbidden per Hard Rule #2; will be replaced with custom SVG icons during Phase 3.

## Output format

Emit before Phase 2 dialog:

```
Pre-flight findings:
· Font stack: {detected fonts} ({source file:line})
· Palette: {OKLCH | HSL | hex | DTCG tokens} ({source})
· Motion: {framer-motion 11 | gsap | none} ({source})
· Spacing: {Tailwind extend | 4-pt scale | none}
· Framework: {detected framework}
· Icon library FLAG: {lucide-react detected — will replace per Hard Rule #2}

perfect-ui will preserve: {list — fonts, palette, spacing}.
perfect-ui will introduce: {list — vibe + custom icons + anti-slop + motion intensity + Tier 1/2/3 audit}.

If you want perfect-ui to override any preserved item, say so before Phase 2 locks.
```

Cap output at ≤6 bullet lines + 2 summary lines + 1 override prompt = 9 lines max.

## Persistence

Write findings to `.perfect-ui/preflight.json` at project root once. Subsequent runs reuse cached findings unless either:

- User says "refresh pre-flight" / "scan again" / "re-scan"
- `package.json` / `tailwind.config.*` / framework config mtime newer than `preflight.json`

If cached, emit one-line note instead of full block: `Pre-flight cached (last scan: 2026-MM-DD). Say "refresh pre-flight" to re-scan.`

Recommend adding `.perfect-ui/` to project `.gitignore` (skill suggests once on first scan; respects existing `.gitignore`).

## Edge cases

- **Conflicting signals** (e.g., `framer-motion` installed but no `motion.div` usage anywhere; or `Geist` import in `package.json` but hard-coded `font-family: Inter` in CSS) → flag the conflict explicitly: *"Conflict: Geist imported via next/font but a hard-coded `font-family: Inter` in app/globals.css L4. I'll preserve next/font Geist; please confirm or remove the Inter declaration."*
- **No signals found** (vanilla HTML project, empty repo, scratch directory) → one line only: *"Pre-flight: no signals — proceeding with full perfect-ui stack."*
- **User said "ignore the existing project" / "fresh start"** → skip pre-flight entirely; emit *"Pre-flight skipped at user request."* and proceed to Phase 0.5.
- **`.perfect-ui/preflight.json` corrupt** → regenerate silently.
- **Empty project** (no `package.json`, no `index.html`, no `*.css`) → silent. No emit.

## Sample outputs

**Vanilla HTML project, motion-cut:**
> *Pre-flight findings: vanilla HTML, no framework detected. No motion library, no Tailwind, no design tokens. perfect-ui will introduce: full token system, vibe + custom icons + anti-slop + motion intensity + Tier 1/2/3 audit. Nothing to preserve.*

**Astro + Tailwind + DTCG tokens already present:**
> *Pre-flight findings: Astro 5 (astro.config.mjs L1) · Tailwind v4 with `@theme` inline tokens (src/styles/global.css L3) · `tokens.json` at project root (DTCG format, 12 colour tokens, 6 font tokens). No motion library detected.*
> *perfect-ui will preserve: Tailwind tokens, `tokens.json` (won't overwrite). perfect-ui will introduce: vibe + custom icons + anti-slop + motion intensity + Tier 1/2/3 audit. Motion stance: motion-cut.*

**Next.js with forbidden icon library:**
> *Pre-flight findings: Next.js 15 (app router) · Geist + Geist Mono via `next/font` (package.json L23) · OKLCH custom properties (app/globals.css `:root`) · `framer-motion` 11 (package.json L41) · Tailwind extend.spacing 4-pt scale (tailwind.config.ts L18).*
> *Icon library FLAG: `lucide-react` detected (package.json L29) — will replace per Hard Rule #2 during Phase 3.*
> *perfect-ui will preserve: font stack, palette, spacing scale, motion library. perfect-ui will introduce: vibe lock, custom SVG icon set, anti-slop audit. Replacing: lucide-react imports.*

The pre-flight block is the user's accountability line: *"here's what perfect-ui noticed about your project before it touched anything."* Skipping it on a populated repo = fastest way to lose the user's trust.
