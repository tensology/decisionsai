# BuilderIO skills + Open Design reference assessment

Reference clones live beside DecisionsAI (same parent as `reference/ecc`, `reference/rtk`):

| Clone | Upstream | Path |
|-------|----------|------|
| BuilderIO skills | [BuilderIO/skills](https://github.com/BuilderIO/skills) | `../reference/builderio-skills` |
| Open Design | [nexu-io/open-design](https://github.com/nexu-io/open-design) | `../reference/open-design` |

Full path: `/Users/paul/development/TENSOLOGY/DECISIONS/reference/` (note: `development`, not `developement`).

Vendor slice in DecisionsAI: `plugins/visual-plan-pack/` (sync via `scripts/sync_visual_plan_pack.py`).

Bootstrap: `distr/core/visual_plan_pack.py` (mirrors `community_skills_pack.py`).

---

## What each upstream is for

### BuilderIO / Agent-Native skills

**Job:** Turn plans and diffs into **reviewable visual artifacts** — MDX plans with inline diagrams, canvas wireframes, PR recaps, status blocks.

**Key skills vendored into Decisions:**

| Skill | Use |
|-------|-----|
| `visual-plan` | Approve implementation plans before code; inline Mermaid/diagram blocks + UI canvas |
| `visual-recap` | Branch/PR shape before line-by-line review |
| `quick-recap` | Green/yellow/red completion convention |

**Not vendored (install ad hoc):** `efficient-fable`, `efficient-frontier`, `stay-within-limits`, `read-the-damn-docs` — overlap with ECC/Ponytail or are host-specific.

**Optional dependency:** [Agent-Native Plans app](https://github.com/BuilderIO/agent-native/) for hosted shareable plan links. Decisions Mermaid viewer covers the lightweight diagram case without it.

### Open Design

**Job:** Local-first **design studio** — prototypes, decks, HyperFrames, images, 150+ `DESIGN.md` systems, 261 plugins, MCP for coding agents.

**Surfaces:** Desktop app (macOS/Windows), `od` CLI, `od mcp install <agent>`.

**Diagram-related skills in upstream:** `hand-drawn-diagrams`, `d3-visualization`, `frame-flowchart-sticky`, deck templates with `arch-diagram` / `flow-diagram` blocks.

**Not vendored:** Full monorepo (~8k files). Reference clone + routing skill only.

---

## Mermaid viewer synergy

Decisions already ships a freestanding Mermaid viewer (`/diagram/`, `show_mermaid_diagram`, History, PNG/JPEG export).

| Layer | Tool | Best for |
|-------|------|----------|
| Fast technical chart in chat | Decisions Mermaid | ER, sequence, flowchart, state — agent-editable text, local history |
| Plan with mixed backend + UI | visual-plan + Mermaid | Architecture blocks → `show_mermaid_diagram`; screens → visual-plan canvas |
| Branded UI / deck / motion | Open Design Studio | CSS prototypes, PPTX, MP4 |
| Sketch / whiteboard feel | Open Design `hand-drawn-diagrams` | Excalidraw-style; not Mermaid syntax |

**Rule:** One fact, one surface. Do not render the same architecture twice in Mermaid and Open Design.

---

## Decisions integration (implemented)

| Piece | Location |
|-------|----------|
| Routing skills | `skills/decisions-visual-plan`, `skills/decisions-open-design` |
| Vendored BuilderIO skills | `plugins/visual-plan-pack/skills/` |
| Harness bootstrap | `distr/core/visual_plan_pack.py` + `harness_stack.py` |
| Workflow pre_chain merge | `capabilities_pack.merge_harness_pre_chain` |
| MCP catalog entry | `mcp_harness.collect_mcp_catalog` → `open_design` (manual install) |

### Setup commands

```bash
# Reference clones (already present; refresh)
cd ../reference/builderio-skills && git pull
cd ../reference/open-design && git pull

# Vendor BuilderIO skills into DecisionsAI
cd DecisionsAI && python3 scripts/sync_visual_plan_pack.py

# Project harness (Codex/Cursor skills)
bin/setup.py

# Open Design MCP (separate; app must be running)
cd ../reference/open-design
pnpm install   # first time only
od mcp install cursor
```

---

## Fit by Decisions surface

### Voice / chat agent

| Request | Route |
|---------|-------|
| "Open mermaid viewer" | `open_page` → `/diagram/` |
| "Show ER diagram for users table" | `show_mermaid_diagram` |
| "Plan this feature before we code" | Provision `visual-plan` skill; Mermaid for backend blocks |
| "Mock up the settings screen" | `decisions-open-design` — user runs Open Design |

### Workflows / tickets

Add to `pre_chain` when step mentions plan, architecture, diagram, prototype, or deck:

- `decisions-visual-plan`
- `decisions-mermaid-diagrams` (technical)
- `decisions-open-design` (UI artifact)

Loop preset opportunity: **Plan → Mermaid review → implement** using visual-plan + show_mermaid_diagram gate.

### IDE workers (Codex/Cursor)

Worker skill item: read `decisions-visual-plan` before large UI or architecture tickets.

---

## Gaps / next steps (optional)

1. **Agent tool** `open_visual_plan` — POST to Agent-Native Plans API when user wants hosted share links.
2. **Diagram export bridge** — Mermaid PNG → Open Design project asset (manual today).
3. **Tray menu** — "Open Design" next to Diagram viewer when `od` CLI detected on PATH.
4. **Workflow template** — visual-plan approval step before `send_to_project_cli`.

---

## License

- BuilderIO/skills: MIT (see upstream `LICENSE`)
- nexu-io/open-design: Apache 2.0 (see upstream `LICENSE`)
