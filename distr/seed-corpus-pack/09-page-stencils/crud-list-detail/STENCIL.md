# Stencil: CRUD list + detail — SYNTHESIZED

Refs: open-dashboard CRUD table, Detail/Show, Form dialog; backends CONTRACT.md.

## List page
- Table or card grid with server `q`, sort, page, filters.
- Row actions: view, edit, delete (confirm).
- Primary “Create” CTA.
- Bulk select optional.

## Detail page
- Route `/<resource>/$id`.
- Header: title, status chip, actions (edit/delete).
- Body: definition list / sections; related entities as tabs or nested tables (e.g. order → line items).

## Form
- Create/edit shared schema validation (zod or equivalent).
- Keyed remount on edit id change.
- Toast success/error.

## Validation / AC
- [ ] CONTRACT list params honored
- [ ] 404 detail state
- [ ] Delete confirm + optimistic or safe refresh
- [ ] Field errors mapped from API
- [ ] Premium gate green
