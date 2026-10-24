# CRUD management surfaces

Read this when designing or revising a page that lists and manages records.

## Default structure

- Treat the route-level page title as the only page title. Do not repeat it inside the content with a card title and explanatory subtitle.
- Treat the selected tab as the title of its content. Do not repeat the tab label as a heading underneath it.
- Put the primary create action at the top right of the content area.
- Show records in a real table when users need to compare consistent fields across rows.
- Keep table cells read-only. Display roles, permissions, and status as plain text or compact badges, not live inputs or checkboxes.
- Put row actions in a dedicated right-aligned Actions column. Use explicit accessible labels on icon-only controls.
- Use a focused Add or Edit modal for bounded forms. Keep the same fields, ordering, validation, and terminology in both modes.
- Require a confirmation dialog for destructive deletion and name the exact record and consequence.
- Focus Cancel when a destructive dialog opens. Escape cancels; Enter or Delete confirms; closing restores focus to the triggering action.

## Human identity

- Display people as first name then last name. Use email only as secondary text or as the fallback when the full name is missing.
- Put First name before Last name in forms and default ordering. Prefer one Name column over separate name-part columns in lists.

## Context and operational scope

- A right-side panel must be scoped to the current record or task. Omit it when there is no real contextual content; never fill it with generic links, duplicated navigation, fake activity, placeholder people, or hard-coded notification counts.
- In admin navigation, expose only records that administrators routinely manage. Hide sessions, tokens, reset requests, join tables, and audit internals unless a defined operational workflow requires them.
- Give every non-trivial admin form task-oriented fieldsets: primary identity first, operational details next, advanced data later, and read-only history last.
- Route real notification email through one delivery utility and editable event-keyed templates. Do not seed templates for events that have no caller.

## Relationships and permissions

- For many-to-many assignment with more than a few choices, use a searchable horizontal transfer control with Available and Selected lists, move controls, and select-all/remove-all shortcuts.
- Distinguish role, permission, assignment, and active status. Do not collapse them into one ambiguous control or label.
- Make protected records visibly non-editable and enforce the protection in the API, not only in the interface.

## Required states

Include loading, empty, saving, validation error, deletion-in-progress, success feedback, keyboard focus, and responsive overflow. A completed state change must update the table without requiring a manual page refresh.
