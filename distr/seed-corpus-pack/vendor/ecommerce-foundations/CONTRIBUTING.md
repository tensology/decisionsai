# Contributing to ecommerce-foundations

Thanks for considering a contribution. This repository is a **knowledge package**, not a code package. Every contribution is markdown text plus metadata files. There are no scripts, no binaries, no SDKs.

Before opening a PR, read [`CLAUDE.md`](./CLAUDE.md) in full — it documents the operational rules for this repo (hard rules, design principles, anti-patterns). What follows is a quick contributor-facing summary.

## Skill authoring conventions

Every skill is a directory under `skills/` containing a single `SKILL.md` file with YAML frontmatter followed by markdown content.

```text
skills/
└── <skill-name>/
    └── SKILL.md
```

### Required rules

1. **The directory name must equal the `name` in the frontmatter.** No exceptions — the validator enforces this and `skills.sh` indexing depends on it.
2. **Kebab-case** for the skill directory name and the frontmatter `name`.
3. **English only** in the body, headings, and frontmatter. The author may discuss the work in any language; the deliverable is still English.
4. **Frontmatter must include `name` and `description`.** The description must include concrete triggers (keywords, task types, or file patterns) so an agent can decide when to load the skill.
5. **No executable code in skills.** No `scripts/`, no `tools/`, no binaries.

### Frontmatter template

```yaml
---
name: skill-name-in-kebab-case
description: One sentence describing when an agent should load this skill. Must include concrete triggers — keywords, task types, or file patterns.
---
```

### Content structure

Every skill follows the template documented in [`CLAUDE.md`](./CLAUDE.md#skill-authoring-template):

- **Vocabulary** — define every term before using it.
- **Canonical model** — present as one possibility, not the truth.
- **Variation dimensions** — at least business model, product type, geography, customer model.
- **Decision frameworks** — options, trade-offs, criteria for choosing. Never pick the answer.
- **Anti-patterns** — common mistakes with the failure mode each one causes.
- **Sources** — real, resolvable URLs only.

The three design principles every skill must satisfy:

1. **Generic in method, not in answers.** Describe how to think about a problem; do not prescribe the answer.
2. **Vocabulary first.** Define terms before using them.
3. **Variation dimensions are explicit.** Name the axes along which the pattern adapts.

### What does not belong here

- **Platform-specific recommendations as the default answer.** Skills describe the space of options and the criteria for choosing — they never say "use Stripe" or "use Medusa's module pattern."
- **Business-specific knowledge.** Pharmacy, marketplace, subscriptions, B2B quotes belong in vertical packs (separate repositories), not in this core layer.
- **Fabricated sources.** Every non-trivial claim must link to a real, verifiable URL. If a claim has no source, mark it as inferred or remove it.

See [`SOURCES.md`](./SOURCES.md) for the curated reference bibliography. Cite by URL, not by reputation.

## Local development

You can load the plugin locally without publishing it. From the parent directory:

```bash
claude --plugin-dir ./ecommerce-foundations
```

When you make changes to a skill, run `/reload-plugins` inside Claude Code to pick up the update without restarting.

## Validation

Run validation before opening a PR. CI runs a manual JSON + YAML validator on every push and PR (see `.github/workflows/validate.yml`). To reproduce locally:

```bash
# Validate plugin manifest and marketplace JSON
python3 -c "import json; json.load(open('.claude-plugin/plugin.json'))"
python3 -c "import json; json.load(open('.claude-plugin/marketplace.json'))"

# Validate skill frontmatter (the CI workflow inlines this check —
# see `.github/workflows/validate.yml` for the exact script)
```

If you have the Claude Code CLI available locally and authenticated, you can also run:

```bash
claude plugin validate .
```

The CI workflow uses the manual validator because the Claude Code CLI requires authentication that is not safe to expose in public CI.

## Versioning

The plugin follows semantic versioning. See `version` in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` — both must be bumped together.

- **Patch** (`0.x.y` → `0.x.(y+1)`): typo fixes, clarifications, minor additions that do not change conclusions.
- **Minor** (`0.x` → `0.(x+1)`): new skills added; existing skills extended without breaking changes.
- **Major** (`x.0.0`): skill renamed, removed, split, or its conclusions changed. Migration notes required in the release.

Within a minor version, skill names, frontmatter contracts, and the directory layout do not change.

## Pull request checklist

Before opening a PR, verify:

- [ ] Every new term appears in the Vocabulary section before use.
- [ ] The skill frames a decision space; it does not prescribe an answer.
- [ ] All citations are real, resolvable URLs.
- [ ] The README roadmap table is updated if status changed.
- [ ] `version` is bumped in both `plugin.json` and `marketplace.json`.
- [ ] CI validation passes (or the equivalent local check).
- [ ] All content is English-only.
- [ ] The skill directory name is identical to the frontmatter `name`.

## Reporting issues

Open an issue describing the symptom, the skill (if applicable), and a minimal repro. For knowledge issues — disputed terminology, missing variation dimension, broken citation — link the specific paragraph and propose an alternative.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](./LICENSE).
