# Where the packs disagree

Each entry names the conflicting rules, where they come from, and the ruling
the stack applies. Rulings follow the precedence ladder in `../SKILL.md`.

## Motion: how much, and when

- **taste** (`design-taste-frontend`, `high-end-visual-design`,
  `stitch-design-taste`) pushes "perpetual micro-motion", kinetic type, GSAP
  scroll hijacks and staggered reveals as part of a premium feel.
- **Emil** (`emil-design-eng`, `animate`) starts from "should this animate at
  all", keeps UI motion short (roughly 125-300 ms), never uses `ease-in` for UI,
  and treats motion as functional first.
- **Ruling:** in product UI (anything a user operates repeatedly), Emil wins:
  purposeful, short, interruptible, `ease-out`, respects
  `prefers-reduced-motion`. On a marketing page, taste may lead the hero and
  scroll storytelling, but every interactive control (buttons, menus, dialogs,
  toasts) still follows Emil's timing rules.

## Motion library

- **taste** reaches for GSAP for scroll and kinetic effects; **UI UX Pro Max**
  ships GSAP presets (`--domain gsap`).
- **Emil** uses CSS transitions first and Motion (Framer Motion) springs, and
  warns that Motion's `x`/`y` shorthands run on the main thread.
- **Ruling:** CSS first. Use the library the project already has. Add GSAP only
  for scroll-driven storytelling on marketing pages; add Motion for springs and
  gestures in React. Never both in the same component.

## Fonts

- **taste** `design-taste-frontend` discourages Inter as a default (allowed on
  request or for neutral / accessibility-first briefs).
  `high-end-visual-design` (archived 22 Oct 2026) banned Inter, Roboto, Arial,
  Open Sans and Helvetica outright.
- **frontend-design** asks for distinctive type chosen for the subject.
- **UI UX Pro Max** font-pairing search can return Inter-based pairings.
- **Ruling:** brand / existing system first. For a fresh project, avoid Inter as
  an unconsidered default, but it is fine when the user asks, when the product
  is dense and neutral (admin, data tools), or when the project already uses
  it.

## Purple and gradients

- **taste** "Lila rule": no default AI-purple glows or neon gradients.
- **taste** `high-end-visual-design` "Ethereal Glass" archetype uses purple and
  emerald radial mesh gradients on OLED black.
- **Ruling:** the Lila rule is the default. The preset's gradient look only
  applies when the user picked that preset or the brand is purple.

## Who owns `DESIGN.md`

- **Impeccable** reads `PRODUCT.md` and `DESIGN.md` as project context and writes
  `DESIGN.md` via `document` / the `impeccable-documenter` agent.
- **taste** `stitch-design-taste` also writes a `DESIGN.md`, in Google Stitch
  format.
- **Ruling:** Impeccable owns `DESIGN.md`. The Stitch skill only runs on
  explicit request, and then writes to `DESIGN.stitch.md` unless the user says
  otherwise, so Impeccable's context is not overwritten.

## Scope of design-taste-frontend

- Its own header: "Landing pages, portfolios, and redesigns. Not dashboards,
  not data tables, not multi-step product UI."
- **Ruling:** binding. For product (Operate) UI the lead is `ui-ux-system`;
  `frontend-design` and the taste skills lead only explicit Persuade briefs.

## Component libraries

- **taste**: one design system per project, never ship shadcn/ui in default
  state.
- **UI UX Pro Max** `ui-styling`: shadcn/ui + Tailwind as the main path.
- **Emil** `pick-ui-library`: opinionated per-problem picks (Sonner, cmdk,
  Vaul...).
- **Ruling:** these agree once combined. Keep the project's system; shadcn/ui is
  the default for new React + Tailwind projects and must be themed (radius,
  color, type) before shipping; use Emil's picks for the problems shadcn does
  not cover.

## Em dashes in UI copy

- **taste** bans the em dash (`—`) anywhere on the page, calling it the top "AI
  tell".
- `ui-ux-system` (the UI authority) owns the ruling.
- **Ruling:** settled by `ui-ux-system` section 8a: no em dashes in UI copy,
  rationale or labels the agent writes; do not rewrite copy the user supplied;
  en dashes for numeric ranges are fine. `ui-typography`'s entity table
  documents the character but does not override this ruling.

## Review before shipping

- **Impeccable** `audit` / `critique` / `polish`, **Emil** `review-animations`,
  **taste** pre-flight checklist and **frontend-design**'s anti-template list
  all review the same page from different angles.
- **Ruling:** do not run all of them. `ui-ux-system` section 10 always;
  Emil's review when the diff touches motion; Impeccable `audit`/`critique`
  only when asked; taste's checklist only on marketing pages it led.
