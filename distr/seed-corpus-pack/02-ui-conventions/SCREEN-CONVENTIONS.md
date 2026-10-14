# Global Spec: Interface conventions

The source of truth is the global `ui-ux-principles` skill:

- `UX Principles` controls purpose, flow, CRUD structure, navigation, feedback, state, and work protection.
- `UI Principles` controls hierarchy, duplication, visual restraint, layout, content, consistency, accessibility, and responsiveness.
- `Taste Memory`, `Definition of Done - UI`, and `Changelog` keep the principles current.

Read the current global skill before planning, building, reviewing, or testing an interface. Product, framework, stencil, and aesthetic guidance cannot override it unless the user explicitly requests an exception.

## Architecture invariants

- Use one list-parameter shape: `_page`, `_limit`, `_sort`, `_order`, `q`, and filters.
- Keep a repository seam such as `list/getOne/create/update/remove` so storage can change without rewriting pages.

## Design direction

- Choose one product-appropriate aesthetic after applying the global principles.
- Keep project-specific aesthetic guidance subordinate to functional necessity and the existing token system.

## Vision to web tasks

Vision2Web parquet splits are acceptance-style tasks from screenshot or specification to implementation. Use them for evaluation, not as production assets. Archives are not in the pack by default.
