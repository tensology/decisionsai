# Component Scope

When user brief = 1 UI element (button / card / modal / dropdown / etc.), perfect-ui collapses page-level apparatus and emits a self-contained component + 8-state preview wrapper. NOT for full pages — those stay in page-scope.

## When this applies (auto-detect at Phase 0.5)

Multi-signal detection. **2+ signals → component scope confirmed.**

### Component-scope signals

- **Brief mentions single UI element:** button · input · card · modal · dropdown · tooltip · select · checkbox · switch · tab strip · chip · badge · banner · snackbar · popover · slider · date picker · avatar
- **Brief is short (≤30 words)** and refers to one element
- **Target file is single component** (e.g. `./Button.tsx`, `app/components/Card.vue`)
- **User says** *"just the X"*, *"only the Y"*, *"this one element"*, *"a single ___"*
- **Explicit `--component` flag** passed

### Routing rule

- If **2+ signals fire** → component scope. Confirm with one-line note to user.
- If only page-flow signals fire (multi-section brief, "build me a landing page") → stay in page scope.
- If **exactly 1 signal** fires (ambiguous) → ask via `AskUserQuestion`: *"One component or whole page?"*
- Default if user doesn't engage → component (single-artifact output is cheaper to redirect than multi-section page).

## What component-scope KEEPS from page flow

- **Phase 0 pre-flight scan** — read existing tokens, fonts, framework, microinteraction stance (same)
- **Phase 1 genre detection** — editorial / modern-minimal / atmospheric / playful (same)
- **Phase 2 vibe + palette + typography** — if `tokens.css` or `design.md` exists, use those; else ask user
- **Phase 2.6 Brand Motion Identity** — personality + 3 constants apply to component
- **2+1 font discipline** — same
- **State discipline — STRICTER.** Every interactive component MUST ship code for **all 8 states**
- **Anti-slop universal subset** — visual / microinteraction / contrast / a11y / typography gates

## What component-scope SKIPS

- **Phase 2.5 Macrostructure pick** — components don't have macrostructures. State explicitly: *"Component-scope: skipping macrostructure."*
- **Phase 2c Spatial language** — single element; no page-level spatial
- **Nav + footer archetypes** — page-scope only. A component is one element; it has no nav, no footer.
- **Hero enrichment patterns** — page-scope only. A button or card has no hero.
- **Phase 8 visual checks** — no full page to render
- **`.perfect-ui/log.json` write** — component runs don't rotate; diversification rule doesn't apply

## What component-scope EMITS

**Two files, side by side:**

### 1. Component artifact

Single self-contained file matching project conventions:

- **React / Next.js:** `Button.tsx`
- **Vue / Nuxt:** `Button.vue`
- **Svelte / SvelteKit:** `Button.svelte`
- **Vanilla web:** `button.css` + `button.html`
- **Tailwind project:** a `.tsx` with `className` chains AND a `tokens.css` if missing

Component consumes perfect-ui tokens by name (`var(--color-accent)`), never inlines OKLCH values.

### 2. 8-state preview wrapper

Auto-detect framework from pre-flight scan; emit matching preview file:

- React → `<ComponentName>.preview.tsx`
- Vue → `<ComponentName>.preview.vue`
- Svelte → `<ComponentName>.preview.svelte`
- Vanilla → `<ComponentName>.preview.html`

A standalone page rendering the component in **all 8 states** stacked vertically with labels:

```
┌──── Button — 8 states ────────────────────────┐
│                                                │
│ default       [ Click me                  ]    │
│ hover         [ Click me                  ]    │  ← .is-hover forces :hover styling
│ focus         [ Click me                  ]    │  ← .is-focus forces :focus-visible
│ active        [ Click me                  ]    │  ← .is-active forces :active
│ disabled      [ Click me                  ]    │  ← disabled attr
│ loading       [ ⌛ Working…                ]    │  ← data-state="loading"
│ error         [ ⚠ Try again               ]    │  ← data-state="error"
│ success       [ ✓ Saved                   ]    │  ← data-state="success"
│                                                │
└────────────────────────────────────────────────┘
```

Each labelled row uses a class (e.g. `.is-hover`) that the component's CSS targets in addition to the real pseudo-class, so all 8 states render at once on the demo page:

```css
.btn:hover, .btn.is-hover { background: var(--color-paper-3); }
.btn:focus-visible, .btn.is-focus { outline: 2px solid var(--color-focus); }
.btn:active, .btn.is-active { transform: translateY(1px); }
```

User opens preview once, sees the component working, then deletes it. The wrapper is NOT part of production code.

## Stamp format for component output

Components stamp differently from pages:

```css
/* perfect-ui · component: <type> · genre: <genre> · vibe: <vibe>
 * states: default · hover · focus · active · disabled · loading · error · success
 * contrast: pass
 */
```

The `component:` prefix tells future perfect-ui runs this artifact is component-scoped — won't trigger page-level diversification rules. The `states:` line is a checklist — every state listed must have actual styling in the file.

## Cross-references

- `workflow-phases.md § Phase 0.5 Step 7` — component-scope detection routes here
- `workflow-implement.md § Step 1.6` — Phase 7 component-scope short-circuit
- `workflow-audit.md § Step Y` — Phase 8 component-scope subset
- `preflight-scan.md` — framework detection feeds preview file extension
- `motion-patterns.md § Motion Personalities` — Brand Motion Identity applies to component
