# Plan - Ticket #168 Build responsive menu and restaurant landing page

Planning step: workflow 369, run 99, step 1926. This file is the ticket-local
`plan.md` attachment mirror for Ticket #168 and should be read before
implementation begins. The companion ticket workspace target is
`/Users/paul/.decisions/workspaces/tickets/168/plan.md`.

## Scope

Build the static Ember & Crust restaurant landing page into a responsive,
accessible customer journey without adding a backend, payment provider,
analytics, blog, or unrelated content system.

The current site already has the expected shape: hero, data-driven menu,
story/restaurant copy, checkout form, mobile navigation, cart quantities,
basic validation, and a `mailto:` order handoff. This plan keeps that
architecture and closes only the ticket-specific gaps.

## Implementation slices

| Slice | Goal | Planned work | Evidence before moving on |
| --- | --- | --- | --- |
| 1. Landing-page polish | Make the page feel deployable and shareable | Add restaurant-focused metadata in `index.html`; add/point to a real `favicon.svg`; keep the existing visual direction from Ticket #167 | Head metadata visible in file read; favicon path resolves locally |
| 2. Maintainable menu content | Keep menu changes data-only | Preserve the pizza object array in `app.js`; only add/adjust pizza data or image paths there; avoid duplicating menu markup in HTML | Menu still renders from the array; changing one item updates rendered card/cart text |
| 3. Responsive layout pass | Verify the landing page and menu at practical widths | Test 600, 750, 840, and 1200 px; patch `styles.css` only if there is horizontal overflow, clipped text, broken nav, or cramped checkout layout | Screenshots or manual notes for all four widths; no horizontal scroll |
| 4. Order journey validation | Confirm the customer can move from item selection to quantity changes to order submission | Keep empty-cart guard, required contact fields, delivery/collection radio group, cart subtotal, and `mailto:` payload; add only a small in-page confirmation if mail client launch has no visible feedback | Empty cart blocks submission; valid cart builds a mailto body containing items, quantities, subtotal, customer details, and order type |
| 5. Accessibility check | Preserve keyboard and screen-reader affordances | Verify skip link, mobile nav `aria-expanded`, distinct quantity button labels, form labels, and `aria-live` cart/status updates | Keyboard can reach nav/menu/checkout; DOM inspection shows labels and live regions intact |

## Affected files

| File | Planned use | Change rule |
| --- | --- | --- |
| `index.html` | Metadata, favicon link, and only necessary semantic fixes | Do not restructure the body unless validation exposes a specific accessibility defect |
| `styles.css` | Responsive fixes for nav, menu grid, hero, checkout, and status messages | Minimal breakpoint or spacing patches only; preserve Ticket #167 warm rustic tokens |
| `app.js` | Menu data, cart rendering, quantity handlers, validation, and mailto payload | Keep the current client-only cart model; no network calls or persistence |
| `favicon.svg` | New static brand asset if missing | Small local asset only |
| `images/*` | Optional local menu images if implementation replaces inline placeholders | No external hotlinks; keep fallback behavior if images are introduced |

## Tests and validation

- Open the page locally and check widths `600`, `750`, `840`, and `1200`.
- Confirm no horizontal overflow and no overlapping text or controls.
- Use keyboard only to reach the skip link, navigation, add/remove controls, and checkout form.
- Add at least two pizzas, increment/decrement quantities, and verify subtotal changes correctly.
- Submit an empty cart and confirm submission is blocked with visible feedback.
- Submit a valid order and verify the generated `mailto:` includes ordered items, quantities, subtotal, order type, name, email, phone, and notes/address.
- Inspect rendered menu cards to confirm images retain `alt` text and lazy loading where present.
- Run any available lightweight static checks for this repo; if no test runner exists, record manual browser evidence instead of inventing a test command.

## Rollback notes

- Revert metadata/favicon changes by restoring `index.html` head edits and removing `favicon.svg`.
- Revert menu-data changes by restoring the previous pizza array entries in `app.js`.
- Revert responsive patches by removing the specific `styles.css` breakpoint or rule block added for the failing viewport.
- Revert order-flow confirmation by removing the new status/toast branch while leaving the existing `mailto:` submit handler intact.
- Because this ticket remains static and client-only, rollback should not require data migration, server changes, or credential cleanup.

## Out of scope

- Payment collection, delivery pricing, availability scheduling, backend order storage, admin menu editing, analytics, marketing blog pages, and third-party form services.
- Broad visual redesign beyond preserving the established Ember & Crust warm rustic direction.
