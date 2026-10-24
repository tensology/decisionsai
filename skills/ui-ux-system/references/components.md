# Components

Patterns for the components that appear in nearly every product screen. Tailwind classes assume the
default tokens; swap in the project's tokens if it has them.

## Buttons

| Kind | When | Classes (36px) |
|---|---|---|
| Primary | the one main action of a region | `h-9 px-3 rounded-md bg-blue-600 text-white text-sm font-medium hover:bg-blue-700` |
| Secondary | alternatives, Cancel | `h-9 px-3 rounded-md border border-slate-300 bg-white text-slate-900 hover:bg-slate-50` |
| Tertiary / ghost | low-emphasis, toolbars | `h-9 px-2 rounded-md text-slate-700 hover:bg-slate-100` |
| Destructive | final confirm of a destructive action | `bg-red-600 text-white hover:bg-red-700` |
| Destructive (quiet) | Delete entry point in a menu/row | `text-red-600 hover:bg-red-50` |
| Icon-only | row actions, close | `size-8 grid place-items-center rounded-md` + `aria-label` + tooltip |

All buttons: `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2`,
`disabled:opacity-50 disabled:cursor-not-allowed`, `type="button"` unless it submits.
Busy: keep width, swap label to "Saving…", show a 16px spinner inside, `disabled` + `aria-busy="true"`.
Labels: verb + object, sentence case ("Add book"). The same verb flows through the toast ("Book added").
Order: Cancel (secondary) left, primary right, in dialogs and form footers. One primary per region.
Mobile: min 44×44px hit area (pad or `min-h-11`).

## Forms

Structure
- Label above input by default (`text-sm font-medium text-slate-900`, 4–6px gap). Labels embedded in the
  input border (floating/notched) or to the left of the control are allowed where the project does that,
  and labels inside full-width selectors are allowed (SKILL.md rule 2a.1). Never placeholder-as-label.
- Required: mark required fields with `*` + legend "Required fields are marked *", or mark optional ones,
  whichever is fewer. Use `required` + `aria-required`.
- Group long forms into fieldsets in task order: identity → details → advanced → read-only history.
- Input: `h-9 px-3 rounded-md border border-slate-300 text-sm` (border slate-500 if no other boundary
  cue), `focus:ring-2 focus:ring-blue-500 focus:border-blue-500`. Mobile `text-base` (16px).
- Correct `type`, `inputmode`, `autocomplete`, `name`. Don't block paste.
- Helper text below in `text-xs text-slate-600`, only if it adds information.

Validation timing (NN/g + common practice)
1. Don't validate while the user is still typing a fresh field.
2. On blur of a touched field: validate that field.
3. Once a field shows an error: re-validate on each input so the error clears the moment it's fixed.
4. On submit: validate all; if errors, show each inline, focus the first invalid field, optional summary
   at top linking to fields (never the only indicator).
5. Server errors map back onto fields when possible; otherwise an error callout above the footer.
   Auth screens (sign in, sign up, reset): show the failure as a centred pink error callout above the
   auth fields (SKILL.md rule 2a.5).
6. Never clear entered data on error. Disable submit only while the request is in flight.

Error presentation: `text-xs text-red-600` below the field with an icon, input `border-red-600`,
`aria-invalid="true"`, `aria-describedby` → error id. Message says what and how: "Enter a year between
1450 and 2026", not "Invalid input".

Unsaved changes: track dirty state; on close/navigate show "Discard changes?" with "Keep editing"
(default focus) and "Discard". Overlay click on a dirty form does nothing (or asks).

## Tables / data lists

Anatomy (top to bottom): content header (title left, primary action right) → toolbar (search, filters,
view options) → table → footer (count + pagination). Bulk action bar replaces the toolbar while rows
are selected ("3 selected · Export · Delete · Clear").

Rules
- Real `<table>`, `<thead>` with `<th scope="col">`, `text-xs font-medium text-slate-600` headers on
  `bg-slate-50`, rows `h-10` `border-b border-slate-200`, hover `bg-slate-50`.
- First column identifies the record (name, as a link to detail if a detail exists). People: "First Last",
  email secondary.
- Numbers, money, counts right-aligned, `tabular-nums`. One date format app-wide (e.g. `22 Oct 2026`).
- Sort: clickable header button with icon, `aria-sort="ascending|descending|none"`, one sort at a time
  by default; default sort stated (newest first, or name A–Z).
- Search: debounce 200–300ms, searches the visible text fields, shows "No authors match 'xyz'" + Clear.
- Filters: chips or selects in toolbar; active filters visible and removable; "Clear all".
- Pagination: >25 rows. "1–20 of 134", prev/next, page size select optional. Infinite scroll only for feeds.
- Row actions: one right-aligned Actions column, icon buttons (Edit, Delete) with `aria-label="Edit Ursula Le Guin"`,
  or a ⋯ menu when >2 actions. Cells read-only; no inline inputs unless the user asked for inline edit.
- Row click: either opens detail or does nothing; never both click-to-select and click-to-open.
- Long text: `truncate` with `title`, `min-w-0` on flex children. Genuinely long titles may marquee
  instead (honour `prefers-reduced-motion`).
- Mobile (<640px): rows become stacked cards (name, 2–3 key fields, actions menu) or keep a horizontally
  scrollable table inside its own container (`overflow-x-auto`), never the page.
- Routed apps: put `q`, `sort`, `page`, filters and open record in the URL.

## Master / detail (list → related items)

- Desktop ≥1024px: split view (list 1/3, detail 2/3) or list page → detail page with back link and the
  parent name as title. Selected list item `bg-blue-50` + `aria-current="true"`.
- Mobile: list and detail are separate views; back button returns to the list at the same scroll position.
- Child collection in detail gets its own header ("Books · 4" + "Add book") and its own empty state.
- Deleting the parent states the cascade of children.

## Container choice: inline vs popover vs drawer vs modal vs page

| Need | Use |
|---|---|
| Change one value, pick from short list | inline control / dropdown / popover |
| 1–5 simple fields, no need to see the page behind | modal (max-w-md / lg) |
| Longer form, or user needs to reference the list | right drawer (max-w-lg), full-screen sheet on mobile |
| Nested data, tabs, related lists, deep-linkable | full page |
| Confirm destructive or irreversible action | small alert dialog (max-w-sm) |
| Transient result | toast |

Modal checklist: centred both axes (`fixed inset-0 grid place-items-center p-4`), overlay
`bg-slate-900/50`, panel `rounded-xl bg-white shadow-xl w-full max-w-md`, `role="dialog" aria-modal="true"
aria-labelledby`, focus first field (or Cancel for destructive), Tab trapped, Escape and × close, overlay
click closes unless dirty, body scroll locked (`overflow:hidden` on `<html>`) and restored, focus returns
to the trigger, header title states the action once ("Edit book"), footer Cancel + verb primary, body
scrolls inside panel when tall (`max-h-[85vh] overflow-y-auto`), never stacked, never opened on load.

## Navigation

- App shell: left sidebar 224–256px (`bg-slate-50 border-r`), groups with `text-xs` labels, items
  `h-9 px-3 rounded-md text-sm`, active = solid brand/logo blue fill with white text and white icon
  (e.g. `bg-blue-600 text-white`, icon `text-white`), no underline or bottom border, `aria-current="page"`. Collapsible to icons ≥1024px; on mobile a hamburger opens a sheet.
- Content header: page title (text-xl font-semibold) left, primary action right by default, optional
  filters/tabs below. A contextual add-to-list action ("Add itinerary") may sit beneath its table. Do not repeat the title inside the content.
- Tabs: for sibling views of the same object; selected tab is the section title (no repeated heading).
- Breadcrumbs only at depth ≥3; last crumb is the current page, not a link.
- Marketing: top bar with ≤6 links + one CTA; mobile sheet.

## Menus, dropdowns, comboboxes

- Use native `<select>` for simple forms unless styled consistency is required; themed for dark mode.
- Custom menus: button with `aria-haspopup="menu" aria-expanded`, list `role="menu"`, arrow keys,
  Escape closes and returns focus, click-outside closes, rendered above content (z-dropdown), flips to
  stay in viewport, never pushes layout. Menus, popovers and dropdowns have no border; `shadow-md` is
  the separation, and they sit inset from the viewport edge.
- Combobox/autocomplete: results visible under input after 1+ chars, "No results" row, Enter selects,
  highlight matches. Test that the results list actually renders.
- Toggle states in menus use a checkmark or switch, not a coloured dot.

## Toasts and inline feedback

- One reusable snackbar component at top centre (desktop and mobile), `aria-live="polite"` (`assertive`
  only for errors that block). It overlays content and never shifts layout. Success auto-dismiss 4–6s; with Undo 8–10s; errors persist until dismissed.
- Max 3 visible, newest on top. Don't toast what is already visible in place.
- Inline: status chip or text next to the affected row/field ("Saved", "Sync failed · Retry").

## Badges / status

`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium` with subtle bg + strong text
(green-50/green-700 etc). Status = colour + text (+ icon), never colour alone. Pills (`rounded-full`)
only for filters/tags/counts.

## Icons

One icon set, 16px in body text / 20px in nav, `aria-hidden="true"` when decorative, stroke width
consistent. No emoji as UI icons. Icon-only controls need `aria-label` and a tooltip.
