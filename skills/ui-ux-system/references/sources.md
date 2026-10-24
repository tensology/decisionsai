# Sources and evidence

## 1. Paul's evidence (primary; collected 22 Oct 2026)

### Agent-chat history
Corpus: 4,353 unique user turns from Codex sessions + archived sessions (926 files) and Claude Code
(`history.jsonl`, `projects/**`). 893 turns matched "UI term + correction/negation". Phrase counts
across all turns (regex, one turn can match several):

| Theme | n | Representative quote (verbatim, trimmed) |
|---|---|---|
| Repeat failure ("still / again / I said") | 215 | "still no drop down. look harder" · "that prd and roadmap sidepanel is still not flush inside the page" |
| Remove an element | 110 | "in the footer remove all that logo shit, remove the donation stuff as well" |
| Modal problems | 63 | "the close button doesn't work! you have to click outside the modal" · "when the modal opens it must be center vertically and horizontally" · "why are you having a whole modal popup if the import button can be a drop down" · "the gallery modal popup doesn't have a overlay background that's blacked out" |
| Dropdown/select problems | 62 | "there is no drop down menu showing the results of the filter" · "Where you have charter above the drop-down box … it pushes everything down … make it look like a badge" |
| Centring / alignment | 52 | "the QR code and the details don't really look vertically center" · "title and text and logo … aren't text centered … especially on mobile" |
| Consistency across pages | 48 | "the login, the forgot password … need to be consistent" · "the fonts are inconsistent all over the place" |
| Buttons/clicks that don't work | 44 | "I click on save … and nothing happens" |
| Too much space / tighten | 29 | "there is far too much space between the top of the banner and the callout block" · "the padding around the iframe is far too much" · "give that yellow callout block a bit less padding" |
| Spinner / feedback | 28 | "make the save changes button say 'Saving…' and it's got a spinner in the button and it's disabled" · "that preloader spinner is far too big, have it be next to the wording" · "reload the file browser, but don't show the spinner, just silently reload" · "there is no feedback" |
| Placeholder / fake data | 27 | "the shipping price estimate is fake, remove it" |
| AI-slop / invented copy | 17 | "DONT write AI SLOP SHIT!" (about numbered **01** prose) · "'Field proof beats brochure copy'? what is this copy" |
| Text too small | 16 | "the fonts … are still very small" · "favicon is incredibly small" |
| Too big / too bold | 11 | "It all looks way too bold for the front end" |
| Strong-language frustration (any topic) | 450 | (signals repeat failures; not quoted) |

Projects with most UI corrections: DecisionsAI/DECISIONS (≈265), www.tensology.com (≈260),
SPOORTRACK (71), planmycharter.com (67), thatshirtshow (48), merrypak (39).

### MemPalace (read-only copy of `~/mempalace-backup-20261022/decisions-palace`, mempalace 3.3.3 / chromadb 1.5.9)
8,997 drawers; wing `decisions` has 1,080. Only 20 explicit `UI feedback` drawers (room `orchestrator`,
source `orchestrator_user_memory`); some look like e2e fixtures, so weight them lightly:
- spacing_off ×5: "Keep interface controls dense, avoid excessive vertical padding, and show status beside the affected item." · "The toolbar had too much vertical padding." · "Rows had inconsistent gaps."
- approved ×10: "Dense operational layouts with clear hierarchy." · "Dense table layout worked well." · "Compact controls with clear hierarchy." · "Approved desktop CRUD reference."
- hierarchy_unclear ×2: "Primary action was buried among secondary buttons." / "Keep the primary action visually dominant and show validation status beside the affected item."
- flow_bad ×2: "Always validate the complete responsive flow in Playwright before calling an interface finished." · "It took too many clicks to complete the happy path."
- inconsistent_styling ×1: "Buttons used mismatched visual weight."
No drawers about fonts, colours or modals specifically.

### Shipped code (utility frequency, excluding node_modules/dist/reference)
| Project | Top spacing | Top text | Top radius | Neutrals / accent | Font |
|---|---|---|---|---|---|
| DONE/www.tensology.com (4,882 files) | 2, 4, 3 | sm 2062, xs 939 | lg, sm, md | gray 3287, blue 1124 | Quicksand (`frontend/tailwind.config.js`) |
| planmycharter.com | 2, 3, 4 | sm 4157, xs 2873 | md, lg, full | amber/emerald brand, slate | `--pmc-sans`, Radix |
| DONE/mailshot/client | 2, 3, 4 | sm 87, xs 56 | full, xl, lg | slate 301, blue 119 | Inter |
| DECISIONS/decisions.tensology.com/frontend | 2, 3, 4 | sm 142, xs 125 | full, md, lg | gray, slate | system-ui / Quicksand |
| DONE/ecommerce.tensology.com | 4, 2, 3 | sm, xs | lg, md | gray, blue; `--color-*`/`--font-*` vars | Segoe UI stack |
| DONE/xtract.tensology.com (`src/index.css`) | plain CSS | – | – | `--blue #3b82f6`, `--slate #111827`, `--muted`, `--line #dbe7f5`, `--surface #f8fafc` | Quicksand; `:focus-visible` 3px blue outline |
| Django admin (`white-label-starter/backend/config/settings.py`) | Unfold | – | – | `UNFOLD["COLORS"]` from brand primary | – |

Git commit subjects show hand-tuning toward density and removal: www.tensology.com "Tighten Done tab…",
"Compact incoming navigation tabs", "Compact delivery forecast totals", "Tighten project details header
spacing", "Compact dashboard project panels", "Remove GSD wizard from project tickets UI"; Mailshot
"Refine refresh and sidebar icon spacing", "Unify full-color icon system"; compact/tighten = 26 and
remove/simplify = 48 commits across tensology.com, planmycharter, Mailshot, DecisionsAI.

## 2. External research (secondary; GitHub stars as of 22 Oct 2026)

| Source | Stars | What was taken |
|---|---|---|
| Anthropic skills, `frontend-design`: https://github.com/anthropics/skills | 182.8k | plan tokens before code; list of generated-UI tells (cream+serif, identical cards, ALL-CAPS eyebrows, `01/02/03`); copy rules (verb labels, same verb in toast) |
| ui-ux-pro-max: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill | 138.7k | searchable style/palette/font data; pre-delivery checklist idea |
| shadcn/ui: https://github.com/shadcn-ui/ui | 126.2k | semantic CSS-variable roles (`--primary`, `--ring`, `--radius`), Dialog/Sheet/AlertDialog split |
| taste-skill: https://github.com/Leonxlnx/taste-skill | 100.8k | anti-default "taste" prompting; we keep only verifiable parts |
| impeccable: https://github.com/pbakaus/impeccable | 84.6k | product modes (Operate/Persuade/Read), CRUD management page rules, audit/critique split |
| Radix Primitives: https://github.com/radix-ui/primitives | 19.4k | dialog/menu keyboard + focus contracts |
| Vercel Web Interface Guidelines: https://github.com/vercel-labs/web-interface-guidelines | 1.0k | concrete lint-able rules: focus-visible, `aria-live`, no `transition: all`, `…`, tabular-nums, URL state, destructive needs confirm or undo, submit enabled until request starts |
| Rauno Freiberg, interfaces: https://github.com/raunofreiberg/interfaces | 2.0k | interaction details (interruptible motion, hit areas) |
| WCAG 2.2: https://www.w3.org/TR/WCAG22/ | – | 1.4.3 (4.5:1), 1.4.11 (3:1 non-text), 2.4.7 focus visible, 2.4.11 focus not obscured, 2.5.8 target size 24px, 3.3.1/3.3.3 error identification/suggestion, 3.3.7 redundant entry |
| NN/g form error guidelines: https://www.nngroup.com/articles/errors-forms-design-guidelines/ | – | inline validation after field completion, errors next to fields, summary never the only indicator, no tooltip errors |
| Nielsen's 10 heuristics: https://www.nngroup.com/articles/ten-usability-heuristics/ | – | names used in Interaction rationale |
| Laws of UX: https://lawsofux.com/ | – | Fitts, Hick, Jakob, Miller, Doherty (≤400ms), Tesler |
| Material 3 tokens/motion: https://m3.material.io/foundations/design-tokens and https://m3.material.io/styles/motion/easing-and-duration | – | role-based colour tokens; duration bands short 50–200ms / medium 250–400ms |
| Apple HIG: https://developer.apple.com/design/human-interface-guidelines/ (local copy: `apple-design` skill) | – | 44pt targets, feedback for every action, clear hierarchy, modality only when needed |
| Refactoring UI: https://www.refactoringui.com/ | – | constrained spacing/type scales, hierarchy via weight/colour not size alone, fewer borders, emphasise by de-emphasising |

### What proved concrete vs vague
Concrete (kept): numeric scales, contrast ratios, state lists, validation timing, container decision
table, focus/dialog contracts, lint-able anti-patterns, verb-label copy rules, rendering at named widths.
Vague (dropped or turned into a check): "beautiful", "distinctive", "premium", "delightful", "bold
aesthetic direction", "make it pop". The aesthetic-direction advice in frontend-design/taste skills is
for Persuade mode and is not applied to Operate screens by default.

### Taste log (authoritative for Paul's settled preferences)
`references/taste-log.md` (moved here from `ui-ux-principles/taste-log.md` on 22 Oct 2026): dated
Did / Wanted / Why entries from Paul's corrections. Settled rules 2a.1 to 2a.7 in `SKILL.md` cite it:
border-embedded and left-of-control labels (2026-10-18, 2026-10-19, 2026-10-20, 2026-10-21), top-centre
snackbar (2026-10-19), solid blue selected nav (2026-10-19), borderless shadowed menus (2026-10-19),
centred pink auth error callout (2026-10-18), Add itinerary beneath table (2026-10-19), marquee long
titles (2026-10-15). Where the log and a generic default disagree, the log wins.

## 3. Local skills reused
`ui-ux-principles` (fixed CRUD flow, no duplication/bloat, Definition of Done, preserve product truth,
theme completeness, coverage matrix, taste-memory loop; now a redirect here), `compact-ui-decisions`
(grouping by meaning, inline icons, hover-revealed row actions, duplication pass; archived 22 Oct 2026),
`ui-crud-discipline` (people naming, admin scope, fieldsets, email/notifications; merged into crud-flows.md), `impeccable/reference/crud-management.md` (destructive dialog
focus, Actions column), `frontend-design` (AI tells, copy), `web-design-guidelines` (Vercel rules),
`apple-design` (feedback, targets).
