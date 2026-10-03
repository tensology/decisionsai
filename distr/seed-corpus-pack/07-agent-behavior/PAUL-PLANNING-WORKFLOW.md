# Paul's planning workflow — starting approach

Use this as the **default interaction pattern** for Plan mode and seed-pack planning. It is how Paul dictates and builds sense of a project. Prefer this order over inventing a parallel process.

## How Paul talks to the agent

- Mostly **dictation / mid-chat**: spoken or typed streams of intent, not ticket jargon.
- Expects the agent to **infer structure** from the journey, then ask only expensive questions.
- Speaks in **screens and flows first** ("login, cart, checkout…"), then fills FRAC and data.
- Wants the plan to stay a **rapid-sense hub**, not a dump of file paths.

## What belongs in a plan (hold together)

1. **Pages / wireframes** — stencil outlines per screen, browsable as carousel or sitemap
2. **Sitemap** — whole-set navigation map (Mermaid), not an error grid
3. **FRAC / requirements** — features, requirements, acceptance criteria
4. **ERD / data** — entities and relationships
5. **Front-end and back-end mapping** — for a given page: view (route), model (binds), business logic (requirement IDs)

Flow of work: **planning → wireframes → final production**.

## Order of instructions (agent starting point)

1. **Scan or outline screens** for the main journey (entry, success, empty, validation, failure).
2. **Show the set** — All pages carousel and/or Sitemap.
3. **FRAC the risky deltas**; reuse pack done-states for the rest.
4. **ERD** for entities touched by those screens (`bind=Entity.field`).
5. **Link each page** to view / model / logic annotations; do not invent payment/inventory topology.
6. **Build tasks** only when enough decisions exist (see Plan conversation rules).

## For a given page, surface three layers

| Layer | User sees |
|-------|-----------|
| Planning | Stencil summary, template, linked FR / binds |
| Preview | Rendered page outline inside the preview shell |
| Development | Route (view), binds (model), requirement IDs (logic) + jumps to Data / Requirements |

Full production render-in-template and complete V/M/BL wiring may land later; always keep the **structure and links** honest.

## Dictation cues → artifacts

| Paul says… | Prefer… |
|------------|---------|
| "pages / screens / journey" | Wire DSL screens + sitemap |
| "must / acceptance / done when" | FRAC / requirements |
| "data / tables / entities" | ERD |
| "build / implement / tasks" | Build tasks from linked sources |

## Do not

- Stall on chrome (fonts, spacing) before screens and FRAC exist.
- Leave the user staring at raw path dumps or "Invalid wireframe" walls — plain English + next step.
- Treat discussion as permission to rewrite artifacts until Paul asks to record/update.
