# Acceptance-criteria check list — Ticket #167

These are the self-testable checks that validate this ticket produced its stated outcomes. They reference no external design tools; every item can be evaluated with file reads or Playwright.

## Token document present and sufficient (from plan AC-S1)

| # | Check hook | Pass condition |
|---|-----------|----------------|
| AC-01a | `wc -l .decisions/brand-tokens.md` | line count ≥ 80 |
| AC-01b | Grep for "rustic-artisanal-warm" inside that file | ≥ 1 match (visual anchor named) |

## Measurable criteria defined as a separate artefact (from plan AC-S2, AC-S4)

| # | Check hook | Pass condition |
|---|-----------|----------------|
| AC-02a | `ls docs/plans/ac-*.md` returns ≥ 1 file | check-list written |
| AC-02b | Count numbered lines across the file (grep for `^AC-\d+`) | ≥ 6 unique criterion numbers present |

## Tokens declared on live CSS match the token document (from plan AC-S3)

For every token in the brand-tokens.md table: `Token → value`. Compare against `:root{}` at top of `styles.css`.

| # | Token declaration to verify | Pass condition |
|---|-----------------------------|----------------|
| AC-03a | `--paper` = `#f4efe5` | `grep -- '\-\-paper:#f4efe5' styles.css` returns 1 match |
| AC-03b | `--ink` = `#201c18` | `grep -- '\-\-ink:#201c18' styles.css` returns 1 match |
| AC-03c | `--ember` = `#d64b2a` | `grep -- '\-\-ember:#d64b2a' styles.css` returns 1 match |
| AC-03d | `--cream` = `#fffaf0` | `grep -- '\-\-cream:#fffaf0' styles.css` returns 1 match |

## Zero-code state delta from this ticket (from plan AC-S5)

| # | Check hook | Pass condition |
|---|-----------|----------------|
| AC-04a | `git diff --stat` (or equivalent) lists only `.decisions/*md` and/or `docs/plans/*md` files touched | zero changes to source code (`*.css`, `*.html`, `*.js`) introduced |
| AC-04b | No new directories created outside `.decisions/` or `docs/` | confirmed by file-list inspection, no new non-doc paths added |

## Visual-anchor and design decision recorded (from plan slices 1 & 2)

The brand-tokens.md must contain:

| # | Required element | Pass condition |
|---|-----------------|----------------|
| AC-05a | A "Visual direction" section with the anchor phrase ("rustic-artisanal-warm") | ≥ 1 match for the phrase in that section header or body |
| AC-05b | A documented typography table (≥ 3 rows, one per role) | line-count pass from the table block ≥ 9 lines |
