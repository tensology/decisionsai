# Changelog

All notable changes to `ecommerce-foundations` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html), applied to knowledge content:

- **Patch** (`0.x.y` → `0.x.(y+1)`): typo fixes, clarifications, minor additions that do not change conclusions.
- **Minor** (`0.x` → `0.(x+1)`): new skills added; existing skills extended without breaking changes.
- **Major** (`x.0.0`): skill renamed, removed, split, or its conclusions changed. Migration notes required.

---

## [0.1.3] — 2026-05-19

### Added

- `CHANGELOG.md` (this file).
- `.github/ISSUE_TEMPLATE/` with form templates for bug reports, skill suggestions, and broken citations.
- `.github/PULL_REQUEST_TEMPLATE.md` mirroring the contributor checklist.
- README badges for license and CI validation status.
- GitHub repository metadata: description, topics, homepage.

### Removed

- `skills/.gitkeep` (obsolete now that skills exist).

### Changed

- Repository SSH and identity hygiene clarified (operational; not user-facing). Working tree now uses `git@github.com:oa2p-solutions/ecommerce-foundations.git` with the `arochaoscar` identity by default.

---

## [0.1.2] — 2026-05-19

### Added

- **Practitioner perspectives** section in `SOURCES.md` framing retailer engineering blogs (Amazon — All Things Distributed, Walmart Global Tech, Mercado Libre, Shopify Engineering, Stripe blog, DoorDash, Uber, Etsy, Zalando, eBay, Wayfair, Target, Coupang, Booking.com), Amazon-school practices (Bezos shareholder letters, Werner Vogels essays, ACM Queue), and commerce-strategy commentators (Stratechery, Andrew Chen, Bill Gurley, a16z, Reforge).
- **Industry analysts and frameworks** section: MACH Alliance (composable commerce), NRF + ARTS retail data model, eMarketer / Insider Intelligence, Forrester, Gartner, McKinsey Retail, BCG, IDC, Retail Dive, Digital Commerce 360. Subsection on composable / headless commerce literature.
- **Books and long-form references** section: *Working Backwards*, *The Everything Store*, *Amazon Unbound*, *Delivering Happiness*, *Shoe Dog*, *The Lean Startup*, *Hooked*, *The Innovator's Dilemma*, *Don't Make Me Think*, *Hit Refresh*, *Designing Data-Intensive Applications* (Kleppmann), *Building Microservices* (Newman), *Release It!* (Nygard), *DDD Distilled* (Vernon).
- Per-skill practitioner references: each of the 9 skills now cites 3–8 relevant blog posts, engineering teams, or books in addition to vendor docs and cloud-provider references.

### Notes

- Total: ~3,000 lines of skill + reference material.
- Always-on token cost: ~1,477 tokens per session (unchanged from 0.1.0).

---

## [0.1.1] — 2026-05-19

### Added

- **Cloud-provider architecture references** section in `SOURCES.md` covering AWS (Builders' Library, Prescriptive Guidance, retail architectures, Step Functions, Well-Architected), Azure Architecture Center (cloud design patterns, saga, cache-aside, materialized view, sequential convoy, async request-reply, circuit breaker), Alibaba Cloud (Architecture Center, retail solutions, blog), Google Cloud retail, and Cloudflare.
- **Engineering-blog references (vetted)** section with editorial-reviewed sources.
- **Cross-platform vocabulary mapping** table in `domain-vocabulary` skill cross-referencing Stripe / Shopify / commercetools / AWS / Alibaba terminology — including the Shopify "available" vs canonical "on_hand" divergence.
- **Catalog at scale** section in `catalog-and-product-modeling`: browse vs search authority, read-through caching, faceted search index design.
- **Dynamic and real-time pricing** section in `pricing-and-channels`: editor-managed / rules-based / model-driven, audit trail, stale-cart contract.
- **High-concurrency decrement patterns** section in `inventory-management`: pre-allocated stock pool, token-based admission, materialized-view inventory.
- **Cart storage and caching** section in `cart-lifecycle`: direct key-value, cache-aside, edge-resident.
- **Orchestrating side effects across systems** section in `order-lifecycle`: orchestrated vs choreographed saga, compensation as forward action.
- **Regional payment methods** section in `payment-flows` with comparison table for US, EU, UK, Brazil, Mexico, LATAM, China, India, SE Asia, Africa.
- **Pattern catalog cross-references** section in `idempotency`: idempotent receiver/consumer, transactional outbox, inbox table, sequential convoy, compensating transaction.
- **Webhook delivery and fan-out at scale** section in `webhooks`: per-endpoint queues, circuit breaker, pub/sub fan-out, two-stage receiver, inbox table, backstop polling.

### Notes

- All sources back-filled into per-skill blocks in `SOURCES.md` per the maintenance rule.
- No changes to skill names, frontmatter contracts, or directory layout.

---

## [0.1.0] — 2026-05-17

### Added

Initial release. Nine skills authored covering the core and cross-cutting layers:

**Core layer:**

- `domain-vocabulary` — authoritative definitions for cart, quote, order, line item, on-hand, available, reserved, committed, channel, customer, account, variant, SKU, authorization, capture, fulfillment, shipment, and other foundational terms.
- `catalog-and-product-modeling` — product vs variant (SKU), categories/taxonomies, attributes vs options, media, slugs, SEO.
- `pricing-and-channels` — list price vs sale price, price lists per channel/customer-group/region, multi-currency, tax-inclusive vs exclusive, tiered pricing, Money pattern.
- `inventory-management` — four-state model (on-hand / committed / reserved / available), multi-location, reservation timing options, oversell policies, safety stock.
- `cart-lifecycle` — anonymous vs authenticated carts, merge-on-login, price/stock revalidation, abandonment, cart state machine.
- `order-lifecycle` — orthogonal state machines for payment and fulfillment, side-effect contracts per transition, cancellation and refund branches.
- `payment-flows` — authorization vs capture vs sale, hosted vs embedded checkout, webhook handling, refunds, disputes and chargebacks, tokenization and PCI scope.

**Cross-cutting:**

- `idempotency` — idempotency keys, replay windows, client-supplied vs domain-derived keys, concurrent-replay handling.
- `webhooks` — signature verification, retry policies, at-least-once delivery, dead-letter handling, Standard Webhooks spec.

### Infrastructure

- `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` manifests.
- `.github/workflows/validate.yml` CI workflow validating JSON manifests, skill frontmatter, kebab-case names, and dir-name / frontmatter-name match.
- `CLAUDE.md` operational guidance for AI agents working on the repository.
- `CONTRIBUTING.md` contributor-facing summary.
- `SOURCES.md` curated reference bibliography (primary, foundational, per-skill).
- `README.md`, `LICENSE` (MIT), `TODO.md` publication roadmap.

<!--
Versions 0.1.0–0.1.2 predate the first git tag; the repository was
initialized at the 0.1.2 state and 0.1.3 is the first tagged release.
Their entries above are kept for narrative history.
-->

[0.1.3]: https://github.com/oa2p-solutions/ecommerce-foundations/releases/tag/v0.1.3
