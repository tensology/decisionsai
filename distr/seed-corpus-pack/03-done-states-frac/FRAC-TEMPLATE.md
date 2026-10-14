# FRAC template - SYNTHESIZED

Combine: ecc Plan-PRD pattern, mattpocock to-prd, writing-plans bite-sized tasks, Merrypak planning numbers (23-30), verification-before-completion.

```markdown
# FRAC: <Feature name>

## F - Feature
One sentence: who + what capability + why.
Out of scope: bullet list.

## R - Requirements
Numbered, testable, domain vocabulary from `01-entity-domain/CONTEXT.md`.
1. ...
Non-functional: performance, security, i18n, a11y as needed.

## AC - Acceptance Criteria
Write every behavioral criterion in Given / When / Then form. Each AC maps to a verification command or QA tier.

For every user-facing feature, read the current global `ui-ux-principles` skill, especially `Design Principles`, `UX Principles`, `UI Principles`, and `Definition of Done - UI`. Include every applicable criterion below. Do not replace these criteria with a checklist, and do not mark the feature complete until they pass.

- AC1: Given ..., when ..., then ...
- AC2: Given ..., when ..., then ...

## Done-state
What "done" means for this flow (see DONE-STATES.md). Not "code merged" alone.

## Verify loop
1. Write or adjust a failing test or QA script for each AC.
2. Implement the minimal change.
3. Run the verification command fresh.
4. Record the command, exit status, and key output in the ship note.
5. If verification fails, fix or reopen the FRAC. Never claim completion without evidence.

## Seams / modules
Highest-level test seam; files to touch (from writing-plans).

## Links
PRD path, plan path, tickets, related ADR, and the global `ui-ux-principles` skill when UI is in scope.
```

## Required UX and UI acceptance criteria templates

Copy and specialize every applicable criterion. Replace bracketed placeholders with feature-specific language.

### Purpose, hierarchy, and removal

- Given a screen is defined, when its purpose is reviewed, then it performs one job and does not merge list, create, edit, and detail views.
- Given a CRUD screen is implemented, when its controls are inspected, then forms are not embedded in lists and edit-in-place or expanding-row forms are absent unless explicitly required.
- Given a user completes an action, when the result appears, then the user's location, available actions, and outcome feedback are clear.
- Given a task can be completed safely in fewer steps, when the flow is reviewed, then unnecessary clicks are removed.
- Given a tab, page, or modal title is visible, when its content renders, then the same label is not repeated in headings, breadcrumbs, cards, body copy, or button text.
- Given information and actions render in a view, when the screen is inspected, then counts, summaries, subtitles, and primary actions are not duplicated.
- Given a screen is complete, when every visual element is reviewed, then generic heroes, gratuitous icons, gradient cards, stock illustrations, emoji, and welcome copy are absent unless explicitly required.
- Given content is grouped, when containment is inspected, then it uses one wrapper level unless another is structurally necessary.
- Given labels and supporting text render, when copy is reviewed, then it is short and functional without placeholder marketing or repeated helper text.
- Given the requirements do not request analytics, when the view renders, then unsolicited dashboards, statistics widgets, and charts are absent.
- Given whitespace is visible, when spacing is inspected, then it comes from design tokens rather than accumulated padding.
- Given the screen is ready to ship, when the global Definition of Done - UI is run, then every applicable check passes before completion.

### CRUD screen

- Given a user opens the [entity] area on desktop, when the list loads, then a table provides search, filters, sorting, pagination, and relevant bulk actions.
- Given the [entity] list is viewed on mobile, when the viewport reaches the mobile breakpoint, then the table becomes readable cards without losing actions or state.
- Given create and edit are available, when either action opens, then both use the same form component with record-specific initial values.
- Given the form has five or fewer fields and no nested data, when create or edit opens, then it uses a modal.
- Given the form has more than five fields without complex related data, when create or edit opens, then it uses a page or side drawer, and a chosen drawer becomes a full-screen sheet on mobile.
- Given the record has tabs, nested items, or related lists, when create or edit opens, then it uses a full page.
- Given the [entity] list is empty, when it loads, then it explains what belongs there and offers the single primary action.
- Given the [entity] list or record is loading, when data is pending, then skeletons preserve layout.
- Given the [entity] list or record fails to load, when the failure is shown, then a persistent message and retry action appear.
- Given filters, sort, page, or the open record changes, when the URL is copied or refreshed, then the same state is restored.
- Given a save succeeds, when the form closes, then the user returns to the originating view with the changed record visible and briefly highlighted.
- Given deletion is reversible, when the record is soft deleted, then an automatically disappearing Undo toast appears.
- Given deletion requires confirmation, when the user initiates it, then a confirmation modal names the record and consequence.
- Given a destructive action is at scale, when the typed record name does not match, then execution remains blocked.

### Form

- Given an invalid field, when it loses focus or the form is submitted, then an inline error appears beside that field and the entered value remains.
- Given a form with unsaved changes, when the user closes it or navigates away, then an unsaved-changes warning appears and the form stays open until the user explicitly discards or saves.
- Given the user cannot perform an action, when the form renders, then unauthorized actions are hidden and temporarily unavailable actions are disabled with an explanatory tooltip.
- Given a keyboard-only user, when they traverse the form, then every labelled field and action is reachable, has a visible focus state, and meets WCAG AA contrast.

### Modal

- Given a modal is in scope, when it opens, then it was triggered by the user for a focused short task or confirmation and no second modal can open from it.
- Given the page first loads, when no user action has occurred, then no popup or modal opens.
- Given a modal has no unsaved changes, when the user presses Escape or clicks outside, then it closes.
- Given a modal has unsaved changes, when the user presses Escape or clicks outside, then a warning appears and the modal remains open.
- Given a modal is open, when the user navigates by keyboard, then focus stays inside it.
- Given a modal closes, when focus is restored, then it returns to the triggering element.
- Given a modal presents actions, when it renders, then Cancel is on the left, the specific primary verb is on the right, and destructive actions use destructive styling. Labels such as `OK` and `Yes` are absent.
- Given an action succeeds, when feedback appears, then it is an automatically disappearing success toast.
- Given an error occurs, when feedback appears, then it remains visible until dismissed or fixed and browser `alert()` and `confirm()` are not used.

### General UI

- Given a page renders, when the header is visible, then it shows the page title and at most one primary action in the top right.
- Given the view is styled, when its computed design values are inspected, then spacing, color, type, and radius come from design tokens rather than one-off values.
- Given actions render in any view, when their labels and order are inspected, then each button uses a specific verb, Cancel is on the left, the primary action is on the right, and destructive actions use destructive styling.
- Given an action succeeds, when feedback appears, then it is an automatically disappearing success toast.
- Given an error occurs, when feedback appears, then it stays visible until fixed or dismissed, explains recovery when relevant, and browser `alert()` and `confirm()` are absent.
- Given any view is loading, when data is pending, then layout-preserving skeletons appear instead of a full-page spinner.
- Given a table contains numbers, currency, dates, or times, when cells render, then numbers and currency are right-aligned and dates and times use the application format.
- Given a keyboard-only user traverses any view, when interactive elements receive focus, then every control is reachable, focus is visible, contrast meets WCAG AA, and form fields are labelled.
- Given a drawer is viewed on mobile, when the mobile breakpoint is reached, then it becomes a full-screen sheet without losing state or actions.
- Given the same action is repeated from the same state, when it completes, then it produces the same result in the same place without surprise navigation or layout shift.

### Staging files (ecc)
`.claude/prds/X.prd.md` -> `.claude/plans/X.plan.md` -> TDD -> `/pr`

### Agent boot
Load this template, DONE-STATES, the current global `ui-ux-principles` skill, and `04-verify-feedback-loops` before implementing.
