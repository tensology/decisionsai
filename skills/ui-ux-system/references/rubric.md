# Self-check rubric (100 points + gates)

Score honestly from a rendered page, not from source alone. Report the table. Ship only if every gate
passes and total ≥ 80.

## Gates (any fail = not done)

| # | Gate | How to verify |
|---|---|---|
| G1 | All applicable states exist and are reachable (empty, no-results, loading, error, success; denied/offline if relevant) | toggle each; screenshot |
| G2 | Every visible control works; overlays close by ×, Escape, overlay click | click through in a browser |
| G3 | Destructive actions confirm (naming record + cascade) or offer Undo | try delete |
| G4 | Inputs labelled; visible focus ring; keyboard path list → create → save → delete works | Tab through |
| G5 | No invented copy/metrics, no decorative filler, no duplicated titles/actions | read the page |
| G6 | Token discipline: no arbitrary values, one accent, ≤2 font families | grep for `[`, hex, `style=` |
| G7 | 390, 768 and 1280px (plus 1440 for wide layouts) render without page overflow, clipping or misalignment | screenshots |
| G8 | Interaction rationale present, each line names a principle | read output |
| G9 | Theme completeness and colour pairing pass in every supported theme (SKILL.md 2b) | toggle themes; inspect every state |
| G10 | Multi-route/role work: coverage matrix complete, request reconciled line by line (SKILL.md 2c) | matrix in output |

## Scored criteria

| Area | Pts | Full marks when |
|---|---|---|
| States coverage | 20 | empty (first-use + no-results + child) 6, loading skeleton + button busy 5, load/action error with retry and preserved input 5, success feedback with live region 4 |
| CRUD safety & flow | 15 | shared create/edit form 3, confirm/undo naming record and cascade 4, double-submit prevention 2, unsaved-changes guard 2, focus/scroll to changed row and toast 2, no `alert/confirm` 2 |
| Forms & validation | 10 | labels + required marking 3, blur/submit timing and focus first error 4, specific messages 3 |
| Hierarchy & density | 15 | one primary per region top-right 4, text-sm body / text-xs meta, 8/12/16 rhythm, no wasted vertical space 5, no duplication or filler 6 |
| Token consistency | 10 | spacing/radius/type/colours all from scale 6, consistent components across views 4 |
| Accessibility | 15 | contrast ≥4.5:1 text, ≥3:1 UI 4, focus-visible everywhere 3, dialog semantics + focus trap/return 4, aria-labels on icon buttons, aria-sort, live regions 4 |
| Responsive | 10 | mobile recomposed (cards or contained scroll) 4, touch targets ≥44px 3, nav/overlays adapt (sheet) 3 |
| Rationale | 5 | present 2, each line cites a valid principle 3 |

## Quick automated checks (when a browser is available)

- Contrast: compute for text colour vs background of each text style used.
- `document.querySelectorAll('input,select,textarea')` each has a `<label for>` or `aria-label`.
- Icon-only buttons have `aria-label`.
- `document.documentElement.scrollWidth <= innerWidth` at 390px.
- Press Tab: focused element always has a visible outline/ring.
- Open dialog → Escape closes → `document.activeElement` is the trigger again.
- grep: `alert(`, `confirm(`, `transition-all`, `outline-none` without `focus-visible:ring`, `p-[`, `text-[#`.

## Report format

```
Rubric: 86/100, gates 10/10
States 18/20 · CRUD 14/15 · Forms 8/10 · Hierarchy 13/15 · Tokens 9/10 · A11y 12/15 · Responsive 8/10 · Rationale 4/5
Not verified in browser: (list) 
```
