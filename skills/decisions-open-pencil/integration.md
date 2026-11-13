# OpenPencil integration — slice 1: pin + read-side bridge

Approved by Paul on 12 Nov 2026 (~23:18 SAST). Scope: reference pin, read-only scene-graph reader,
gap report, page association API. Out of scope: Vue canvas/editor embed, desktop app install,
Wireframe DSL translation, live ticket creation.

## Version pin
| | |
|---|---|
| Upstream | https://github.com/open-pencil/open-pencil (MIT) |
| Tag / SHA | `v0.17.0-beta.4` / `9e43b6d3b26cea2badcf3707489da579696b9857` (released 2026-11-11T17:48:13Z) |
| CLI | `@open-pencil/cli@0.17.0-beta.4` (exact version; npm `latest` is 0.16.0, `next` floats) |
| Latest non-beta | `v0.16.0` / `ffe2fc57…` (2026-10-14) — six weeks behind a fast-moving format |
| Reference clone | `DECISIONS/reference/open-pencil` (sibling of `open-design`, outside the DecisionsAI git root) |
| CLI install | `DECISIONS/reference/open-pencil-cli` (`package.json` exact pin + lockfile) |
| Source of truth | `distr/core/open_pencil/pin.py`; `check_pin()` verifies CLI `--version`, installed package version, and clone HEAD SHA |

## Integration surface
**CLI (used), file mode only, never the running app:**
- `openpencil --version` — pin check.
- `openpencil info <file> --json` — page count, total nodes, node type counts, fonts.
- `openpencil eval <file> --stdin --json` with `distr/core/open_pencil/inspect_scene.js` — read-only
  Figma-Plugin-API script (no `--write`/`--output`; file sha256 checked before/after). Uses
  `figma.root.children`, `findAll`, `componentPropertyDefinitions`, `boundVariables`, `mainComponent`,
  `figma.variables.getLocalVariables()`, and OpenPencil's `openpencil.getBehaviour()` / `getStatus()`, plus `SLOT` node `limitViolations`.

Also available at the pin but not used yet: `tree`, `pages`, `find`, `query` (XPath), `node`, `lint`, `variables`, `export`, `convert`.

**MCP (not used in this slice):** `@open-pencil/mcp@0.17.0-beta.4` (`openpencil-mcp` stdio / `openpencil-mcp-http`)
proxies to the *running desktop app* (`list_pages`, `get_page_tree`, `list_components`, `get_bindings`, …).
Decisions does not install the app, so headless CLI file mode is the cleaner official read path. The MCP
binaries ship inside the pinned CLI install if a later slice needs live-app reads.

**Telemetry:** none in the CLI at this pin; the bridge sets `OPENPENCIL_TELEMETRY=0`, `DO_NOT_TRACK=1` anyway.

## Decisions code
- `distr/core/open_pencil/pin.py` — pin constants, `check_pin()`, `require_pin()`, error types.
- `distr/core/open_pencil/reader.py` — `inspect_document(path, max_depth=8, max_nodes=5000)`.
- `distr/core/open_pencil/inspect_scene.js` — the eval script.
- `distr/core/open_pencil/gaps.py` — `find_gaps(inspection, tickets=None)`.
- `distr/core/open_pencil/associations.py` — page ↔ file links in `~/.decisions/open_pencil/associations.json`.
- `distr/core/open_pencil/tickets.py` — `draft_ticket_specs()` (dry run), `create_tickets_from_gaps()` raises `NotImplementedError`.
- `distr/gui/web/routes/development/open_pencil.py` — HTTP boundary (registered in `routes/development/__init__.py`).
- `distr/core/plugins/paths.py` — `open_pencil_reference_dir()`, `open_pencil_cli_dir()`.
- `skills/decisions-open-pencil/SKILL.md`, `tests/core/test_open_pencil_bridge.py`.

## Inspection shape
```
{ source: {path, format: fig|pen, sha256, bytes, inspected_at},
  reader: {tool, cli_version, pin_tag, pin_sha, commands, mode},
  summary: {pages, total_nodes, node_types, fonts, top_level_frames, components, component_sets,
            slots, instances, bound_layers, variables, layer_tree_truncated},
  pages: [{id, name, topLevelCount, layers: [{id, name, type, bound?, childCount?, children?}]}],
  frames: [{id, name, page, width, height, status}],          # FRAME directly on a page or in a SECTION
  components: [{id, name, type, page, parentType, isVariant, description,
                properties: [{key, type: SLOT|VARIANT|TEXT|BOOLEAN|INSTANCE_SWAP, defaultValue,
                              variantOptions, description, slotSettings}], behaviour, status}],
  slots: [{id, name, page, childCount, ownerId, ownerName, ownerType, limitViolations}],
  instances: [{id, name, page, mainComponentId, mainComponentName}],
  bindings: [{id, name, type, page, fields}],
  text_in_components: [...], variables: {count, collections, names} | null, limits }
```

## Gap report
`untracked_components`, `untracked_frames` (no ticket title/description contains the node name;
with no tickets everything is untracked), `empty_slots` (`gap` for instance slots, `info` for template
defaults), `slot_limit_violations`, `unbound_behaviour_parts`, `unbound_text_in_components`
(heuristic; suppressed with an explicit note when the file defines no variables), `counts`.

## HTTP (all `require_internal_token_dependency`)
| Method | Path | Body |
|---|---|---|
| GET | `/workflows/studio/open-pencil/status` | — |
| POST | `/workflows/studio/plan-workspaces/{id}/open-pencil/inspect` | `{path, board_id?, max_depth?}` |
| GET | `/workflows/studio/plan-workspaces/{id}/open-pencil/links` | — |
| PUT | `/workflows/studio/plan-workspaces/{id}/open-pencil/links` | `{page_key, path, note?}` |
| POST | `/workflows/studio/plan-workspaces/{id}/open-pencil/links/inspect` | `{page_key, board_id?, max_depth?}` |
| DELETE | `/workflows/studio/plan-workspaces/{id}/open-pencil/links?page_key=` | — |
`board_id` reads that board's ticket titles/descriptions read-only for matching. Responses include
`ticket_drafts` (dry run) and `ticket_generation: "deferred…"`. 422 invalid file, 503 CLI/pin failure.
Live only after the app's next normal restart.

## Planning UI hook (deferred)
Planning static files (`static/development/planning/*`) were being edited by another worker, so no UI
was added. Next pass: in the Interfaces page panel, a "Link OpenPencil file" field → `PUT …/links`
with the page key, a "Inspect" button → `POST …/links/inspect`, render `summary` + `gaps.counts` and
lists. No canvas embed.

## Next slices
1. Planning UI affordance (above). 2. Approve ticket generation: map `draft_ticket_specs` into the
kanban/workflow pipeline with `external_source="open-pencil"` + `external_id` idempotence. 3. Optional
live-app reads via `@open-pencil/mcp`. 4. Later roadmap: editor embed.
