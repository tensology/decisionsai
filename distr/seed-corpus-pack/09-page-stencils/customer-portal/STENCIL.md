# Stencil: Customer portal - SYNTHESIZED

Read the global `ui-ux-principles` skill first. This stencil adds commerce-specific requirements only.

Compile an approved `ScreenContract` with the `customer-portal` executable pack before model-authored changes.

## Commerce flow

- Make variant selection explicit before add-to-cart when variants affect fulfillment.
- Persist the cart across refresh and explain price revalidation.
- Keep checkout submission idempotent so repeated activation cannot double charge.
- Make guest and account paths explicit.
- Show the frozen order snapshot and totals after confirmation.

## Acceptance

- Given a product requires a variant, when none is selected, then add-to-cart remains unavailable with a field-level explanation.
- Given a cart survives refresh, when prices changed, then the updated values and reason are visible before checkout.
- Given checkout is submitted repeatedly, when the order is processed, then only one charge and order are created.
- Given checkout completes, when confirmation renders, then it shows the frozen order snapshot and totals.
- The feature is incomplete until the global Definition of Done - UI and applicable FRAC criteria pass.
