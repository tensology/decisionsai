# Planning → polish: a production-ready render from the plan

Date: 2026-11-26
Status: in progress - P0-P5 done, P6-P8 remaining
Owner: Paul

## The problem, stated exactly

A stencil for a real page originally read:

```
screen "Locale (stale)" device=desktop route="/locale/"
  stack
    heading "Locale"
    text "Route /locale/ · group Pages"
    text "No fields or sections were found in this route's own template."
```

The scan found the **route** and nothing about the **page**. The canvas was 81
grey boxes labelled "Page stencil". The pipeline was backwards: it guessed
structure from source, and when the guess failed it rendered nothing.

## The acceptance bar

For a real project (Merrypak, Player1Sport, Tensology), the render from the plan:

1. **Resembles the real page** - a reviewer can tell which page it is without the
   route label.
2. **Obeys the base template** - tokens, contracts, grid. One edit restyles every
   page.
3. **Is composed from the library** - a form is a `form`, a list is a
   `data-table`, not free text.
4. **Is interactive enough to judge** - a stencil can be dropped into, moved, and
   previewed at phone/desktop widths.
5. **Is honest about gaps** - a page the scan could not resolve says so *in the
   UI*, rather than pretending with an empty box.

**Structure coverage** = pages with at least one real element from the page ÷
total pages. This is the number every phase is judged by.

| | screens | with real content | coverage |
|---|---|---|---|
| where this started | 81 | 1 | **1%** |
| + stack detection, endpoints excluded | 41 | 25 | 61% |
| + widened extractor | 41 | **29** | **71%** |
| + rendered DOM (when a dev server is set) | covers the i18n remainder | | |

Target: **>= 90%** on the reference projects.

---

## Done

### P0 - Why extraction failed
`scan_page_cues()` only extracted cues for `.js/.jsx/.ts/.tsx` sources, so every
Django/Python route was skipped. That one line explained all 81 blank stencils.
It was not a template-resolution bug to chase per framework.

### P1 - Stencil from the stack that holds the pages
`scan_react_pages()` finds page components and the route each answers - from a
router table, or from path comparisons when the router is hand-rolled
(`if (currentPath === '/x')`), which Tensology's is. The cue extractor already
read JSX; it needed the scan pointed at the app.

### P1b - Widen the cue extractor
**A correction to my own report:** I claimed it missed `<h1>`, `<button>` and
`<a>`. It does not - it misses them only for single-character content, because of
a minimum-length guard. The genuine gaps were `<h4>`-`<h6>`, `<section>`,
`<table>` and `<nav>`; those are now read, table column headers become a `table`
node, every landmark renders, and a page that composes imports borrows their cues.

### P2 - Read the rendered DOM
`dom_cues.py` reads headings, fields, buttons, links, landmarks and table columns
from a rendered page. Bounded (one browser, a page budget, a hard timeout) and
fail-closed - no browser, no server, a slow page or a crash all mean **no cue**,
never a guessed one. A DOM cue carries `observed: True`: a rendered page is
evidence of what a user *sees*, not what the developer *intended*.
`enrich_cues_from_dom()` renders only pages whose source cue is thin.

### P2b - A dev-server URL so it has a target
Setting `planning_dev_server_url`, **default empty**. When set, the scan reads
thin pages from the running app; when empty it never launches a browser. There is
a field for it in Settings -> Initiative. Verified end to end against a served
page.

### P4 - The base template governs the render
The tokens applied but nothing else did: a link rendered a bare `<a>` with no
class, so it stayed browser-blue and underlined. The renderer already emits each
node's type as a class, so the fix was in `to_css()` - style `.node.heading`,
`.node.link a`, `.node.button button`, `.node.input input`, `.node.table` and the
field label, all from tokens. Verified by computed style.

### P4b - Endpoints are not pages
53 "pages" -> **14 pages + 40 endpoints** for Tensology. A Django page view names
a template; a view that does not answers with data, however it is spelled
(`identity()` returns a project-local `api_response(...)`). Endpoints are
reported, not drawn as blank boxes.

### P4c - Stack detection
`stack_detect.py` answers, before anything is stencilled, which stacks are present
and whether each serves pages or only data.

```
Tensology   -> Pages come from react (161 files). Also present: django.
               django: pages=16 data_only=46 serves_pages=False
DecisionsAI -> react, rust, pyqt present
```

### P2 (second) - Compose the extends chain
A page is base + page, so `_compose_template_cue` walks `{% extends %}` (depth
capped, cycle safe), composes outermost-first so the page wins, and merges. The
shell - header, nav, footer - is now part of the stencil.

### P5 - The project's own theme seeds the base template
`project_theme.py` reads the project's real decisions - CSS custom properties
first, Tailwind as fallback - maps names across aliases, and emits a base-template
entry. Fail-closed: fewer than two usable tokens means no derived theme, because a
wrong theme is worse than a generic one.

Measured on Tensology's frontend - their real theme:

```
color.accent #3b82f6   color.ink #1e293b   color.muted #64748b
color.line #e2e8f0     color.surface #ffffff  color.background #eff6ff
font.family 'Quicksand', system-ui, ...
```

It drives the render: link `rgb(59,130,246)`, background `#eff6ff`, font
Quicksand. `save_as_project_entry()` writes it into the project's own library.

---

## Remaining

### P6 - Make staleness visible
The wire still says "53 routes observed" while the classifier reports 14 + 40, and
screens carry "(stale)" labels and `# @stale` markers. A plan must not look current
when it is not: say when it was generated, what changed since, and offer a re-scan.

### P7 - Give a sitemap and a page distinct surfaces
The overview is a *map* and the pages are *stencils*, and they render identically
with the same affordances. Nothing tells the operator which they are looking at.
The map wants layout/navigation affordances; a page wants component affordances.

### P8 - Drag and drop that feels right
The library is a hidden panel of text rows. It should be a **visible strip**, with
a **thumbnail** per entry and a **grid overlay** on the target page showing where
a component will land. A page should be assemblable in under a minute.

### P9 - Production render and polish
The final artefact at desktop and phone widths, with real components and real
content - the thing you would show a client. Then: empty/loading/error states,
keyboard reach for every action, an a11y pass, and performance (an 80-page plan
opens without stalling).

---

## What is already in place (do not rebuild)

- Library, base template, pages, sitemap model, tickets as component diffs.
- **One plan**: the workspace's `flows` wire, which the canvas already renders.
- The plan persists into the project's planning folder.
- The wireframe renderer, with `baseCss` injection and contract-aware classes.
- Ticket generation as a component diff.
- Stack detection, endpoint classification, the rendered-DOM reader.

## Risks

- **Template resolution is per-framework.** React, Vue, Django, Rust, Qt and plain
  HTML all differ; the producer contract keeps this tractable, but it is not one
  fix.
- **Screenshots and the DOM are a fallback, not a replacement.** A DOM-derived
  layout is approximate and must stay marked `observed`.
- **The library is only as good as what it has.** If recognition finds a form and
  the library has no form entry, it must fall back rather than invent.
