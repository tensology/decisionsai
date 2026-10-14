# Stencil: CRUD list + detail - SYNTHESIZED

Read the global `ui-ux-principles` skill first. Its fixed CRUD flow controls shared behavior. This stencil adds resource-specific structure only.

Compile an approved `ScreenContract` with `python -m distr.core.ui_factory` and treat its manifest, generated Playwright spec, and allowed-file boundary as the implementation contract.

## Resource details

- Use route `/<resource>/$id` for a dedicated detail page when a record needs more than list context.
- Keep row actions in one accessible Actions column.
- Show only bulk actions that make sense for the selected records.
- Use sections, tabs, or nested tables for related entities only when the record is complex enough to need a full detail page.
- Remount or reset the shared form safely when the edited record id changes.

## Acceptance

- Add a Given / When / Then criterion for list-parameter contract behavior and a not-found criterion for detail routes.
- Use every applicable criterion from `../../03-done-states-frac/FRAC-TEMPLATE.md`.
- The feature is incomplete until the global Definition of Done - UI and applicable FRAC criteria pass.
