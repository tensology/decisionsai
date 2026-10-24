---
name: frontend-stack
description: Router for the frontend design stack. Use at the start of any frontend UI task (build a page or component, redesign, pick a style or palette, polish, audit, animate, review UI) to decide which installed skill leads, which ones support, and which rule wins when skills disagree. ui-ux-system leads all Operate (app/product) work; marketing skills lead only explicit Persuade briefs.
---

<!-- GLOBAL_UI_UX_REFERENCE -->
## Global interface source

Before using this skill for interface work, read the current global `ui-ux-system` skill (`~/.agents/skills/ui-ux-system/SKILL.md`), especially its Paul's rules, settled taste rules, and Definition of Done - UI. Those rules are authoritative and override conflicting guidance here. Aesthetic recipes are opt-in: use them only when the brief explicitly calls for them and they earn their place. This skill adds only its specialized guidance.

# Frontend Stack router

Several design packs are installed side by side, and each assumes it is the only one in the room.
This router decides who leads, so the agent loads **one lead plus at most two supporting skills**
instead of competing rulebooks. `ui-ux-system` is the single UI/UX authority; everything else is a
specialist that defers to it for app, dashboard and product UI.

## 1. Read the project first

Look for what the project already decided:

- an existing design system: tokens, `tailwind.config.*`, `@theme`, `:root` variables, Unfold
  `UNFOLD["COLORS"]`, `components/ui/`, a component library in `package.json`
- `DESIGN.md` / `PRODUCT.md` at the repo root, or a brand guide the user pointed at

These outrank every skill below (ladder rule 2): extend that system, do not replace it. If two of them
disagree (palette, fonts, spacing), ask which wins. `design-system/*/MASTER.md` from
`ui-ux-pro-max --persist` is not a source of truth; do not create one, and if one exists treat it as a
draft that the project's real tokens override.

## 2. Classify the mode, then pick the lead

**Operate** = anything a user works in repeatedly: apps, admin (Django/Unfold), dashboards, settings,
tables, CRUD, forms, auth screens, account pages, internal tools. **Persuade** = marketing: landing
pages, portfolios, pricing and other public marketing pages, campaign sites. If unsure, it is Operate.

| The task is... | Lead | Support (max 2) |
|---|---|---|
| Any Operate build or change: app screen, admin, dashboard, settings, table, CRUD, form, auth | `ui-ux-system` | `ui-crud-discipline` for CRUD/admin checklists; `ui-ux-pro-max` only for a palette/font/chart lookup |
| Review or audit an Operate UI | `ui-ux-system` (section 10 + rubric) | `impeccable` (`audit`/`critique`) as optional reviewer when asked |
| Polish or harden an Operate UI before shipping | `ui-ux-system` | `impeccable` (`polish`/`harden`) on explicit request |
| Native iOS/macOS/mobile/desktop app, or an Apple HIG review | `apple-design` | `ui-ux-system` for shared states, a11y and CRUD rules |
| Persuade build with an explicit marketing brief (landing page, portfolio, marketing site) | `design-taste` or `frontend-design` (pick one) | `ui-ux-pro-max` for palette/font lookup; `perfect-ui` for landing/portfolio polish |
| Redesign an existing marketing site | `redesign-existing-projects` | `design-taste` |
| Marketing page from a screenshot or mockup, explicitly requested | `image-to-code` | `design-taste` |
| Explicit look requested by name (minimalist, brutalist) | `minimalist-ui` / `industrial-brutalist-ui` | none (presets never stack) |
| Choose a palette, font pairing or chart type | `ui-ux-pro-max` | none |
| Typography details (quotes, dashes, entities, spacing) | `ui-typography` | none |
| Build an animation | `animate` | `animation-vocabulary` when the motion is described vaguely |
| Review or fix existing motion | `review-animations` / `improve-animations` | none |
| "Where should this animate?" | `find-animation-opportunities` | none |
| Web app should feel native on phones | `mobile-native` | `ui-ux-system` |
| Try several versions of a component | `prototype` | `ui-ux-system` |
| Which library for X (charts, OTP, virtual lists) | `pick-ui-library` | none |
| Google Stitch DESIGN.md | `stitch-design-taste` (explicit request only) | none |

`frontend-design`, `design-taste`, `design-taste-frontend`, `perfect-ui`, `minimalist-ui`,
`industrial-brutalist-ui` and similar aesthetic skills **never lead Operate work** and lead Persuade work
only when the brief explicitly asks for a marketing or experience build.

## 3. Precedence ladder

When two loaded skills disagree, the higher rule wins:

1. **The user's explicit request and brand.** "Use Inter", "purple is our brand", "keep it like
   Linear" beat every skill ban.
2. **The project's existing system.** Tokens, shell, components, copy. Extend; do not swap libraries
   or mix systems.
3. **`ui-ux-system`, including Paul's taste log** (`ui-ux-system/references/taste-log.md`). For Operate
   UI this is the referee for layout, density, states, CRUD, accessibility and finish.
4. **Accessibility and correctness.** Contrast, focus, reduced motion, keyboard access, no layout shift
   (as specified in `ui-ux-system` section 7).
5. **Scope of the lead skill.** A skill's own "not for X" line is binding: `design-taste-frontend`
   says it is not for dashboards, tables or multi-step product UI, so it does not lead those.
6. **Domain owner.** Motion questions go to the animation skills, palette/font data to
   `ui-ux-pro-max`, native/HIG to `apple-design`, marketing aesthetic direction to
   `frontend-design` / `design-taste`.
7. **The lead skill's taste defaults.**

Pack-specific collisions and their rulings are in [references/conflicts.md](references/conflicts.md).

## 4. Load budget

- One lead, at most two supporting skills per task. Big skills (`design-taste`,
  `design-taste-frontend`, `ui-ux-pro-max`, `impeccable`) are long; loading several produces averaged,
  generic output.
- Aesthetic presets (`minimalist-ui`, `industrial-brutalist-ui`) never stack with each other or with
  Operate work. Pick one or none, and only on explicit request.
- `impeccable` and `apple-design` are optional reviewers, not default leads. `apple-design` applies
  only to native apps or explicit HIG reviews, never to web UI reviews.

## 5. Finish the same way every time

1. Run `ui-ux-system` section 10 (gates + rubric) and report the score.
2. If the change has motion, run `review-animations`.
3. Optional, when asked: `impeccable` `audit`/`critique`; `apple-design` for native apps.
4. Fix what they flag, then summarize what changed and what was left as-is.
