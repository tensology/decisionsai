<!--
Thanks for the contribution. Please complete the sections below before requesting review.
The checklist mirrors the one in CONTRIBUTING.md; CI will catch most structural issues automatically.
-->

## Summary

<!-- One sentence: what changes and why. -->

## Scope

<!-- Mark the boxes that apply. -->

- [ ] New skill
- [ ] Existing skill extended (no breaking change)
- [ ] Skill renamed, removed, split, or conclusions changed (**major** bump required)
- [ ] Documentation only (README, CONTRIBUTING, CLAUDE.md, SOURCES.md, CHANGELOG)
- [ ] Plugin manifest / marketplace manifest / CI

## Skill(s) affected

<!-- List the kebab-case skill names, e.g. order-lifecycle, payment-flows. Use "n/a" if none. -->

## Sources consulted

<!-- Two or more primary sources per the triangulation rule in SOURCES.md. Link by URL. -->

-
-

## Checklist

- [ ] Every new term appears in the Vocabulary section before use.
- [ ] The skill frames a decision space; it does not prescribe a single answer.
- [ ] No platform-specific defaults (no "use Stripe", "use Medusa", etc.) outside the per-platform mapping sections.
- [ ] No vertical-specific content (pharmacy, marketplace, subscriptions, etc.).
- [ ] All citations link to real, resolvable URLs.
- [ ] The README roadmap table is updated if status changed.
- [ ] `version` bumped in both `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` per the semver rules in `CHANGELOG.md`.
- [ ] `CHANGELOG.md` updated under the new version heading.
- [ ] CI validation passes (or the equivalent local check: `claude plugin validate .`).
- [ ] Content is English-only.
- [ ] Skill directory name equals the frontmatter `name` (kebab-case).
