# Stencil: Admin screen — SYNTHESIZED

Refs: open-dashboard app shell + Merrypak Django Unfold habits translated to React admin.

## Layout
- Persistent shell (nav grouped by domain: Catalog, Orders, Customers, Settings).
- Breadcrumbs on nested pages.
- Dense but readable (enterprise aesthetic); data > decoration.

## Screen types to pick
- CRUD list/detail (compose `crud-list-detail`)
- Master-detail (orders)
- Settings forms
- Ops tools (reindex, sync) with danger confirm

## Validation / AC
- [ ] Auth-gated; role-aware nav if roles exist
- [ ] Destructive actions confirmed
- [ ] Audit-friendly toasts (what changed)
- [ ] Matches open-dashboard invariants
- [ ] Premium gate with **enterprise/clean/shadcn** family (not playful marketing)
