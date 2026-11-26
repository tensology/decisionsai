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

### P4 - The base template governs the render (DONE)

**Fixed.** The tokens applied but nothing else did: a link rendered a bare `<a>`
with no class, so it stayed browser-blue and underlined, and headings stayed
browser-sized. The renderer already emits each node's type as a class, so the fix
was in `to_css()`: style `.node.heading`, `.node.link a`, `.node.button button`,
`.node.input input`, `.node.table`, `.node.divider`, `.node.image` and the field
label, all from tokens.

Verified by computed style, not by eye: link `rgb(59,110,245)` (the base accent),
`text-decoration: none`, heading 28px (`font.size.5`).

### P4b - Endpoints are not pages (DONE)

53 "pages" -> **14 pages + 40 endpoints** for Tensology. A Django page view names
a template; a view that does not answers with data, however it is spelled
(`identity()` returns a project-local `api_response(...)`). Endpoints are now
reported, not drawn as blank boxes.

### P4c - Stack detection (DONE)

`stack_detect.py` answers, before anything is stencilled: which stacks are present,
and whether each serves pages or only data. The verdict rides in the sitemap.

```
Tensology   -> Pages come from react (161 files). Also present: django.
               django: pages=16 data_only=46 serves_pages=False
DecisionsAI -> react, rust, pyqt present
```

So the scan now knows it was looking at an API. **What it does not yet do is
switch to the React stack and stencil from there** - that is P1/P2 below, and it
is the next real piece of work.

### P1 - Stencil from the stack that holds the pages (DONE)

`scan_react_pages()` finds page components and the route each answers - from a
router table when there is one, and from path comparisons when the router is
hand-rolled (`if (currentPath === '/x')`), which Tensology's is. The cue extractor
already reads JSX, so a React page needed no new parser; it needed the scan
pointed at the app. `widen_routes_with_sitemap` now stencils from the detected
page stack.

**Structure coverage, measured:**

| | screens | with real content | coverage |
|---|---|---|---|
| before | 81 | 1 | **1%** |
| after | 41 | 25 | **61%** |

and a page is a page now:

```
screen "Contact" device=desktop route="/contact"
  stack
    heading "Contact"
    form "Contact"
      input "Name"
      input "Email" type=email
      input "Phone" type=tel
```

### P2 - Compose the extends chain (DONE)

A page is base + page, so `_compose_template_cue` walks `{% extends %}` (depth
capped, cycle safe, a missing parent is not an error), composes outermost-first so
the page wins on a shared key, and merges into the page's cue. The shell - header,
nav, footer - is now part of the stencil.

### P1b - Widen the cue extractor (DONE, with a correction)

**I was wrong the first time.** I reported that the extractor missed `<h1>`,
`<button>` and `<a>`. It does not - it misses them only when the content is a
single character, because of a minimum-length guard. Measured properly, it already
handled `<h1>`, `<button>`, `<a>`, `<input>`, `<textarea>` and `<select>`.

The genuine gaps were `<h4>`-`<h6>`, `<section>`, `<table>` and `<nav>`. Those are
now read, table column headers become a `table` node, and every landmark
(header/nav/main/aside/section/footer) is rendered rather than only header/hero.

Also added: **a page that composes imports borrows their cues.** `Home` is
`Hero + Partners + ...` and holds no markup of its own; without this it rendered
blank.

**Coverage: 1% -> 71%** (41 screens, 29 with real content).

### P1c - The remaining blanks are i18n, and need the rendered DOM (NEXT)

The 12 blank screens are real pages whose text is not in the component:

```jsx
<h2>{copy.moreTitle}</h2>
<h3>{offer.title}</h3>
```

A fully translated app has **no literal text in its components** - the strings
live in translation objects and are resolved at runtime. Static extraction cannot
see them, and widening the extractor will not help.

Two ways out, and both are already scoped:

1. **The translation files.** The strings ARE on disk (`i18n/publicTranslations`);
   they can seed a page's content when the component has none.
2. **The rendered DOM (P2).** Render the page with Playwright and read the real
   headings and labels. This is what P2 was for, and this is the case that
   justifies it.

### P5 - The base template governs the render
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

## Render analysis (what the output actually is)

Rendered the plan's real wire through the real renderer with the base template's
CSS, and looked at it.

**The overview screen** is a grouped link list - "Tensology journey map / 53 routes
observed / Pages: Locale, Availability, Identity, ... / Core / Account / Support".
That is a *sitemap*, and it is the one thing that reads correctly.

**Every page stencil is this, in full:**

```
screen "Locale (stale)" device=desktop route="/locale/"
  stack
    heading "Locale"
    text "Route /locale/ · group Pages"
    text "No fields or sections were found in this route's own template."
```

A heading and two lines of prose. Not a stencil.

**Five things wrong, in order of importance:**

1. **The stencils are empty by construction** (P1) - the cue extraction never
   runs for this stack, so there is nothing to draw. Everything else is
   secondary to this.
2. **The base template barely shows.** The render looks like browser defaults -
   Times, blue underlined links. `to_css()` emits vars and rules for `.wf-grid`,
   `table`, `fieldset`, `content`, but the wireframe's own stylesheet wins. **The
   base template is not actually governing the render**, which is the whole point
   of P4. This is a real bug, not a polish item.
3. **The wire is stale.** It says "53 routes observed"; the classifier now reports
   14 pages + 40 endpoints. The screens carry "(stale)" labels and `# @stale`
   markers. A plan can be shown as current when it is not.
4. **A page is not a page.** The overview is a *sitemap* and the pages are
   *stubs*, but they render in the same surface with the same affordances, so
   there is no way to tell "here is the map" from "here is a page".
5. **Nothing is interactive.** No drop targets, no grid overlay, no drag. Reading
   only.

**What that means for order:** P4 (the base template actually governing the
render) is not a later phase - it is broken now and sits directly under P1,
because a correct stencil still has to be *rendered* by something.

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
