# Planning: widget & layout library, base template, and tickets from plans

Date: 2026-11-26
Status: A-G IMPLEMENTED (see commits 7c608388, 885c2bf7, 0750cd62, e4762b22, 56b047a3, e4bdc5ec, b91d887d). H designed separately, not yet started.
Owner: Paul

## Goal

Make the planning area produce real interfaces instead of bare-minimum boxes:
a **dynamic library** of widgets, layouts and page templates (picked up from
folders, like skins), governed by a **base template** that owns every design
decision, so wireframes, sitemaps and the tickets generated from them are
composed rather than hand-written.

The payoff is section G: once a plan is built from a library, a ticket becomes a
diff against real components ("add a KPI row (library: dashboard.kpi)") instead
of prose. "Tickets from planning are terrible" and "there is no templating" are
the same problem.

## Non-goals

- Not a replacement for the existing wireframe DSL or renderer. The library
  composes the existing primitives; it does not replace them.
- Not a general-purpose design tool. It serves the planning surfaces.
- Not a WYSIWYG page builder for the real app. Wireframes describe; they do not
  generate production components.

## The central split

**The base template owns every decision. A page owns only placement.**

| Layer | Owns | Example |
|---|---|---|
| **Base template** | tokens, component contracts, the grid | type scale, colour, spacing; "an input renders with a *legend*, not a label"; what a table looks like; how `content` renders markdown |
| **Page** | grid placement + which components | "a form here, a table there" |

A page that says "a form goes here" receives the base's form standard. It never
re-decides what a form looks like. Changing the base propagates to every page.

Inheritance is **inherit-everything, override-by-exception**: the base defines
type scale, spacing, form layout, head/footer and grid; a page overrides only
what it explicitly changes.

## Section A - Library folders

Mirrors `skin_discovery.py`: scan a directory for folders containing a valid
manifest, exclude invalid ones with a logged warning, never fail the caller.

```
<library_root>/<entry-name>/
    manifest.json      # required
    preview.png        # optional
    ...assets
```

**Library root:** user data (`~/.decisions/planning/library/`) so entries are
shareable and editable without touching the repo, with a small built-in set
shipped in `assets/planning-library/` as the default. User entries override
built-ins of the same name.

**Manifest** (validated; invalid entries are skipped, never fatal):

```json
{
  "name": "kpi-row",
  "kind": "widget",              // widget | layout | page-template
  "title": "KPI row",
  "scope": "global",             // global | product:<slug>
  "tags": ["dashboard", "metrics"],
  "description": "A row of headline metrics.",
  "wire": "row { ... }",         // the existing DSL this entry emits
  "slots": [],                   // optional named slots
  "version": 1
}
```

- `kind` decides *where* it can be dropped (page templates at sitemap level,
  widgets inside a page).
- `scope` is the product-specific distinction: a `product:player1sport` entry is
  offered only when working on that product, so a bespoke widget never pollutes
  the general library.
- `wire` uses the **existing** wireframe DSL, so the renderer is unchanged.

## Section B - Base template

A base template is a library entry of `kind: "base-template"` (or a page marked
as the base). It carries:

- **tokens**: colour, type scale, spacing, radii, shadows, breakpoints
- **contracts**: form field rendering (legend vs label), table styling,
  `content` markdown rendering, button variants, head/footer shell
- **grid**: the layout system pages place into

Pages inherit all of it and override by exception.

## Section C - Pages

A page is: grid placement + component references + explicit overrides. It pulls
everything else from its base template.

## Section D - Sitemap and flows

Page templates are dropped at this level to create pages; pages are arranged and
linked. Reuses the existing sitemap/flow structures.

## Section E - Side panel

One panel that swaps between **chat** and **library**. Its contents are
screen-aware:

- sitemap / flow level -> page templates
- inside a page -> widgets

Drag targets: sitemap, wireframe, flow, or directly into a page.

## Section F - Derivation from local projects

Scan local projects to extract real components and emit library entries, so
wireframes resemble the operator's actual products.

**The AI consults the library first** and only invents something custom when
nothing fits. Product-specific widgets are written with `scope:
"product:<slug>"` rather than into the general library.

## Section G - Tickets from plans

Tickets become add / update / remove operations against library components,
templated from the same library, rather than free prose. This is the section that
fixes the "tickets from planning are terrible" complaint.

## Section H - Frustration scoring (designed separately)

The agent noticing its own and the operator's friction and scoring it. A
different kind of system from A-G; deliberately not designed here.

## Error handling

- A malformed library entry is skipped with a logged warning, never fatal -
  same contract as `discover_skins`.
- A missing base template falls back to the built-in default rather than
  rendering nothing.
- Derivation (F) is best-effort and writes nothing on failure.

## Testing

- Discovery: valid entries load; invalid are skipped with a warning; user
  entries override built-ins of the same name.
- Manifest validation: every required field, every `kind`, every `scope` form.
- Base template: a page with no overrides renders from the base; an override
  changes only that page.
- Panel: contents differ by screen (templates vs widgets); `scope` filtering.
- Tickets (G): a plan built from the library produces component-level tickets.

## Phases

- **A** library folders - done (`7c608388`)
- **B** base template - done (`885c2bf7`)
- **C** pages - done (`0750cd62`)
- **D** sitemap - done (`e4762b22`)
- **E** side panel (library API + panel) - done (`56b047a3`)
- **F** derivation, library-first - done (`e4bdc5ec`)
- **G** tickets as component diffs - done (`b91d887d`)
- **H** frustration scoring - **not started**; needs its own design pass

Tests: `tests/core/planning/` (139) + `tests/gui/test_plan_library_panel_js.py` (8).

Still to wire (follow-on, not part of A-G): the panel is mounted by hand today -
the Development planning surface does not yet mount it, and nothing yet persists
a sitemap/page built from the library to a workspace.
