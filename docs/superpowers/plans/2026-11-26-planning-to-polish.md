# Planning → polish: a production-ready render from the plan

Date: 2026-11-26
Status: proposed
Owner: Paul

## The problem, stated exactly

A stencil for a real page currently reads:

```
screen "Locale (stale)" device=desktop route="/locale/"
  stack
    heading "Locale"
    text "Route /locale/ · group Pages"
    text "No fields or sections were found in this route's own template."
```

The scan found the **route** and nothing about the **page**. That is why the
canvas is 81 grey boxes labelled "Page stencil". The scan extracts `fields`,
`headings`, `buttons` and `links` from the route's own template; when that
resolution fails, it says so and stops. Most routes fail.

So the pipeline is backwards: it guesses structure from source, and when the
guess fails it renders nothing. A plan for Merrypak must produce stencils that
look like **Merrypak's actual pages**.

## What "production ready" means (the acceptance bar)

For a real project (Merrypak, Player1Sport, Tensology), the render from the plan:

1. **Resembles the real page** - a reviewer can tell which page it is without the
   route label. Real headings, real labels, real navigation, real data shape.
2. **Obeys the base template** - tokens, contracts, grid. One edit restyles every
   page.
3. **Is composed from the library** - a form is a `form`, a list is a
   `data-table`, not free text.
4. **Is interactive enough to judge** - a stencil can be dropped into, moved, and
   previewed at phone/desktop widths.
5. **Is honest about gaps** - a page the scan could not resolve says so *in the
   UI*, rather than pretending with an empty box.

## The measurement

Every phase is judged by one number we can compute, not by opinion:

- **Structure coverage** = pages whose stencil contains at least one real
  element (a heading, field, button, link, table or image) that came from the
  page, over total pages in the plan.
- Today, for Tensology: **1 of 81** (only `Create` has a screenshot).
- Target: **>= 90%** for the reference projects.

A phase that does not move that number, or the render fidelity, is not done.

## Phases

### P0 - Why extraction fails (ANSWERED)

`project_scan.scan_page_cues()`:

```python
for row in routes:
    if Path(row.get('source') or '').suffix.lower() not in {'.js', '.jsx', '.ts', '.tsx'}:
        # A same-named component is not evidence that a Python HTML handler
        # renders it. Template evidence comes from the handler/template link.
        continue
```

**Cue extraction is JS/TS only.** Tensology and Merrypak are Django/Python, so
every route is skipped, every cue is absent, and every stencil falls through to
"No fields or sections were found in this route's own template."

That single line explains the 81 blank stencils. It is not a template-resolution
bug to be chased per framework: the Python path was never wired to the cue
extractor at all.

That is only half the story, and the second half is more useful. The Django
machinery is **already there and works**:

- `_template_index()` indexes the project's templates (Tensology: **50**);
- `{% extends %}` / `{% include %}` are resolved;
- `_template_cue()` extracts a real cue from **33 of those 50**;
- `widen_routes_with_sitemap()` merges template cues into `_observed_cues()`,
  which is what the stencil builder reads.

**What is missing is one link: view -> template.** Measured on Tensology:

```
page nodes:                    53
nodes with a resolved template:  4      <- 49 of 53 are blank
  /locale | view: locale | template: ''
templates indexed:              50
templates yielding a cue:       33
template_cues keyed to routes:   0
```

The route knows its **view** (`locale`). The template (`locale.html`) is indexed.
Nothing joins them, so `node["template"]` is empty, so no cue is produced, so
every stencil falls through to "No fields or sections were found".

**So P1 is: resolve `view` -> `template` for the Python stack** - a name-based
join, not a rewrite. Everything downstream of it already exists and is tested.

**Exit (done):** root cause identified, measured, and written down. No code
changed.

### P1 - Resolve the template, for real (the biggest single win)
Fix template resolution per stack: Django (`urls.py` -> view -> template),
React/Vue route -> component file, and a static-HTML fallback. This is the
existing path, made to work rather than replaced.

**Exit:** structure coverage rises sharply; `/locale/` renders its real fields and
buttons.

### P2 - Screenshots for what source cannot tell us
For routes that still resolve to nothing - dynamic pages, client-rendered apps -
drive the page with Playwright (the `interface-shots` path already exists) and
derive layout from the rendered DOM: landmarks, headings, forms, tables, nav.

**Exit:** every route has *either* source-derived or render-derived structure;
coverage reaches the 90% target.

### P3 - Recognise components, do not emit text
Map extracted elements onto the **library**: a `<form>` becomes `form`, a table
`data-table`, a KPI cluster `kpi-row`, nav `app-shell`. The stencil then says
`row { kpi-row }` rather than three lines of prose.

**Exit:** stencils are library references; a ticket from the plan names real
components (Section G already does this - it just has nothing to name today).

### P4 - The base template governs the render
Every stencil renders through the base template: tokens, form standard, table
styling, grid. This is what makes it look like one product instead of 81 sketches.

**Exit:** changing one base token visibly changes every stencil.

### P5 - Real content, not placeholders
Seed each stencil with the page's actual text and data shape - the real heading,
the real field labels, the real column names, the real nav items. This is the
difference between "looks like a wireframe" and "looks like the product".

**Exit:** a reviewer can identify the page from the render alone.

### P6 - Drag and drop that feels right
The library becomes a **visible strip**, not a hidden panel:

- page templates at sitemap level, widgets inside a page;
- each entry has a **thumbnail**, not a text row;
- dragging shows a **grid overlay** on the target page, so you can see where it
  lands before you release;
- a drop lands where you aimed, and undo puts it back.

**Exit:** a page can be assembled from the library in under a minute without
reading any documentation.

### P7 - Production render
The final artefact: the plan renders a page that looks like the product, at
desktop and phone widths, with real components and real content - the thing you
would show a client.

**Exit:** the acceptance bar above, met, on Merrypak.

### P8 - Polish
Empty / loading / error states; keyboard reach for every action; a11y pass;
performance (a 80-page plan opens without stalling); and the honest-gap UI from
the acceptance bar.

## Order and why

P0 -> P1 -> P2 are **fidelity**: without real structure, nothing downstream can
look right. P3 -> P4 make it *composed* and *consistent*. P5 makes it *real*.
P6 is the interaction. P7 is the payoff. P8 is what separates "works" from
"shippable".

P0 is deliberately first and cheap: it replaces guessing with a measured list.

## What is already in place (do not rebuild)

- Library, base template, pages, sitemap model, tickets as component diffs.
- **One plan**: the workspace's `flows` wire, which the canvas already renders.
- The plan persists into the project's planning folder.
- `interface-shots` can screenshot real pages with Playwright.
- The wireframe renderer, with `baseCss` injection.
- Ticket generation as a component diff.

## Risks

- **Template resolution is per-framework.** P1 is where the effort is, and it
  will not be one fix.
- **Screenshots are a fallback, not a replacement.** A DOM-derived layout is
  approximate; it must be marked as such so nobody treats it as a spec.
- **The library is only as good as what it has.** If P3 finds a form and the
  library has no form entry, it must fall back rather than invent.
