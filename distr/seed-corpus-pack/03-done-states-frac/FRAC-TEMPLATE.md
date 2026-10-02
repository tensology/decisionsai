# FRAC template — SYNTHESIZED

Combine: ecc Plan-PRD pattern, mattpocock to-prd, writing-plans bite-sized tasks, Merrypak planning numbers (23–30), verification-before-completion.

```markdown
# FRAC: <Feature name>

## F — Feature
One sentence: who + what capability + why.
Out of scope: bullet list.

## R — Requirements
Numbered, testable, domain vocabulary from `01-entity-domain/CONTEXT.md`.
1. …
Non-functional: performance, security, i18n, a11y as needed.

## AC — Acceptance Criteria
Given / When / Then (or checklist). Each AC maps to a verification command or QA tier.
- [ ] AC1: …
- [ ] AC2: …

## Done-state
What "done" means for this flow (see DONE-STATES.md). Not "code merged" alone.

## Verify loop
1. Write/adjust failing test or QA script for AC.
2. Implement minimal change.
3. Run verification command fresh (verification-before-completion).
4. Evidence: paste command + exit + key output into ship note.
5. If fail → fix or reopen FRAC; never claim done on vibes.

## Seams / modules
Highest-level test seam; files to touch (from writing-plans).

## Links
PRD path · plan path · tickets · related ADR
```

### Staging files (ecc)
`.claude/prds/X.prd.md` → `.claude/plans/X.plan.md` → TDD → `/pr`

### Agent boot
Load this template + DONE-STATES + `04-verify-feedback-loops` before implementing.
