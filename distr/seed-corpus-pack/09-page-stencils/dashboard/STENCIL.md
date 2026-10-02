# Stencil: Dashboard — SYNTHESIZED

Refs: open-dashboard chart page + awesome-design enterprise.

## Layout
- App shell: sidebar or top nav + content.
- Top row: 3–4 stat cards (label, value, delta, spark optional).
- Main: 1–2 charts + optional table below.
- Filters (date range / channel) URL-synced when possible.

## Components
- StatCard, Area/Bar/Pie charts, DataTable snippet, empty-state, skeleton loaders.

## Validation / AC
- [ ] Loading skeletons; empty “no data” state
- [ ] Numbers formatted; timezone labeled if relevant
- [ ] Filter changes refetch; no silent stale cache
- [ ] Premium gate + enterprise/clean aesthetic
