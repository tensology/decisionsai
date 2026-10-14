# Stencil: Admin screen - SYNTHESIZED

Read the global `ui-ux-principles` skill first. This stencil adds admin-specific structure only.

Compile an approved `ScreenContract` with the `admin-screen` executable pack before model-authored changes.

## Admin structure

- Group persistent navigation by product domain.
- Use breadcrumbs on nested pages only when they aid orientation, and omit the final item when it repeats the visible page title.
- Use the CRUD stencil for entity management and preserve selection in the URL for master-detail workflows.
- For operations tools, make scope, permission, consequence, and audit evidence explicit.

## Acceptance

- Given an authenticated user opens an admin screen, when permissions are evaluated, then authorized navigation and actions are visible while unauthorized actions are absent.
- Given an administrative change succeeds, when feedback appears, then it names what changed without exposing internal identifiers or secrets.
- The feature is incomplete until the global Definition of Done - UI and applicable FRAC criteria pass.
