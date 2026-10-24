---
name: ui-ux-system
description: The single UI/UX authority. Use when planning, building, changing, reviewing, or testing any user-facing web UI (Django/Unfold, Tailwind, React, plain HTML), and it leads all Operate work (apps, admin, dashboards, settings, tables, CRUD, forms). Covers tokens, layout, forms, tables, CRUD flows, modals, states, a11y, theme completeness, Paul's settled taste rules and taste log, and a scored self-check.
---

# UI/UX System

One operational playbook for product UI. It encodes Paul's observed taste and recurring
corrections (mined from ~4,350 of his agent-chat turns, his MemPalace UI feedback, and the
Tailwind/CSS in his shipped projects), then fills gaps with verifiable best practice.
Evidence counts are in `references/sources.md`.

**This skill is the single authority for interface behaviour and presentation.** `ui-ux-principles`
is now a redirect here, and `compact-ui-decisions` has been folded in. Other UI skills (frontend-design,
design-taste, impeccable, ui-ux-pro-max, apple-design, presets) add specialist guidance only; for
app, dashboard and product UI they defer to this skill. Load references only when needed:

| Need | Read |
|---|---|
| Exact token values, Tailwind mapping, adopting a project's tokens | `references/tokens.md` |
| Buttons, forms, tables, modals/drawers, nav, menus, toasts | `references/components.md` |
| Empty / loading / error / partial / success / denied / offline + copy | `references/states.md` |
| Create / edit / delete / bulk / optimistic / undo flows | `references/crud-flows.md` |
| Scoring before you finish | `references/rubric.md` |
| Where each rule comes from | `references/sources.md` |
| Paul's dated corrections (read the last 20 entries before UI work; append new ones) | `references/taste-log.md` |

## 0. Order of authority

1. The user's explicit words and any screenshot or reference they name ("make it look like X" means match X, verbatim, not "inspired by").
2. The existing project: its tokens, shell, nav, components, copy, and behaviour. Change only the requested delta.
3. Paul's rules (section 2) and his taste log (`references/taste-log.md`). Where the log and a generic default in this skill disagree, Paul's log wins.
4. The default system in this skill.
5. Generic taste. Never let generic taste override 1 to 3.

## 1. Before writing UI code

1. **Name the job.** One sentence: who uses this screen, for what task, in which mode
   (Operate = app/admin/dashboard; Persuade = marketing; Read = docs). Operate is dense and quiet.
   Persuade (marketing) surfaces may use ambient motion and gradients where the brand or brief calls for
   it (for example the PlanMyCharter hero waves and layered wave dividers); Operate UI stays restrained:
   no ambient loops, gradient washes or decorative motion.
2. **Adopt existing tokens first.** Search the project for `tailwind.config.*`, `@theme`, `:root { --… }`,
   Unfold `UNFOLD["COLORS"]`, shadcn `components.json`/`globals.css`, existing button/input classes.
   If found, map them onto the roles in section 3 and use only those. Only if nothing exists, use section 3.
3. **Sketch the flow and wireframe (text, 10 lines max).** List the user flow as steps
   (`List → New → Form → Save → back to List with row highlighted`) and an ASCII wireframe of each
   distinct screen or overlay. This catches extra clicks and wrong container choices before code.
4. **Inventory real content.** Use the real data model, field names and copy. If something is unknown,
   use a short functional label or ask. Never invent metrics, testimonials, features or marketing lines.
5. **Preserve product truth.** Current product behaviour, approved screenshots, committed navigation,
   shared shells and user-named exemplars are constraints. "Polish", "clean up", "audit" and "make this
   better" do not authorize a redesign. Make the requested delta only; do not rename, relocate, add or
   remove adjacent navigation, controls, content or workflows. Never fabricate product semantics, counts,
   statuses, roles, copy, controls or workflows to fill a gap: inspect the real implementation and data,
   and ask if meaning is still unclear. When an approved reference and the implementation differ, name
   the exact difference and confirm which is authoritative before replacing behaviour.
6. **Read the taste log.** Read the last 20 entries of `references/taste-log.md` before starting.

## 2. Paul's rules (evidence-backed; these win over generic advice)

Each rule cites the evidence theme. `n` = count of Paul's chat turns matching that theme.

**Density and spacing** (MemPalace: 5 `spacing_off` + 4 `approved: dense/compact` drawers; "too much space/padding" n=29; commits "Compact…", "Tighten…" in tensology.com, Mailshot, DecisionsAI n=26)
- Operational UI is compact: body `text-sm` (14/20), meta and table headers `text-xs` (12/16).
  This matches his shipped code (text-sm and text-xs are the top two sizes in every project surveyed).
- Rhythm is 8/12/16px (`gap-2`, `gap-3`, `p-4`), the three most-used spacings in his code.
  Toolbars `py-2`; table rows 40px; card/panel padding 16px; modal padding 20–24px. Never stack padding on
  padding (wrapper inside card inside section, each with `p-6`).
- No dead vertical space: no empty hero, no spacer divs, no `min-h-screen` centring of small content
  inside an app shell.

**Remove, don't add** ("remove/get rid of/take out/hide …" n=110, the largest single correction; MemPalace `inconsistent_styling`, `hierarchy_unclear`)
- Every element must earn its place. No welcome banners, stat cards that restate the table, decorative
  icons, gradient washes, emoji, footers inside app screens, duplicate titles (page title + card title + tab
  label saying the same thing), or helper text that repeats the label.
- No page title or subtitle when the navigation already names the page (e.g. an "Automations" page
  must not open with a "Scheduled tasks" heading and subtitle). Start directly with the content or its
  toolbar. (Paul, 2026-10-22, MemPalace frustration.)
- One primary action per view, top-right of the content header by default. Do not repeat it in a footer.
  Exception: a contextual action that adds to a list or table (for example "Add itinerary") may sit
  directly beneath that table.

**Real content, no AI slop** ("slop/brochure/what is this copy/fake" n=17 + placeholder/fake n=27)
- No invented copy ("Field proof beats brochure copy", "Manage your items with ease"), no numbered
  `01/02/03` markers on non-sequences, no fake prices/estimates/counts. Labels are short and literal.
- Demo or seed data must look like the real domain and be clearly the only fake thing.

**Alignment and centring** ("vertically/horizontally centred / not centred" n=52; "align" commits n=31)
- Use flex/grid with `items-center` for every row that mixes icon, text, button, avatar. Modals are
  centred vertically and horizontally in the viewport. Section edges share one content frame.
- Check at 390, 768 and 1280 (plus 1440 for wide layouts) that headings, icons and controls line up on a common edge; mobile text in cards
  that was centred on desktop stays deliberately aligned (do not let it drift).

**Consistency across pages** ("consistent/inconsistent/same as/standardised" n=48; "Unify…" commits)
- Same component, same look, same behaviour everywhere: one button set, one input style, one modal,
  one date format, one font pairing. Sibling pages (login / forgot password / sign up) share one layout.
- Fonts come from tokens. Do not swap the brand font (his brand font is Quicksand on Tensology, Xtract,
  Systems, Decisions; Inter in Mailshot); never introduce a third family.

**Everything that looks clickable works** ("doesn't work / nothing happens / close button / can't click" n=44; "still/again/I said" n=215)
- Every button, link, close `×`, overlay click, Escape, row click and menu item is wired and tested.
  Close buttons close. Clicking a row does exactly one predictable thing.
- Do not claim done from source review. Open the page, click every control, test the happy path and
  one failure path at desktop and mobile widths (MemPalace `flow_bad`: "validate the complete responsive
  flow in Playwright before calling an interface finished").

**Modals and dropdowns** ("modal" n=63; "dropdown" n=62)
- Prefer the lightest container: inline control > dropdown/popover > drawer > modal > page.
  "Why a whole modal if the import button can be a dropdown" is a real correction.
- Modals: centred, dark overlay (`bg-slate-900/50`), working `×`, Escape and overlay click (unless
  dirty form), focus trapped and returned, body scroll locked while open and restored after, never
  stacked, never on page load.
- Dropdowns/comboboxes must actually show results, be keyboard operable, sit above other content
  (z-index), and never push layout down. Labels that would push content become a badge/legend.

**Feedback beside the cause** (MemPalace `spacing_off` + `hierarchy_unclear`: "show status beside the affected item"; spinner/feedback n=28)
- Busy state lives in the triggering control: button shows a small spinner + "Saving…" and is disabled
  for the request. No giant preloaders; small spinner next to text if needed. Background refreshes are
  silent (no spinner flash).
- Success: the shared top-centre snackbar or an inline confirmation in place. Errors: persistent, next to
  the field or row (auth errors: centred pink callout above the fields, rule 2a.5).

**Too small / too bold** ("too small" n=16; "too big/bold" n=11)
- On marketing/print surfaces body is ≥16px and secondary text ≥14px; on app surfaces see density rules.
- Weights 400/500/600. Use 700 only for one page title if the brand needs it. Avoid all-bold UIs.

**Grouping and compactness** (folded in from `compact-ui-decisions`)
- Group by meaning: identify information and action groups before changing layout; keep one semantic
  unit in one row when space permits; put title, relevant status and immediate action in one header
  row; at compact widths reflow whole groups instead of stacking every child.
- Icons sit inline with the title, label or action they support. Never give a lone icon its own row
  unless it is the primary illustration or empty-state content.
- Rows stay clean: reveal the one immediate row action on hover or focus (always reachable by keyboard
  and on touch), and put infrequent management actions in a contextual menu.
- Duplication pass before handoff: for every icon, badge, label, border and status, say what would
  become unclear if it were removed; remove anything with no defensible answer. Represent one fact once
  per row (no provider icon plus the same provider name).
- Light/dark check: when the app supports both themes, inspect both; critical operational information
  must survive in each.

## 2a. Settled taste rules (from `references/taste-log.md`; these override older defaults)

1. **Labels.** Labels embedded in the input border (floating or notched) or placed to the left of the
   control are allowed wherever the project does that; a visible label above the field is only the
   fallback default. Labels inside full-width selectors are allowed. Placeholder-as-only-label is still
   banned, and every control keeps an accessible name.
2. **Toasts and snackbars.** One reusable snackbar component, positioned top centre, for transient
   feedback (replaces the old bottom-right toast region). It never shifts page layout.
3. **Selected navigation item.** Solid brand/logo blue fill, white text and a white icon; no pale
   background, underline or bottom border.
4. **Menus, popovers and dropdowns.** No border; use shadow for separation. Inset from the viewport
   edge rather than flush to it.
5. **Auth errors.** Authentication failures show as a centred pink error callout above the auth
   fields. Field-level validation errors elsewhere still sit below their fields.
6. **Primary action position.** Top-right of the content header is the default, but a contextual
   action that adds to a list or table (for example "Add itinerary") may sit beneath that table.
7. **Long text.** Genuinely long titles may marquee (respecting `prefers-reduced-motion`); truncate
   with a `title` tooltip otherwise.

## 2b. Theme completeness and colour pairing (release blockers)

- Build from semantic tokens for surfaces, borders, text, muted text, accent, status, focus, type,
  spacing and radius. Colour has one meaning; brand colour never replaces error, warning, destructive,
  disabled or focus meaning.
- Mismatched foreground/background pairs (black on grey or dark, dark on dark, pale on white, colours
  inherited from the other theme) are release-blocking defects unless the pair is an intentional token
  with verified contrast. Passing an automated contrast check does not excuse a visibly discordant pair.
- A light or dark theme is complete only when every visible component and state uses that theme's
  tokens, including native controls, popups, menus, dialogs, tables, editors, tooltips, empty, loading
  and error states and third-party components. An unthemed one is a release blocker.
- Theme review covers default, hover, focus, active, selected, disabled, loading, empty, success,
  warning, error, destructive and overlay states.

## 2c. Coverage matrix (multi-route or role-sensitive work)

Keep a matrix of route x role/permission x state x viewport x requested behaviour, including negative
cases and hidden or unavailable actions. Every applicable cell is verified, explicitly excluded with a
reason, or reported as blocked. Reconcile the final UI line by line against every item in the request
and any supplied reference; a successful build, a representative screenshot or a passing happy path is
not proof of completion.

## 2d. Taste memory loop

- Whenever Paul rejects, corrects or reworks a UI decision, append one line to
  `references/taste-log.md`: `YYYY-MM-DD | Did: <decision> | Wanted: <replacement> | Why: <reason>`.
  Record only real corrections, never inferred preferences; ask for the reason if it is unclear.
- Keep corrections from the active thread in working context until they are recorded.
- When the same correction appears two or three times, propose promoting it to a rule in section 2 or
  2a. Do not promote one-offs.
- If a log entry contradicts a rule here, flag the conflict and ask which wins (by default the log wins).
- Older references to "Fix Memory" mean this loop; older references to "Pre-ship Check" or
  "Definition of Done - UI" mean section 10.

## 3. Default design system (use only when the project has none)

Full values, dark mode and Tailwind/Unfold mapping: `references/tokens.md`.

- **Color roles** (Tailwind names, light): `bg` white · `surface` slate-50 · `border` slate-200 ·
  `border-strong` slate-300 · `text` slate-900 · `text-muted` slate-600 · `accent` blue-600 (hover 700,
  subtle blue-50) · `focus` blue-500 ring · `success` green-700 · `warning` amber-700 · `danger` red-600
  (hover 700, subtle red-50). One accent. Status colours never used for decoration.
  White text needs blue-600 or darker (blue-500 is 3.7:1 and fails for button text).
- **Spacing** (4px base): 0, 2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64. No other values.
- **Type scale** (size/line-height px): 12/16 meta · 14/20 body/UI · 16/24 lead/marketing body ·
  18/28 section title · 20/28 page title · 24/32 · 30/36 marketing headline only. Tabular numerals for numbers.
- **Radii**: 4 badges/checkboxes · 6 inputs/buttons · 8 cards/menus/panels · 12 modals/drawers ·
  full only for avatars, pills, status dots.
- **Elevation**: 0 flat + 1px border (default) · 1 `shadow-sm` sticky header/raised card ·
  2 `shadow-md` menus/popovers/dropdowns, no border (shadow is the separation) · 3 `shadow-xl` modals/drawers with overlay.
- **Motion**: 100ms hover/press colour · 150ms menus/tooltips · 200ms modal/toast enter ·
  250ms drawer · exits ~25% faster · ease-out for both enter and exit (never ease-in for UI, consistent
  with the motion skills) · `transform`/`opacity` only · honour `prefers-reduced-motion`. No ambient
  loops in Operate UI (Persuade surfaces: see section 1).

## 4. Component rules (summary; details in `references/components.md`)

- **Buttons**: primary (filled accent, one per region) · secondary (white, border) · tertiary/ghost
  (text) · destructive (filled red, only inside a confirm step; elsewhere red text). Verb + object labels
  ("Save author", "Delete book"), never OK/Submit/Yes. Height 36px (32px dense toolbars), 44px min touch
  target on mobile. Cancel left of primary.
- **Forms**: every field has a visible, associated label (above the field by default; border-embedded or
  left-of-control where the project does that, rule 2a.1); required marked; correct `type`/`autocomplete`;
  validate on blur after the user has typed, re-validate on input once a field is in error, full
  validation on submit, focus the first invalid field; errors below the field in red-600 with an icon and
  `aria-describedby`; keep input on every error; warn before discarding unsaved changes.
- **Tables**: real `<table>` with `<th scope>`; toolbar = search (debounced 250ms) + filters + primary
  action; sortable headers with `aria-sort`; numbers right-aligned tabular; one Actions column on the
  right with labelled icon buttons or a menu; pagination with "1–20 of 134"; bulk select appears as a
  contextual action bar; rows become cards under 640px; state (search/sort/page) in the URL when routed.
- **Container choice**: ≤5 simple fields → modal; longer or needs context of the list → right drawer
  (full-screen sheet on mobile); nested/related data or tabs → page. Confirmations → small dialog.
- **Navigation**: app = left sidebar (collapsible; bottom tab bar or hamburger sheet on mobile) +
  content header with title and primary action; current item selected with a solid brand-blue fill,
  white text and white icon (rule 2a.3, `aria-current`).
  Breadcrumb only for depth ≥3. Master/detail (list → detail) keeps the list context (back link or split pane).

## 5. Mandatory states (details and copy patterns in `references/states.md`)

Every data view ships all applicable states, reachable for testing (toggle, query param or dev control):
**empty** (first-use with primary action; and no-results with "Clear filters") · **loading** (skeleton
matching final layout; button spinners for actions) · **error** (what failed + retry, input preserved) ·
**partial** (some items failed or stale data: say which, offer retry) · **success** (top-centre snackbar or inline,
auto-dismiss 4–6s, `aria-live="polite"`) · **permission-denied** (hide or disable with reason; never a
dead button) · **offline** (banner, queue or block writes, keep reads).

## 6. CRUD rules (details in `references/crud-flows.md`)

- List is home. Create and Edit share one form component and field order. After save, return to the
  list (or stay on detail) with the changed row visible and briefly highlighted.
- Delete: confirm dialog naming the exact record and consequence ("Delete author 'Ursula Le Guin' and
  her 4 books? This can't be undone.") with Cancel focused, destructive red confirm labelled with the
  verb; or soft-delete with a 6–10s Undo toast when restore is cheap. Cascades are stated explicitly.
- Optimistic updates only for low-risk, easily reverted changes (toggle, rename, reorder); roll back and
  show an error on failure. Creates and destructive actions wait for confirmation from the server.
- Prevent double submits (disable during request; idempotent handlers). Never `alert()`/`confirm()`.

## 7. Accessibility minimums (WCAG 2.2 AA)

Text contrast ≥4.5:1 (≥3:1 for ≥24px or ≥18.7px bold); UI component and focus-indicator contrast ≥3:1;
visible `:focus-visible` ring on every interactive element, never hidden under sticky bars; full keyboard
operation (Tab order = visual order, Escape closes overlays, Enter submits); targets ≥24×24px (44px on
touch); every input labelled; icon-only buttons have `aria-label`; status messages via `aria-live`;
dialogs use `role="dialog"`, `aria-modal`, labelled title, focus trap and focus return; colour is never
the only signal; zoom not disabled; `prefers-reduced-motion` honoured.

## 8. Responsive rules

Mobile-first; review at 390, 768 and 1280, plus 1440 for wide checks. Recompose by task order, don't shrink. Sidebar → sheet; table →
cards or priority columns with horizontal scroll inside the table container only; drawers → full-screen
sheets; toolbar wraps with primary action kept visible; no horizontal page overflow; inputs ≥16px on
mobile to avoid iOS zoom; sticky bottom action bar only for long forms.

## 8a. Copy punctuation

No em dashes (U+2014) in UI copy, rationale or labels the agent writes; use commas, colons, periods or
parentheses. Do not rewrite copy Paul or the client supplied. Typographic en dashes for numeric ranges
(`1–20 of 134`) are fine. This is the stack-wide ruling; other skills defer to it.

## 9. Required output: Interaction rationale

When you deliver UI, include a short **Interaction rationale** (in the reply, or as an HTML comment at the
top of a single-file deliverable). One line per significant interaction choice, each tied to a named
principle or Paul's rule. Format:

```
Interaction rationale
- Edit book in a modal (5 fields, keeps list context): Container choice rule; Jakob's law (familiar CRUD modal)
- Delete author asks to confirm and names cascade of 4 books: Nielsen #5 error prevention; CRUD delete rule
- Save button shows spinner + "Saving…" in place: Paul, feedback beside the cause; Nielsen #1 visibility of status
- Search filters as you type (250ms debounce): Doherty threshold (<400ms response)
```

Valid principle names: Nielsen heuristics #1–10, Fitts's law, Hick's law, Jakob's law, Miller's law,
Doherty threshold, Tesler's law, Postel's law, Gestalt proximity/similarity, WCAG 2.2 SC numbers,
the rules in this skill (cite section). A choice with no principle is a candidate for removal.

## 10. Self-check before you say done (Definition of Done - UI)

Run `references/rubric.md` and report the score table. Minimum to ship: every gate passes and total
≥ 80/100. Gates (any fail = not done):

1. All applicable states from section 5 exist and are reachable.
2. Every visible control works (clicked in a browser, not just read in source); overlays close by ×, Escape, overlay.
3. Destructive actions confirm or offer undo, naming the record.
4. All inputs labelled; focus ring visible; keyboard path through list → create → save → delete works.
5. No invented copy, metrics or decorative filler; no duplicated titles or actions.
6. Only token values used (no stray `p-[13px]`, `#3a3a3a`, ad-hoc shadows); one accent; ≤2 font families.
7. Rendered and inspected at 390, 768 and 1280 (plus 1440 for wide layouts) after the final change: no
   overflow, misalignment or clipped text.
8. Interaction rationale present.
9. Theme completeness and colour pairing (section 2b) pass in every supported theme.
10. For multi-route or role-sensitive work, the coverage matrix (section 2c) is complete and the request
    was reconciled line by line.

If you cannot run a browser, say exactly which checks were done by reading code only.
