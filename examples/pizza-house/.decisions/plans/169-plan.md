# Plan - Ticket #169 Validate accessibility, responsive layout, and ordering links

Planning step: workflow 369, run 98, step 1926. This plan is the ticket
attachment for the validation slice and should be used before any implementation
changes. The companion ticket attachment target is
`/Users/paul/.decisions/workspaces/tickets/169/plan.md`.

## Scope

Validate the current Ember & Crust Pizza House static site without adding new
features. The work covers the existing three-file site only: `index.html`,
`styles.css`, and `app.js`.

The implementation step should fix only evidence-backed defects found during
validation. If validation passes, the expected output is a written evidence
summary rather than source changes.

## Implementation slices

1. Accessibility validation
   - Inspect `index.html` and generated menu/cart markup from `app.js`.
   - Verify skip-link behavior, landmark structure, heading order, form labels, button names, image `alt` text, keyboard focusability, `aria-expanded` on the mobile nav toggle, and live/status regions for subtotal and order feedback.
   - Run keyboard-only checks for nav, menu add buttons, quantity controls, radio buttons, form fields, and submit.
   - Fix only missing names, labels, roles, or focus states proven by the audit.

2. Responsive layout validation
   - Inspect `styles.css` media queries and layout constraints.
   - Validate widths at 320px, 375px, 414px, 700px, 800px, 1100px, and 1440px.
   - Confirm no horizontal overflow, header/nav remains usable, pizza cards collapse cleanly, checkout controls fit, and text does not overlap or clip.
   - Fix only the specific CSS declaration causing confirmed overflow or breakpoint failure.

3. Ordering link and mailto validation
   - Test the current cart flow in `app.js`: add pizzas, increment/decrement quantities, prevent empty-cart submission, calculate subtotal, and submit valid customer details.
   - Confirm the generated `mailto:hello@emberandcrust.co.za` URL includes an
     encoded subject, customer name, email, order type, notes, item lines, and
     subtotal.
   - Verify the fallback contact mailto link in `index.html` resolves to the same address.
   - Fix only malformed encoding, missing order fields, dead handlers, or incorrect totals found by testing.

## Affected files

| File | Planned use | Change rule |
| --- | --- | --- |
| `index.html` | Validate landmarks, form labels, fallback mailto link, and static copy | Edit only for proven accessibility or broken-link defects |
| `styles.css` | Validate breakpoint behavior, overflow, focus visibility, and layout fitting | Edit only the smallest failing rule |
| `app.js` | Validate generated menu/cart markup, subtotal updates, and mailto body construction | Edit only the failing function or string construction |

## Tests and evidence

- Static checks: grep or direct file inspection for skip link target, `aria-controls`, `aria-expanded`, `aria-live`, `role="status"`, form labels, image `alt` text, and mailto addresses.
- Browser checks: run the site with `python3 -m http.server 4173` and inspect the listed responsive viewports.
- Accessibility checks: keyboard-only traversal and screen-reader-oriented semantic inspection for names, roles, values, focus order, and live/status updates.
- Ordering checks: add at least three pizza quantities across multiple items, submit the form, and capture the resulting mailto URL shape without sending email.
- Regression checks: run any available project lint/test command; if none exists, record that this static site has no package-managed test harness.

## Acceptance criteria

- Skip link reaches `#main-content`, and keyboard focus is visible and logical.
- Header, main content, menu section, story section, checkout form, and footer/order area are navigable with meaningful names.
- Interactive controls have accessible names and preserve state where applicable.
- Layout has no horizontal overflow from 320px through 1440px.
- Mobile nav opens and closes without trapping focus or hiding reachable links.
- Pizza quantities and subtotal update correctly.
- Empty-cart submit produces inline feedback and does not open mailto.
- Valid-cart submit opens a mailto URL with encoded customer details, line items, and subtotal.
- Fallback contact link points to `hello@emberandcrust.co.za`.

## Rollback notes

This ticket should produce minimal, reversible changes only if validation finds a
defect. Roll back by reverting the touched declaration or function block in the
affected file. If no defects are found, no source rollback is needed because the
implementation step should only add validation evidence.
