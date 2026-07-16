# Plan - Ticket #176 Add reusable Pizza House menu validation

Planning step: workflow 369, run 103, step 1926. This file is the ticket-specific
`plan.md` for Ticket #176. The ticket attachment mirror is
`.decisions/tickets/176/plan.md`.

## Scope

Add a small, reusable, dependency-free validator for the existing Ember & Crust
Pizza House menu data. The validator should catch duplicate pizza IDs, invalid
prices, and missing names or descriptions, then be covered by Node built-in
tests. Current customer-facing UI behavior must stay intact.

## Implementation slices

| Slice | Goal | Planned work | Evidence before moving on |
| --- | --- | --- | --- |
| 1. Locate menu data | Confirm the existing source of truth | Use the existing `const pizzas = [...]` data in `app.js`; do not duplicate the menu in HTML or introduce a data service | File read shows the validator targets the current pizza object shape |
| 2. Reusable validator | Add dependency-free validation logic | Create `src/menu-validation.mjs` exporting a validation function and stable error labels for duplicate IDs, invalid prices, and missing required text | Unit tests can import the validator without browser globals or third-party packages |
| 3. Preserve UI behavior | Keep the current static menu and cart flow working | If `app.js` imports/uses the validator, keep rendering, quantity changes, subtotal, empty-cart feedback, and `mailto:` order handoff unchanged | Existing app code path still initializes from the same pizza array and cart IDs |
| 4. Node built-in tests | Cover the ticket acceptance paths | Add `node:test` tests for duplicate IDs, non-positive/non-finite/non-number prices, and blank/missing `name` or `description`; include a smoke test for the real menu data when practical | Exact Node test command passes locally |
| 5. Evidence capture | Report only after self-test | Run the exact test command and record output summary in the next workflow step | Test command and pass/fail state are available for ticket evidence |

## Affected files

| File | Planned use | Change rule |
| --- | --- | --- |
| `src/menu-validation.mjs` | New reusable validator module | No runtime dependencies; no DOM access; throw or return stable validation failures that tests can assert |
| `app.js` | Existing menu source and optional validator integration | Minimal change only; keep current UI behavior and menu rendering intact |
| `test/duplicate-pizza-id.test.mjs` | Node built-in regression test for duplicate IDs | Use `node:test` and `node:assert/strict` only |
| `test/invalid-price.test.mjs` | Node built-in regression test for invalid prices | Cover zero/negative/non-finite/non-number prices |
| `test/missing-required-text.test.mjs` | Node built-in regression test for missing or blank name/description | Cover missing field and whitespace-only field cases; optionally validate the real menu data |
| `plan.md` | Workflow planning artifact | This ticket #176 plan replaces the stale ticket #168 plan |
| `.decisions/tickets/176/plan.md` | Ticket attachment mirror | Keep content aligned with root `plan.md` for DecisionsAI evidence |

## Tests

Run the exact built-in Node test command before reporting implementation
complete:

```bash
node --test test/*.test.mjs
```

If the implementation step wires validation into `app.js`, also run a quick
manual browser smoke check or static file inspection confirming the menu still
renders from the same pizza entries and cart IDs.

## Rollback notes

- Remove `src/menu-validation.mjs` and the three `test/*.test.mjs` files to
  fully roll back the validation feature.
- If `app.js` imports or calls the validator, remove only that import/call and
  restore the direct `const pizzas = [...]` flow.
- No data migration, network credentials, package installation, or build-system
  cleanup should be required because the slice is dependency-free and static.

## Out of scope

- UI redesign, menu copy rewrites, checkout behavior changes, backend order
  storage, payment handling, package manager setup, and broad refactors.
