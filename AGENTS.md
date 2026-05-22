# Codex Working Rules

## UI/UX Discipline

Do not invent UI just because data exists.

Every visible field, column, badge, tab, button, callout, or panel must pass these checks before implementation:

1. User job: what decision or action does this help the user make right now?
2. Source truth: what exact backend or domain state does this represent?
3. Non-duplication: is this same meaning already shown somewhere else in the same view?

If any answer is unclear, do not add the UI.

Prefer the dumb obvious version:

- one status, not multiple labels for the same operational state
- one primary object per row
- the main object column gets most of the width and must remain readable
- secondary fields must be short, useful, and scannable
- no metadata columns unless they change the user's next action
- no decorative callouts
- no invented terminology
- no workflow jargon on the main work surface
- no implementation details unless the user is explicitly debugging implementation

Before implementing UI, state the plain-English sentence:

> I am showing X so the user can Y.

If that sentence is weak, remove the UI.

When changing an existing UI, simplify first. If two UI elements represent the same underlying state, collapse them into one.

## Consistency Rule

The same user action must use the same interaction pattern everywhere it appears.

If local, Jira, Trello, WhatsApp, workflows, or projects support the same job, do not build separate UI paths unless the backend genuinely cannot perform the same action. Use one shared modal, one shared label, one shared field meaning, and one shared success/error behavior.

When a difference is unavoidable, make the limitation explicit in the UI at the point of action. Do not silently fall back to a hidden default such as the first lane, default project, or backlog column.
