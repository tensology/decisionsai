# CRUD flows

## Flow map (default)

```
List ──[Add X]──▶ Form (modal/drawer/page) ──[Save X]──▶ List, new row highlighted, toast "X added"
List ──row name──▶ Detail ──[Edit]──▶ same Form prefilled ──[Save changes]──▶ Detail updated, toast "X updated"
List/Detail ──[Delete]──▶ Confirm dialog ──[Delete X]──▶ List without row, toast "X deleted" (+Undo if soft)
```

Write this map (adapted) before coding; count clicks for the main task and remove any that aren't needed.

## Create

- Entry: primary button top-right of the list header ("Add author"). In a child section, a secondary
  "Add book" in that section header.
- Form opens with focus in the first field. Required fields marked. Sensible defaults prefilled.
- Save button label = "Add author" (create) / "Save changes" (edit). Disabled + spinner during request.
- On success: close the form, insert the row in its sorted position (or top with "new" highlight),
  scroll it into view, highlight 2s, toast "Author added". If created from a parent detail, stay there.
- On failure: keep the form open with values, show the error inline; never lose input.

## Edit

- Same component and field order as create, prefilled; title "Edit author".
- Track dirty state; Save disabled until something changed (or enabled with a no-op), Cancel with
  discard confirm if dirty.
- On success: update the row/detail in place without a full reload; toast "Author updated".
- Concurrency (multi-user apps): if the record changed underneath, show "This author was changed by
  someone else. [Reload] [Overwrite]".

## Delete

Choose by reversibility and blast radius:

| Situation | Pattern |
|---|---|
| Cheap to restore, single item, no cascade | delete immediately + Undo toast (8–10s); actually delete after timeout (soft delete) |
| Irreversible, has children, or affects others | confirm dialog |
| Very high impact (workspace, many records) | confirm dialog + type the name to confirm |

Confirm dialog spec
- Title: "Delete author?" Body names the record and consequence: "‘Ursula Le Guin’ and her 4 books will
  be permanently deleted." State "This can't be undone." only if true.
- Buttons: Cancel (secondary, left, focused by default) · "Delete author" (red, right). Escape = Cancel.
- During request: confirm shows spinner "Deleting…"; on failure keep dialog open with error.
- After: remove the row, toast "Author deleted", move focus to the next row or the list heading
  (not lost to `<body>`). If the deleted item was open in detail, return to the list.
- Never use `window.confirm()` / `alert()`.

## Bulk actions

- Checkbox column (header checkbox = select page; offer "Select all 134" link). Selection count in an
  action bar that replaces the toolbar. Bulk delete always confirms with the count ("Delete 12 authors?").
- Partial failures reported per item ("10 deleted, 2 failed · View").

## Optimistic updates

Use optimistic UI when: low risk, high frequency, easy to revert (toggle favourite, rename, reorder,
mark read). Apply instantly, send request, on failure revert and show an inline error with Retry.
Do not use optimistic UI for: creates that need server ids/validation, deletes with cascades, payments,
permission changes, anything sending email/messages.

## Double-submit and idempotency

Disable the triggering control while in flight, ignore repeated Enter, and make handlers idempotent
(client request id). Paul has hit duplicate-record bugs from double requests.

## Data integrity in demos

In-memory or seed data: realistic for the domain, internally consistent (book years after author birth,
counts match), stable ids, no "Lorem ipsum" or "Test 1". Keep fake data in one clearly marked block.

## People and names (from `ui-crud-discipline`)

- Display a person as `First Last` (`first_name last_name`). Email is secondary, or a fallback only
  when the full name is missing.
- First name before Last name in forms and columns unless the product targets a locale with a
  different convention. One Name / Full name column when users identify or select people.
- Completion check: search for last-name-first ordering and email-first identity displays.

## Operational admin scope (from `ui-crud-discipline`)

- Expose only records administrators routinely manage. Hide sessions, tokens, reset requests, audit
  internals, join tables and other implementation records unless an identified workflow needs them.
- Group non-trivial forms into task-oriented fieldsets: identity and primary fields first, operational
  fields next, advanced or machine-owned data later, read-only history last.
- Table cells read-only; permitted record actions in one accessible Actions column.
- No raw JSON, internal IDs, tokens, hashes or provider responses in the primary editing flow; put
  needed diagnostics in a read-only advanced or history area.
- Irreversible operations keep the exact record, scope, consequence, permission, progress and audit
  outcome visible.

## Email and notifications (from `ui-crud-discipline`)

- Route application email through one delivery utility and editable templates with stable event keys;
  keep plain-text and HTML bodies.
- Seed templates only for real product events.
- Use the same event definition for in-app and email notifications when both channels exist.
- Delivery logs are read-only; validate template syntax before saving; redact secure links and secrets.

## Admin (Django Unfold) specifics

- Expose only models admins routinely manage; hide tokens, sessions, join tables, audit internals.
- `list_display` = identity column first, 3–6 useful fields, status badge; `search_fields`, `list_filter`,
  `ordering` set explicitly; `list_per_page` 25–50.
- Fieldsets/tabs in task order; read-only history last; raw JSON and ids only in an advanced section.
- Use Unfold's built-in actions, filters, and `@display(label=…)` badges instead of custom templates.
- Protected records: read-only in UI and enforced in `has_change_permission` / API.
