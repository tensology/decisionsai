# States

Every data-bearing view must implement and expose these states. Make each reachable for testing: a
dev toggle, `?state=empty|loading|error` query param, or clearly documented code path. A view without
its states is not done.

| State | When | Must show | Copy pattern |
|---|---|---|---|
| Empty (first use) | collection has no items yet | short explanation of what goes here + primary action; optional icon; no illustration fluff | **No authors yet.** Add your first author to start tracking books. [Add author] |
| Empty (no results) | search/filter returns nothing | the query, how to recover | **No authors match "tolk".** Check the spelling or [Clear search]. |
| Empty (child) | parent exists, no children | inside the detail panel, scoped | **No books for Ursula Le Guin.** [Add book] |
| Loading (initial) | first fetch | skeleton matching final layout (header, 5–8 row bars); show only after ~300ms to avoid flash | (no text; `aria-busy="true"` on region, sr-only "Loading authors…") |
| Loading (action) | save/delete in flight | spinner inside the button, label "Saving…", disabled; row-level "Deleting…" | Saving… / Deleting… |
| Refreshing | background refetch | nothing, or a tiny indicator in the header; never replace content with a spinner | – |
| Error (load) | fetch failed | what failed, why if known, Retry; keep layout frame | **Couldn't load authors.** Check your connection and try again. [Retry] |
| Error (action) | save/delete failed | inline at the form/row; input preserved; retry | **Couldn't save book.** The server didn't respond. Your changes are still here. [Try again] |
| Validation | field invalid | below field, specific fix | Enter a title. / Year must be between 1450 and 2026. |
| Partial | some items failed, stale data, truncated results | which part, impact, retry for that part | **2 of 5 books couldn't be imported.** [View details] [Retry failed] |
| Success | action completed | toast or inline, auto-dismiss, changed row highlighted ~2s | Author added. / Book deleted. [Undo] |
| Permission denied (page) | no access | why + who to ask; no dead UI | **You don't have access to Billing.** Ask a workspace admin to give you access. |
| Permission denied (action) | can see, can't act | hide the action, or disable with a tooltip reason | Only admins can delete authors. |
| Offline | `navigator.onLine === false` or requests fail at network level | top banner; reads from cache; writes disabled or queued with clear status | **You're offline.** Changes will sync when you're back online. |
| Not found | record id missing | what, link back to list | **That author doesn't exist or was deleted.** [Back to authors] |

Copy rules
- Sentence case, plain verbs, no apologies ("Sorry!"), no blame ("You entered…"), no jokes in errors.
- Name the object: "Couldn't save book", not "Something went wrong".
- Error = what happened + what to do. Empty = what goes here + the action. Success = past-tense verb
  matching the button ("Delete book" → "Book deleted").
- Use `…` (single character) for in-progress labels.

Accessibility
- Loading regions: `aria-busy="true"`; announce completion via the polite live region if not obvious.
- Toast/live region exists in the DOM at load (`role="status"` / `aria-live="polite"`) so updates are announced.
- Error callouts: `role="alert"` only for blocking errors.

Testing hook (single-file demos): include a small, unobtrusive "Demo state" select (Normal / Empty /
Loading / Error) in a dev corner or behind `?state=`, clearly labelled as demo-only.
