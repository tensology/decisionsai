# CLAUDE.md

Operational guidance for AI coding agents (Claude Code, Cursor, Codex, and any other agent) working on this repository. Read this in full before making any change.

## What this repo is

`ecommerce-foundations` is an Agent Skills package distributed via the Claude Code Plugin Marketplace and skills.sh. Each skill is a single `SKILL.md` file under `skills/<skill-name>/`, containing YAML frontmatter and markdown content.

**This repo contains knowledge, not code.** There are no executable scripts, no binaries, no SDKs. Every contribution is markdown text plus metadata files (`plugin.json`, `marketplace.json`, CI config).

If you find yourself about to write a Python script, a shell helper, or a JavaScript tool — stop. That belongs in a different kind of repo.

## Hard rules

Non-negotiable. If a request asks you to violate one, push back and clarify intent before proceeding.

1. **No executable code in skills.** No `scripts/`, no `tools/`, no binaries. Pure markdown with YAML frontmatter.
2. **No platform-specific recommendations as the default answer.** Skills never say "use Stripe" or "use Medusa's module pattern." They describe the *space of options* and the *criteria for choosing*.
3. **No fabricated sources.** Citations must point to real, verifiable URLs. If a claim has no source, mark it as inferred or remove it. Never write "as Stripe recommends" without linking.
4. **No business-specific knowledge in the core layer.** Pharmacy, marketplace, subscriptions, B2B quotes belong in vertical packs (separate repositories), not here.
5. **English only in skill content.** Frontmatter, headings, body — all English. The author may communicate with you in Spanish; the deliverable is still English.
6. **Skill directory name must equal the `name` in frontmatter.** Always. The validator enforces this and skills.sh indexing depends on it.
7. **Kebab-case everywhere** the schema validates it: skill names, plugin name, marketplace name.

## Skill authoring template

Every skill follows this structure. Deviations require explicit justification.

```markdown
---
name: skill-name-in-kebab-case
description: One sentence describing when an agent should load this skill. Must include concrete triggers — keywords, task types, or file patterns.
---

# Skill Title

## Vocabulary

Define every term before using it. If you use "reservation," define it.
If you use "channel," define it. Disambiguate terms that are commonly
confused — "on-hand" vs "available" vs "reserved" vs "committed" are
not synonyms.

## Canonical model

The default mental model. Present it as one possibility, not as the
truth. Make explicit which assumptions it depends on.

## Variation dimensions

The axes along which this pattern adapts. Always include at least:
- Business model (B2C / B2B / marketplace / subscriptions / hybrid)
- Product type (physical / digital / service / mixed)
- Geography (single-region / multi-region)
- Customer model (guest / account-required / B2B hierarchy)

## Decision frameworks

For each major decision the agent will face, present the options,
their trade-offs, and the criteria for choosing. Never pick the
answer for the agent.

## Anti-patterns

Common mistakes with the specific failure mode each one causes
(oversell, double-charge, lost order, race condition, etc.).

## Sources

Real URLs only.
```

## The three design principles

Every contribution must satisfy all three. Reject changes that violate them.

1. **Generic in method, not in answers.** A skill describes *how to think* about a problem and *choose* between options. It does not prescribe the answer.
2. **Vocabulary first.** A skill defines its terms before using them. Loose terminology is treated as a defect.
3. **Variation dimensions are explicit.** Every skill names the axes along which the pattern adapts.

## Workflow when asked to add or modify a skill

1. **Confirm scope.** If the request is vertical-specific (pharmacy, marketplace, subscriptions, etc.), redirect to the appropriate vertical pack repo — do not add it here.
2. **Locate the existing skill** if it exists. Path: `skills/<name>/SKILL.md`.
3. **Read related skills first** to maintain vocabulary consistency. If `inventory-management` defines "reserved," `order-lifecycle` must use the same definition.
4. **Verify before stating.** Before adding a non-trivial pattern, check it against the trusted sources (below). If you cannot verify, either mark the claim as inferred or remove it.
5. **Run validation:** `claude plugin validate .` from the repo root. If the CLI isn't available in the environment, validate JSON/YAML manually against the documented schema.
6. **Update the README roadmap table** if status changed.
7. **Bump version** in `plugin.json` and `marketplace.json` per semver — see "Versioning."

## File structure

```
ecommerce-foundations/
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── skills/
│   └── <skill-name>/
│       └── SKILL.md
├── .github/workflows/validate.yml
├── CLAUDE.md          # this file
├── CONTRIBUTING.md
├── LICENSE
└── README.md
```

Do not create other top-level directories without discussion. Skills do not have sub-skills — the layout is flat.

## Versioning conventions

Semver applied to knowledge content:

- **Patch (`0.x.y` → `0.x.(y+1)`)**: typo fixes, clarifications, minor additions that don't change conclusions.
- **Minor (`0.x` → `0.(x+1)`)**: new skills added; existing skills extended without breaking changes.
- **Major (`x.0.0`)**: skill renamed, removed, split, or its conclusions changed. Migration notes required in the release.

Within a minor version, skill names, frontmatter contracts, and directory layout never change.

## Trusted sources

When researching a topic, cite these by URL — not by reputation alone.

- Medusa: https://docs.medusajs.com/
- Sylius: https://docs.sylius.com/
- Stripe: https://docs.stripe.com/
- Shopify: https://shopify.dev/docs
- commercetools: https://docs.commercetools.com/
- Spryker: https://docs.spryker.com/

For DDD foundations, reference Evans's *Domain-Driven Design* and Vernon's *Implementing Domain-Driven Design* by chapter, not page number (editions vary).

## Anti-patterns specific to this repo

Watch for these. They are easy to slip into.

- **The "right answer" trap** — writing "you should always reserve stock at cart" instead of describing when each option applies.
- **Platform leakage** — using Medusa-specific or Stripe-specific terminology as if it were universal vocabulary.
- **Vocabulary drift** — using "stock," "inventory," and "on-hand" interchangeably inside the same skill.
- **Citation by reputation** — "as Stripe recommends" without a link to which page.
- **Vertical creep** — adding pharmacy or marketplace specifics to a core skill instead of a vertical pack.
- **Length without density** — a skill is not better because it's longer. Cut every sentence that doesn't introduce a decision, a definition, or a trade-off.

## When the request is ambiguous

Ask before generating. The cost of regenerating a skill is much higher than the cost of one clarifying question. Common ambiguities worth surfacing:

- Is this for the core layer or a vertical pack?
- Which business model is the asker thinking of (B2C, B2B, marketplace)?
- Is "checkout" being used to mean the full flow or just the payment step?
- Does the new content add a section or replace one?

## When asked to do something this file forbids

Push back and reference the specific rule. The author may have forgotten the convention; reminding them is helpful. If after discussion the convention itself should change, update this file in the same change — don't bypass it silently.

## When asked to generate code

This is a knowledge repository. Code generation requests are almost always misrouted. Confirm whether the user wants:

- (a) Content for a skill describing a pattern (correct fit — write markdown).
- (b) An actual implementation in their own codebase (wrong fit — redirect them to that codebase).
- (c) A CI helper or validator script for this repo (acceptable, but propose first and keep it minimal).

## Self-check before opening a PR

- [ ] Does every new term appear in the Vocabulary section before use?
- [ ] Does the skill name "answers" or "frameworks"? It should frame, not prescribe.
- [ ] Are all citations real URLs?
- [ ] Did I update the README roadmap if status changed?
- [ ] Did I bump `version` in `plugin.json` and `marketplace.json` appropriately?
- [ ] Does `claude plugin validate .` pass?
- [ ] Is the content English-only?
- [ ] Is the skill directory name identical to the frontmatter `name`?