# Tokens

Use the project's tokens first. This file gives (A) how to adopt them, (B) the default set with verified
contrast, (C) Tailwind / CSS-variable / Unfold mappings.

## A. Adopt existing tokens (always do this first)

1. Search: `tailwind.config.*` (`theme.extend`), CSS `@theme { … }` (Tailwind v4), `:root { --… }`,
   `globals.css` (shadcn: `--background`, `--foreground`, `--primary`, `--muted`, `--border`, `--ring`,
   `--radius`), Django `settings.py` `UNFOLD = {"COLORS": …}` / brand helpers, existing component classes
   (`.btn`, `Button.tsx`, `forms.py` widgets).
2. Build a role map: write down which existing value plays `bg`, `surface`, `border`, `text`, `text-muted`,
   `accent`, `focus`, `success`, `warning`, `danger`, radius sizes, font families, spacing in use.
3. Frequency check before inventing anything: count the most-used spacing/radius/text utilities
   (`grep -oE '\b(p|px|py|gap|m[trblxy]?)-[0-9.]+' -r src | sort | uniq -c | sort -rn | head`). The top
   values are the de facto scale; reuse them.
4. Fill only missing roles from the defaults below and say which you added.
5. Never mix two systems (e.g. raw `gray-*` in new code when the project uses `slate-*` or CSS vars).

Observed in Paul's projects (use as the house default when a project is ambiguous):
- Tailwind neutrals `gray`/`slate`, accent blue (`#3b82f6` brand, blue-600 for filled buttons).
- Most-used spacing `2`, `3`, `4` (8/12/16px); most-used text `text-sm`, then `text-xs`; radius `md`/`lg`.
- Brand font Quicksand (Tensology, Xtract, Systems, Decisions); Inter in Mailshot; system UI stack elsewhere.
- Plain-CSS projects (Xtract, Systems) use short role names: `--blue`, `--blue-dark`, `--blue-pale`,
  `--slate`, `--muted`, `--line`, `--surface`, `--success`. Reuse that naming in plain-CSS work.

## B. Default token set

### Color roles (light). Contrast measured against white (and slate-50).

| Role | Tailwind | Hex | Contrast | Use |
|---|---|---|---|---|
| bg | white | #ffffff | – | page/app background |
| surface | slate-50 | #f8fafc | – | sidebar, table header, subtle panels |
| surface-hover | slate-100 | #f1f5f9 | – | row/menu hover |
| border | slate-200 | #e2e8f0 | 1.23 | dividers, card edges (decorative) |
| border-input | slate-300 / slate-500 | #cbd5e1 / #64748b | 1.48 / 4.76 | control borders; WCAG 1.4.11 needs ≥3:1 when the border is the only boundary cue, so use slate-500 or add a fill + visible label |
| text | slate-900 | #0f172a | 17.85 | primary text |
| text-muted | slate-600 | #475569 | 7.58 | secondary text, table headers |
| text-subtle | slate-500 | #64748b | 4.76 | placeholders, meta (min allowed for small text) |
| accent | blue-600 | #2563eb | 5.17 (white on it 5.17) | primary buttons, links, selected |
| accent-hover | blue-700 | #1d4ed8 | 6.70 | hover/pressed |
| accent-subtle | blue-50 | #eff6ff | – | selected row, info callout bg |
| focus | blue-500 ring 2px + 2px offset | #3b82f6 | 3.68 (≥3:1 OK for indicators) | focus-visible ring |
| success | green-700 | #15803d | 5.02 | success text/icons; bg green-50 |
| warning | amber-700 | #b45309 | 5.02 | warning text; bg amber-50 |
| danger | red-600 | #dc2626 | 4.83 | errors, destructive; hover red-700 (6.47); bg red-50 |
| overlay | slate-900/50 | – | – | modal/drawer scrim |

Do not use blue-500 as a filled-button background with white text (3.68:1 fails 4.5:1). Do not use
slate-400 for text (2.56:1).

### Color roles (dark). Against slate-900 #0f172a (and slate-800).

| Role | Tailwind | Contrast |
|---|---|---|
| bg | slate-900 | – |
| surface | slate-800 | – |
| border | slate-700 | decorative only |
| text | slate-100 | 16.3 |
| text-muted | slate-400 | 6.96 (5.71 on slate-800) |
| accent | blue-400 text / blue-500 fill with slate-950 text or blue-600 fill with white | 7.02 |
| success / warning / danger | green-400 / amber-400 / red-400 | 10.25 / 10.69 / 6.45 |

Set `color-scheme: dark` on `<html>` in dark mode; theme native `<select>`, scrollbars, date inputs.
A theme is incomplete if any menu, dialog, toast, empty or error state keeps light colours.

### Spacing (4px base)

`0 2 4 6 8 12 16 20 24 32 40 48 64` px → Tailwind `0 0.5 1 1.5 2 3 4 5 6 8 10 12 16`.

| Use | Value |
|---|---|
| icon ↔ label, badge padding | 4–6 |
| control group gap, toolbar padding-y | 8 |
| label ↔ input, field ↔ helper | 4–6 |
| between fields | 12–16 |
| card/panel/table-cell padding-x | 12–16 |
| section gap | 24 |
| page gutter | 16 mobile / 24 desktop |
| marketing section padding | 48–64 (Persuade mode only) |

### Type scale

| Token | px size / line-height | Tailwind | Use |
|---|---|---|---|
| caption | 12/16 | text-xs | meta, table headers, badges, helper |
| body | 14/20 | text-sm | app body, inputs, buttons, table cells |
| lead | 16/24 | text-base | marketing body, mobile inputs (prevents iOS zoom) |
| title-sm | 18/28 | text-lg | section/card/modal title |
| title | 20/28 | text-xl | page title in app shell |
| title-lg | 24/32 | text-2xl | settings/landing sub-heads |
| display | 30/36+ | text-3xl+ | marketing headline only |

Weights: 400 body, 500 labels/buttons/table headers, 600 titles. Line length 45–80ch for prose.
Numbers in tables: `tabular-nums`, right-aligned. Ellipsis `…` and loading text "Saving…".

### Radii

| Token | px | Tailwind | Use |
|---|---|---|---|
| r-sm | 4 | rounded | badges, checkboxes, tags |
| r-md | 6 | rounded-md | inputs, buttons |
| r-lg | 8 | rounded-lg | cards, menus, popovers, panels |
| r-xl | 12 | rounded-xl | modals, drawers, sheets |
| r-full | 9999 | rounded-full | avatars, status dots, pills |

Nested radius: inner = outer − padding (a 12px modal with 8px padding holds 4px-radius items).

### Elevation

| Level | Style | Use |
|---|---|---|
| 0 | none, 1px border | default surfaces, cards in lists |
| 1 | shadow-sm | sticky header, raised card |
| 2 | shadow-md, no border | dropdowns, popovers, comboboxes, menus, snackbars |
| 3 | shadow-xl + overlay | modal, drawer |

### Motion

| Token | Duration | Easing | Use |
|---|---|---|---|
| instant | 100ms | ease-out | hover, press, colour change |
| fast | 150ms | ease-out | menu, tooltip, popover |
| base | 200ms | ease-out enter / ease-out exit 150ms | modal, snackbar |
| slow | 250–300ms | ease-out | drawer/sheet |

Animate only `transform`/`opacity`; list properties (no `transition: all`). Under
`prefers-reduced-motion: reduce` drop translate/scale, keep ≤100ms opacity. Material 3 uses the same
short (50–200ms) / medium (250–400ms) bands.

### Z-index

`base 0 · sticky 10 · dropdown 20 · overlay 40 · modal 50 · toast 60`. Dropdowns inside modals use the
modal layer + 1.

## C. Mappings

### CSS variables (plain CSS / Django templates)

```css
:root{
  --bg:#fff; --surface:#f8fafc; --line:#e2e8f0; --line-input:#64748b;
  --ink:#0f172a; --muted:#475569; --subtle:#64748b;
  --accent:#2563eb; --accent-hover:#1d4ed8; --accent-pale:#eff6ff; --focus:#3b82f6;
  --success:#15803d; --warning:#b45309; --danger:#dc2626; --danger-hover:#b91c1c;
  --r-sm:4px; --r-md:6px; --r-lg:8px; --r-xl:12px;
  --shadow-1:0 1px 2px rgb(15 23 42/.06); --shadow-2:0 4px 12px rgb(15 23 42/.10); --shadow-3:0 20px 40px rgb(15 23 42/.18);
  --dur-1:100ms; --dur-2:150ms; --dur-3:200ms; --dur-4:250ms;
  --font-sans: Quicksand, Inter, ui-sans-serif, system-ui, sans-serif; /* replace with project font */
}
```

### Tailwind (CDN or v3 config)

```js
tailwind.config = { theme: { extend: {
  colors: { accent: { DEFAULT:'#2563eb', hover:'#1d4ed8', pale:'#eff6ff' }, danger:{ DEFAULT:'#dc2626', hover:'#b91c1c' } },
  borderRadius: { DEFAULT:'4px', md:'6px', lg:'8px', xl:'12px' },
  transitionDuration: { 100:'100ms', 150:'150ms', 200:'200ms', 250:'250ms' },
}}}
```

Tailwind v4: put the same values in `@theme { --color-accent: #2563eb; … }`.

### Django Unfold

Unfold derives its palette from `UNFOLD["COLORS"]["primary"]` (50–950 scale). Map `primary.600` to the
accent, keep Unfold's own spacing/components, and do not restyle admin chrome with custom CSS unless
asked. Use `SITE_SYMBOL`, sidebar `navigation` groups and `fieldsets`/tabs for task grouping rather than
custom templates.

### shadcn/ui

Map roles to `--background`, `--foreground`, `--muted-foreground`, `--border`, `--input`, `--ring`,
`--primary`, `--destructive`, `--radius` (set `--radius: 0.5rem`). Use the existing `components/ui/*`;
do not hand-roll a second Button/Dialog.
