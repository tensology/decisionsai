# ecommerce-foundations

> Platform-agnostic ecommerce domain knowledge for AI coding agents.

[![Validate](https://github.com/oa2p-solutions/ecommerce-foundations/actions/workflows/validate.yml/badge.svg)](https://github.com/oa2p-solutions/ecommerce-foundations/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-blue)](https://www.skills.sh/)
[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-Plugin-7B61FF)](https://docs.claude.com/en/docs/claude-code)

**Status:** `v0.1.3` — Core and cross-cutting skills authored; enriched with cloud-provider, practitioner (Amazon, Walmart, Mercado Libre, Shopify, Stripe, DoorDash, Zalando), commerce-strategy commentator, and book references. Contributor templates and changelog in place; preparing for marketplace submission.

---

## What this is

`ecommerce-foundations` is an [Agent Skills](https://www.skills.sh/) package that gives AI coding agents — [Claude Code](https://docs.claude.com/en/docs/claude-code), Cursor, Codex, Gemini CLI, and any other `SKILL.md`-compatible agent — a working understanding of how ecommerce systems are designed.

It is **knowledge, not code.** The package contains no executable scripts and no platform-specific implementations. Each skill captures one domain area — order lifecycle, inventory management, payment flows, etc. — as a structured reference the agent consults while working on any ecommerce codebase.

Whether the codebase is built on NestJS, Django, Rails, Medusa, Shopify, or something custom, the conceptual foundations are the same. This package extracts and documents them.

## Why this exists

Most ecommerce knowledge available to AI agents is implicit, scattered across training data, or coupled to a specific platform — Shopify's API surface, Medusa's module conventions, Stripe's flows. When an agent works on a custom ecommerce system, that coupling becomes friction: it suggests patterns from the platform it knows best, not patterns that fit the system in front of it.

This package separates the *what* (universal domain concepts) from the *how* (implementation details that depend on stack and business). Agents loading these skills can reason about commerce problems first, then map to whatever framework the codebase uses.

## Design philosophy

Three principles guide every skill:

1. **Generic in method, not in answers.** A skill on inventory does not say "decrement stock at payment confirmation." It enumerates the moments where stock can be decremented, the trade-offs of each, and the criteria for choosing — so the agent can apply the right one for the business at hand.

2. **Vocabulary first.** Loose terminology is the source of most commerce design bugs. Each skill defines its terms authoritatively before using them. "On-hand," "available," "reserved," and "committed" are not synonyms, and the package never treats them as such.

3. **Variation dimensions are explicit.** Every skill includes a section listing the axes along which the pattern adapts — B2C vs B2B vs marketplace, physical vs digital vs services, single-region vs multi-region, guest checkout vs account-required. The agent uses these to localize the knowledge to the project.

## Installation

### Claude Code Plugin Marketplace

From inside Claude Code:

```
/plugin marketplace add oa2p-solutions/ecommerce-foundations
/plugin install ecommerce-foundations@ecommerce-foundations
```

The skills will load automatically when the agent detects a commerce-related task.

### skills.sh

From your terminal, in any project:

```bash
npx skills add oa2p-solutions/ecommerce-foundations
```

By default this installs to `.claude/skills/` (project-local). For global installation across all projects, install into `~/.claude/skills/`.

### Other agents (Cursor, Codex, Gemini CLI, etc.)

Any agent that reads the [SKILL.md](https://www.skills.sh/) standard can use these skills. Clone the repo or copy the `skills/` directory into the location your agent expects.

## What's inside

The package is structured in two layers, with vertical-specific packs planned as separate releases.

### Layer 1 — Core (universal)

Domain knowledge that applies to every ecommerce system, regardless of business model.

| Skill | Description | Status |
|:---|:---|:---|
| `domain-vocabulary` | Authoritative definitions for cart, quote, order, line item, on-hand, available, reserved, committed, and the rest. The foundation other skills build on. | Available (v0.1) |
| `catalog-and-product-modeling` | Product vs variant (SKU), categories and taxonomies, attributes and features, media, slugs and SEO. | Available (v0.1) |
| `pricing-and-channels` | List price vs sale price, price lists per channel, multi-currency, tax-inclusive vs exclusive, tiered pricing. | Available (v0.1) |
| `inventory-management` | Stock states, multi-location inventory, reservation timing options, oversell policies, safety stock. | Available (v0.1) |
| `cart-lifecycle` | Anonymous vs authenticated carts, merge-on-login, price and stock revalidation, abandonment, cart state machine. | Available (v0.1) |
| `order-lifecycle` | Canonical order state machine, allowed transitions, side effects per transition, cancellation and refund branches. | Available (v0.1) |
| `payment-flows` | Authorization vs capture vs sale, hosted vs embedded checkout, webhook handling, refunds (full and partial), disputes and chargebacks. | Available (v0.1) |
| `checkout-flow` | Step structure (address → shipping → payment → review), guest vs registered, tax calculation timing, quote-to-order conversion. | 🚧 Planned (v0.2) |
| `fulfillment-and-shipping` | Carrier selection, rate calculation, tracking, split shipments, pick-pack-ship workflow. | 🚧 Planned (v0.2) |
| `returns-and-rmas` | Return reasons, restocking decisions, refund flows, exchanges. | 🚧 Planned (v0.2) |
| `promotions-and-discounts` | Coupon codes, auto-applied promotions, stacking rules, conditions. | 🚧 Planned (v0.2) |
| `customer-accounts` | Account model, addresses, order history, wishlists, B2B account hierarchies. | 🚧 Planned (v0.2) |
| `taxes` | Tax-inclusive vs exclusive pricing, calculation timing, jurisdictional rules, tax-exempt products. | 🚧 Planned (v0.2) |

### Layer 2 — Cross-cutting

Patterns and concerns that span multiple commerce areas.

| Skill | Description | Status |
|:---|:---|:---|
| `idempotency` | Idempotency keys, where to apply them, invalidation, replay safety. | Available (v0.1) |
| `webhooks` | Signature verification, retries, exactly-once vs at-least-once semantics, dead-letter handling. | Available (v0.1) |
| `saga-compensation` | Orchestrated sagas, compensation functions, retry policies, distributed-transaction patterns. | 🚧 Planned (v0.2) |
| `domain-events` | Event design, event vs command, eventual consistency, audit trail. | 🚧 Planned (v0.2) |

### Vertical packs (future, separate repositories)

Domain-specific extensions built on top of this core. Each will live in its own repository and depend on `ecommerce-foundations`.

- `pharmacy-cl` — Chilean pharmacy regulation (prescriptions, controlled substances, ISP, Cenabast, QF oversight).
- `marketplace` — multi-vendor marketplaces, commissions, payouts, dispute mediation.
- `subscriptions` — recurring billing, dunning, plan changes, proration.
- `food-delivery` — delivery windows, geofencing, freshness constraints.
- `b2b-quotes` — quote-to-order workflows, approval chains, account hierarchies.

## Repository structure

```
ecommerce-foundations/
├── .claude-plugin/
│   ├── plugin.json          # Plugin manifest
│   └── marketplace.json     # Marketplace catalog
├── skills/
│   ├── <skill-name>/
│   │   └── SKILL.md         # YAML frontmatter + knowledge content
│   └── ...
├── .github/
│   └── workflows/
│       └── validate.yml     # CI validation on every push and PR
├── CONTRIBUTING.md
├── LICENSE
└── README.md
```

Each skill is a directory containing a single `SKILL.md` with YAML frontmatter (`name`, `description`) followed by the knowledge content in markdown.

## Sources and intellectual lineage

Skills in this package distill knowledge from well-established sources rather than inventing it. Where a pattern comes from a specific source, it is cited inside the skill. Primary references:

- [Medusa documentation](https://docs.medusajs.com/) — open-source headless commerce; one of the best-written architecture references for modern commerce primitives.
- [Sylius documentation](https://docs.sylius.com/) — particularly strong for checkout and order state machines.
- [Stripe documentation](https://docs.stripe.com/) — de-facto standard for payment flows, idempotency, webhooks, refunds, disputes.
- [Shopify developer documentation](https://shopify.dev/docs) — fulfillment, returns, multi-store patterns.
- [commercetools](https://docs.commercetools.com/) and [Spryker](https://docs.spryker.com/) — enterprise B2B, multi-channel, multi-tenant patterns.
- *Domain-Driven Design* (Eric Evans) and *Implementing Domain-Driven Design* (Vaughn Vernon) — commerce is the canonical DDD example; the bounded contexts here map roughly to Vernon's commerce examples.

## Versioning

Semantic versioning.

- **Patch** (`0.x.y` → `0.x.(y+1)`): content refinements, typo fixes, clarifications.
- **Minor** (`0.x` → `0.(x+1)`): new skills added; existing skills extended without breaking changes.
- **Major** (`x.0.0`): skills renamed, split, merged, or removed. Migration notes accompany every major release.

Within a minor version, skill names, frontmatter contracts, and the directory layout will not change.

## Contributing

Contributions welcome — see [`CONTRIBUTING.md`](./CONTRIBUTING.md).

In short:

- Each skill is a directory under `skills/` containing one `SKILL.md`.
- Content in English.
- Frontmatter must include `name` and `description`. The directory name must match the `name` in the frontmatter.
- No executable scripts. Knowledge only.
- Run `claude plugin validate .` (or the equivalent JSON/YAML validator if the CLI is unavailable) before opening a PR.

## License

[MIT](./LICENSE) © 2026 Oscar Arocha — see `LICENSE` for the full text.

## Acknowledgements

Built with [Claude Code](https://docs.claude.com/en/docs/claude-code), inspired by the broader Agent Skills ecosystem, and indebted to the open-source commerce projects whose documentation made this package possible.