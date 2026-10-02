# TODO — Road to v0.1.0 publication

Tracking checklist for getting `ecommerce-foundations` from scaffolded repo to its first published release. Items are grouped by phase; phases are sequential, items within a phase can be parallelized unless noted.

**Current state:** all 9 v0.1 skills authored under `skills/`. Consistency pass complete. README roadmap updated. `claude plugin validate .` passes locally. Pending: open release PR, tag, publish to marketplaces.

**Definition of done for v0.1.0:** all skills tagged "Planned (v0.1)" in the README roadmap are authored, validated, cross-referenced, and the plugin is installable via the Claude Code Plugin Marketplace and `skills.sh`.

---

## Phase 1 — Author v0.1 skills

Nine skills total. Each is independent in scope but must use consistent vocabulary, so author `domain-vocabulary` first and treat it as the canonical glossary for the rest.

### Foundation (blocking for all other skills)

- [x] **`domain-vocabulary`** — authoritative definitions for cart, quote, order, line item, on-hand, available, reserved, committed, channel, fulfillment, capture, authorization, etc. Every other skill imports its terms from here.

### Core layer

- [x] **`catalog-and-product-modeling`** — product vs variant (SKU), categories/taxonomies, attributes, media, slugs, SEO.
- [x] **`pricing-and-channels`** — list vs sale price, price lists per channel, multi-currency, tax-inclusive vs exclusive, tiered pricing.
- [x] **`inventory-management`** — stock states, multi-location, reservation timing options, oversell policies, safety stock.
- [x] **`cart-lifecycle`** — anonymous vs authenticated carts, merge-on-login, price/stock revalidation, abandonment, cart state machine.
- [x] **`order-lifecycle`** — canonical state machine, allowed transitions, side effects per transition, cancellation and refund branches.
- [x] **`payment-flows`** — authorization vs capture vs sale, hosted vs embedded checkout, webhook handling, refunds (full/partial), disputes and chargebacks.

### Cross-cutting

- [x] **`idempotency`** — idempotency keys, where to apply, invalidation, replay safety.
- [x] **`webhooks`** — signature verification, retries, exactly-once vs at-least-once, dead-letter handling. Reference Standard Webhooks spec.

### Authoring checklist (apply per skill)

For each skill above:

- [x] Read related skills first to align vocabulary.
- [x] Triangulate against at least two primary sources from `SOURCES.md`.
- [x] Draft following the template in `CLAUDE.md` (Vocabulary → Canonical model → Variation dimensions → Decision frameworks → Anti-patterns → Sources).
- [x] Every non-trivial claim links to a real URL or is marked as inferred.
- [x] Directory name matches `name` in frontmatter; both are kebab-case.
- [x] No platform-specific recommendations as the default answer.
- [x] No vertical-specific content (pharmacy, marketplace, etc.).
- [x] Run the validation workflow locally (`claude plugin validate .`).

---

## Phase 2 — Consistency pass

Once all nine skills exist, do a holistic review before tagging the release.

- [x] Cross-skill vocabulary audit — every term used in skills B…I must match the definition in `domain-vocabulary`. (All non-foundational skills defer to `domain-vocabulary` via "Vocabulary additions" section; no term drift detected.)
- [ ] Link audit — every external URL still loads (200 OK). *Deferred to PR review; spot-checked at authoring time.*
- [x] Tone audit — no skill prescribes a "right answer"; each frames options and trade-offs. (Grep for "should always / always use / the right answer / the correct approach" returned nothing.)
- [x] Anti-pattern audit — every skill includes an `## Anti-patterns` section with failure modes named per item.
- [x] Length audit — sections kept dense; no skill exceeds ~250 lines; each bullet introduces a definition, decision, trade-off, or failure mode.

---

## Phase 3 — Release preparation

- [x] Update README roadmap table — v0.1 skills marked `Available (v0.1)`.
- [x] Update README header status line — now reflects "Core and cross-cutting v0.1 skills authored; preparing for first publication".
- [x] Confirm `version` is `0.1.0` in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
- [x] Verify `description`, `homepage`, `repository`, `keywords` in both manifests are accurate.
- [x] Run `claude plugin validate .` from the repo root — passes locally.
- [ ] Confirm GitHub Actions `validate.yml` runs green on the release branch (requires push).
- [ ] Self-check list from `CLAUDE.md` ("Self-check before opening a PR") passes for the cumulative change.

---

## Phase 4 — Publication

- [ ] Open release PR against `main` with summary of skills included and design philosophy reminders.
- [ ] Merge to `main` once CI is green and review is complete.
- [ ] Tag release: `git tag v0.1.0 && git push origin v0.1.0`.
- [ ] Create GitHub Release with notes covering each skill and primary sources used.
- [ ] Submit to Claude Code Plugin Marketplace — confirm install command works end-to-end: `/plugin marketplace add oa2p-solutions/ecommerce-foundations` then `/plugin install ecommerce-foundations@ecommerce-foundations`.
- [ ] Register on skills.sh — confirm `npx skills add oa2p-solutions/ecommerce-foundations` resolves and installs.
- [ ] Smoke test in a fresh Claude Code session: load a skill, verify frontmatter parses, verify content renders.

---

## Phase 5 — Post-publication

- [ ] Announce the release (channels TBD).
- [ ] Open backlog issues for v0.2 skills: `checkout-flow`, `fulfillment-and-shipping`, `returns-and-rmas`, `promotions-and-discounts`, `customer-accounts`, `taxes`, `saga-compensation`, `domain-events`.
- [ ] Collect feedback from first users; track in a `FEEDBACK.md` or GitHub Discussions.
- [ ] Schedule v0.1.1 patch window for typo fixes and clarifications surfaced after release.

---

## Out of scope for v0.1.0

These are explicitly deferred. Do not let scope creep pull them into the first release.

- v0.2 skills listed in README (`checkout-flow`, `fulfillment-and-shipping`, `returns-and-rmas`, `promotions-and-discounts`, `customer-accounts`, `taxes`, `saga-compensation`, `domain-events`).
- Vertical packs (`pharmacy-cl`, `marketplace`, `subscriptions`, `food-delivery`, `b2b-quotes`) — each lives in its own repository.
- Any executable code, scripts, or tooling beyond the existing CI validator.
- Translations of skill content. Skills stay English-only per `CLAUDE.md` rule 5.
