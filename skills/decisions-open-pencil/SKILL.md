---
name: decisions-open-pencil
description: Read an OpenPencil (.fig/.pen) scene graph from Decisions planning — pages, frames, components, slots, bindings — and surface gaps. Pinned CLI, read-only, no editor embed. Not Open Design.
---

# Decisions + OpenPencil (read-side bridge)

**OpenPencil** ([open-pencil/open-pencil](https://github.com/open-pencil/open-pencil), MIT) is a Figma-style
editor with a headless CLI. It is a different project from **Open Design** (`decisions-open-design`,
nexu-io/open-design); do not mix the two references.

## Pin
- Tag `v0.17.0-beta.4`, commit `9e43b6d3b26cea2badcf3707489da579696b9857` (2026-11-11).
- CLI `@open-pencil/cli@0.17.0-beta.4`, exact. Never use npm `latest` (0.16.0) or `next`.
- Reference clone: `../reference/open-pencil` (`open_pencil_reference_dir()`); CLI install:
  `../reference/open-pencil-cli` (`open_pencil_cli_dir()`). Pin record: `PIN.md` there.
- Do not vendor the monorepo into DecisionsAI. Do not install the desktop app.

## Read it
```python
from distr.core.open_pencil import inspect_document, find_gaps, draft_ticket_specs
inspection = inspect_document("/abs/path/screens.fig")   # or .pen; local paths only
gaps = find_gaps(inspection, tickets=[{"id": 1, "title": "...", "description": "..."}])
```
HTTP (internal token): `GET /workflows/studio/open-pencil/status`,
`POST /workflows/studio/plan-workspaces/{id}/open-pencil/inspect`,
`PUT|GET|DELETE …/open-pencil/links`, `POST …/open-pencil/links/inspect`.

## Rules
- File mode only. The bridge never connects to a running OpenPencil app and never passes `--write`/`-o`.
- Fails closed: missing CLI, pin mismatch, LFS pointer, non-local path, or a file that changes mid-read.
- Gaps are claimed only from fields the file has; with no variables, no binding gaps are claimed.
- Ticket generation is deferred: `draft_ticket_specs` is dry-run; `create_tickets_from_gaps` raises.
- No Vue canvas / editor embed in planning (later roadmap item).
- No Wireframe DSL translation in this slice.

Docs: `integration.md` (this folder; also `docs/open-pencil-integration.md`, which docs/* gitignore hides).
