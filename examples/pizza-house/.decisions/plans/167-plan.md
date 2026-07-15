# Plan — Ticket #167: Define visual direction and acceptance criteria

**Project**: Ember & Crust Pizza House (id=12)
**Board**: Ember & Crust Delivery (id=9) · Lane: Scoped work
**Iteration**: 2 of 6 (re-running the slice from run #85 with harness verification this pass)

## Goal

Formalise visual direction for `index.html`/`styles.css` and define measurable acceptance criteria so subsequent tickets (#168 — responsive menu + landing page, #170 — accessible customer journey, #174 — quality review) build against a single confirmed aesthetic. This ticket produces documentation only; it does not ship new components.

**Output artefacts (this ticket)**:
- `brand-tokens.md` — design tokens, palette, typography, layout rules (existing from run #85 will be retained and verified here)
- Acceptance criteria document with measurable checks against the static build in this repository

## Slices

| Slice | What is produced | Affected files | Rollback |
|-------|------------------|----------------|----------|
| 1 — Palette lock | `brand-tokens.md` confirms four tokens and pixel-invariant rules | `.decisions/brand-tokens.md` | Delete the file (no CSS change was touched) |
| 2 — Typography lock | Type scale + family table in the same token file (reused from #85 work, confirmed here against build) | Same as above | Same rollback |
| 3 — Layout & responsive rules | Grid/column/spacing conventions that every subsequent ticket must follow | Same document; no code touched | Document-only change, revert by deleting section or file |
| 4 — Measurable acceptance criteria | AC-01 through AC-06 with programmatic check hooks that future tickets use at validation time | `docs/plans/ac-check-list.md` (new) | Delete the new file only |

## Acceptance criteria summary (for ticket self-assessment)

| # | Criterion | How I will verify in this iteration |
|---|-----------|-------------------------------------|
| AC-S1 | A token document exists at `.decisions/brand-tokens.md` describing 4 tokens + the visual anchor phrase | `test -f .decisions/brand-tokens.md && wc -l < .decisions/brand-tokens.md` — expect ≥80 |
| AC-S2 | Acceptance-criteria file exists with numbered, measurable checks | `test -d docs/plans && ls docs/plans/ac*.md` returns at least one path |
| AC-S3 | Tokens declared in the token document match the live CSS custom properties on `:root{}` of `styles.css` | Grep `styles.css` for `--ink`, `--paper`, `--ember`, `--cream`; confirm values align with brand-tokens.md table |
| AC-S4 | Each acceptance criterion is self-testable by a Playwright screenshot or CSS-variable grep — not dependent on an external design tool | Readability pass: every AC number has a named check hook (e.g. AC-01 → pixel-sample ≥96%) and no criteria require third-party sign-off to evaluate |
| AC-S5 | Build is unchanged from this ticket's scope — the plan produces only docs, nothing CSS/HTML | `git diff` reports zero code changes (or a clean working tree in non-git mode: confirm file list of touched files contains only `.decisions/*.md` and `docs/plans/*.md`) |

## Rollback notes

- All artefacts produced by this ticket are pure documentation. Reverting means deleteing the affected files; no user-visible code is changed.
- If Paul wants the dark-navy-in-ink direction first, reopen with updated token values before any colour refactor lands on a subsequent ticket (AC-S3 would catch that drift immediately).

## Linked artefacts from prior runs

- `.decisions/brand-tokens.md` — visual anchor "rustic-artisanal-warm", four-token palette `paper / ink / ember / cream`, Georgia body, system-ui eyebrow labels. Produced in run #85 on the same ticket scope; retained here as the confirmed baseline to verify and lock down with measurable checks.
- `.decisions/plans/` — location for this iteration's plan artefact.

## Next step

Once I write `docs/plans/ac-check-list.md` (Slice 4), run AC-S3 against the live CSS, and confirm zero-code-state delta before marking this ticket attached with evidence.
