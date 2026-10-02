---
name: pricing-and-channels
description: Use when designing how prices are stored, scoped, and computed — list price vs sale price, price lists per channel/customer-group/region, multi-currency representation, tax-inclusive vs tax-exclusive display, tiered pricing, and the Money pattern. Load before adding a price column, designing a price-resolution rule, or wiring a tax-display toggle. Vocabulary is imported from domain-vocabulary.
---

# Pricing and channels

How to model and resolve prices for any commerce system. The hardest part of pricing is not the math — it is scoping (which price applies to whom, where, when, and for how many units) and representation (currency, precision, tax inclusion). This skill frames those decisions; it does not pick a pricing strategy.

Read the `domain-vocabulary` skill first. Terms used here without redefinition: *variant*, *SKU*, *price list*, *list price*, *sale price*, *channel*, *store*, *region*, *customer*, *customer group*, *line item*, *Money*.

## Vocabulary additions

The general terms come from `domain-vocabulary`. The following are specific to pricing.

**Resolved price** — The single price applied to a *line item* after the price-resolution algorithm has selected from all candidate prices. Distinct from any individual *list price* or *sale price*: it is the output, not an input.

**Price-resolution algorithm** — The deterministic procedure for choosing the *resolved price*. Inputs: *variant*, *channel*, *customer* (or *customer group*), *region*/currency, quantity, date. Output: a single price plus the inputs that justified it.

**Cost (landed cost)** — The merchant's acquisition cost of a *variant*. Not a price; used for margin calculation and to validate that *sale prices* don't go below a floor. Should not be exposed to buyers.

**Margin** — The difference between *resolved price* and *cost*, expressed in absolute terms or as a percentage.

**Markup** — A multiplier applied to *cost* to derive a price. The inverse direction of margin.

**Currency rounding mode** — The rule for converting computed amounts (after discount, tax, FX) back to the currency's minor unit precision. ISO 4217 defines the minor unit per currency; the rounding *rule* (half-up, half-even, etc.) is a system choice.

**Minor unit** — The smallest representable unit of a currency: cents for USD, paisa for INR, no minor unit for JPY. Money should be stored as integer minor units (250 cents) or as a fixed-precision decimal (2.50 USD), never as a float.

**Exchange rate (FX rate)** — The conversion factor between two currencies at a point in time. Stale rates introduce systematic error; volatile currencies require sub-daily refreshes.

**Price tier** — A unit price applied at a quantity threshold. Two forms: *volume* (the same unit price applies to all units once the threshold is met) and *graduated* (different unit prices apply to different ranges of units).

**Tax-inclusive vs tax-exclusive** — Whether the displayed *list price* already includes applicable taxes. Inclusive is typical in EU B2C, AU, NZ; exclusive is typical in US and most B2B globally. ([Stripe Tax](https://docs.stripe.com/tax))

**Price list (price book)** — A scoped collection of prices for *variants*, keyed by some combination of *channel*, *customer group*, *region*/currency, and effective date range. The unit of pricing reuse. ([commercetools](https://docs.commercetools.com/api/projects/products#prices), [Shopify B2B](https://shopify.dev/docs/api/admin-graphql/latest/queries/pricelist))

**Effective date range** — The validity window of a price entry (`starts_at`, `ends_at`). Price-resolution uses the current time to filter candidate prices.

**Override (price override)** — A price entry that takes precedence over a more general price for a narrower scope. Per-customer overrides override per-group overrides, which override per-channel defaults, etc. The precedence order must be explicit.

## Canonical model

Most platforms converge on the same structural decision: prices live in scoped *price lists* rather than as a single field on the *variant*.

```
Variant (1) ──< has prices in >── PriceEntry (N)
                                    │
                                    ├── price_list_ref
                                    ├── currency
                                    ├── amount (minor units)
                                    ├── tax_inclusive: boolean
                                    ├── min_quantity (for tiered)
                                    ├── starts_at, ends_at
                                    └── customer_group? channel? region?

PriceList (1) ──< has entries >── PriceEntry (N)
   │
   ├── scope: channel, customer_group, region
   └── currency
```

Price resolution at runtime:

1. Collect every *PriceEntry* candidate for the *variant* whose effective date includes "now."
2. Filter to those whose scope matches the request (current *channel*, current *customer*'s group, current *region*).
3. Filter to those whose *min_quantity* is ≤ the line item quantity.
4. Apply a deterministic precedence (most-specific wins; ties broken by effective date or by an explicit priority field).
5. Return the surviving *PriceEntry* as the *resolved price*.

The *list price* and *sale price* are not separate concepts at the data level — they are two *PriceEntry* rows on the same variant, with the sale entry having a narrower effective date or higher priority.

## Variation dimensions

- **Pricing scope** — Single global price per variant vs price lists scoped by channel, customer group, region, or currency. B2C boutiques often need one price list; multi-channel multi-region B2B needs many.
- **Currency model** — Single-currency, multi-currency with hard-coded per-region prices, multi-currency with FX-converted prices, hybrid (hard prices for major markets, FX for the long tail).
- **Tax display** — Tax-inclusive (one price for all buyers), tax-exclusive (price + tax shown separately), per-customer (B2B sees ex-tax, B2C sees inc-tax in the same store).
- **Discount model** — Flat sale price (discount baked into a price entry) vs cart-level promotion (discount computed by the `promotions-and-discounts` skill from the *list price*). Different operational implications; can coexist.
- **Pricing dynamism** — Static (editor-managed catalog prices), rules-based (computed by formula at request time), algorithmic (ML/optimization-driven). Each step up costs predictability and reproducibility.
- **B2B vs B2C** — B2B: per-account pricing, contracted prices, quote-derived prices. B2C: price-list per channel/region is usually enough.

## Decision frameworks

### Where prices live

Three positions:

1. **Inline on the variant.** One price field per variant; possibly a second for sale. Simple, fragile under scope (cannot vary by channel, customer, region without polymorphic columns).
2. **In a single price list.** Variant has no direct price; price is looked up from a single PriceList scoped to the store. Adequate for single-currency, single-channel stores.
3. **In multiple price lists with scope.** The canonical model above. Required for multi-channel, multi-region, B2B, or any system with contracted pricing.

Default to 3 unless the system is provably constrained to a single scope forever. The migration cost from 1 or 2 to 3 is much higher than starting at 3.

### Tax-inclusive vs tax-exclusive

The decision flows from the buyer's market and the regulatory requirement, not from engineering preference.

- **EU B2C**: must display tax-inclusive (VAT included). EU Directive 98/6/EC requires the all-in price.
- **EU B2B**: typically tax-exclusive (VAT shown separately).
- **US B2C and B2B**: typically tax-exclusive (sales tax applied at checkout based on destination).
- **AU, NZ**: tax-inclusive (GST included) for B2C, exclusive for B2B with ABN.
- **LATAM**: varies; tax-inclusive predominates for B2C.

When a single store serves multiple markets with different conventions, the system must:

1. Store one canonical price (either as-inclusive or as-exclusive — be consistent).
2. Compute the other view at display time using the applicable tax rate.
3. Make tax calculation timing explicit: at price display, at cart, at checkout, or at invoice. Display-time tax requires knowing the buyer's location before any cart is built — often only an approximation (IP, default region) is available.

### Multi-currency

Two positions, sometimes hybridized:

| Approach | Pros | Cons |
|:---|:---|:---|
| Hard-coded per-currency prices | Predictable; merchandiser controls the price each market sees; avoids FX surprises | Many price entries to maintain; price drift across markets must be policed |
| FX-converted at request time | One canonical price; auto-extends to new currencies | FX volatility hits the displayed price; rounding can produce unmarketable amounts ($19.99 → €18.7641); requires FX feed and rounding strategy |

Hybrid: hard-code prices for top-N currencies (the ones where pricing matters editorially); FX-convert for the long tail.

When converting, the rounding strategy matters as much as the rate:

- Round to a *psychologically-natural* amount (`.99`, `.95`, `.00`) rather than the literal converted figure.
- Apply rounding at the *resolved price* level, not after each arithmetic step (compounds rounding error).
- Document the rounding rule; auditors and buyers will both ask.

### Tiered pricing — volume vs graduated

| Pattern | Rule | Example (cost per unit) |
|:---|:---|:---|
| Volume | Once threshold met, the lower price applies to *all* units | 10 units: $9 each = $90 |
| Graduated | Different unit prices apply to *different ranges* | First 5 at $10, next 5 at $8 = $90 |

Both can produce the same total at the same quantity (as above) but diverge as quantity grows. Volume is simpler to explain to buyers; graduated is more common in SaaS and B2B contracts. Decide which the business uses and apply consistently — mixing them per product confuses both customers and the math.

### Precedence when multiple price entries qualify

Three common precedence rules; pick one and document it:

1. **Most specific scope wins.** Per-customer beats per-group beats per-channel beats default.
2. **Lowest price wins.** Buyer-friendly; risks revenue leakage from misconfiguration.
3. **Explicit priority field.** Each price entry carries an integer priority; highest wins. Most flexible, but moves the burden to the merchandiser.

Default to 1. Use 3 when contractual pricing must override even narrower scopes (a contracted buyer always sees the contracted price, even during a public sale).

### Cost storage

Cost is not a price, but the pricing model usually needs it.

- Store *landed cost* (purchase + freight + duty) per *variant* per *location*. Costs vary by source and time.
- Use cost only for internal reporting and margin guardrails — never expose it to buyers.
- Treat cost history as immutable for the variants already sold (cost-of-goods-sold reporting needs the cost at time of sale).

## Dynamic and real-time pricing

The model above assumes prices are edited by humans on a schedule. Some businesses run *dynamic pricing*: prices change continuously based on demand, inventory, competitor signals, customer segment, or ML model output. The vocabulary stays the same; what changes is where the *resolved price* comes from at request time.

Three architectures:

| Architecture | Source of truth | Refresh cadence | Trade-off |
|:---|:---|:---|:---|
| Editor-managed | Curated price entries in price lists | Manual (daily / weekly) | Predictable, auditable; slow to react |
| Rules-based | Evaluator runs declarative rules against context (time, inventory, segment) | Per-request | Reactive, explainable; rule sprawl over time |
| Model-driven | ML model emits per-request prices via inference endpoint | Per-request | Most responsive; least explainable; needs guardrails (floor/ceiling) |

When prices change frequently, two operational concerns become first-class:

- **Audit trail.** Every resolved price must be reproducible. Persist `(variant_id, customer_id, channel_id, timestamp, resolved_price, rule_or_model_version)` for every consequential read (cart line, order line). Without this, refunds and reconciliation become impossible months later.
- **Cart staleness.** A cart built at price P1 must not silently shift to P2 mid-checkout. The revalidation contract from `cart-lifecycle` applies: prompt the buyer when the price changes in a buyer-unfavorable direction; silently apply better prices.

AWS Marketplace's SaaS pricing patterns and Alibaba Cloud's dynamic-pricing solutions both treat dynamic pricing as an inference call with audit-logged outputs, not as an opaque function. ([AWS Marketplace pricing](https://docs.aws.amazon.com/marketplace/latest/userguide/pricing.html), [Alibaba Cloud dynamic pricing](https://www.alibabacloud.com/solutions/retail))

## Anti-patterns

- **Storing prices as floats.** Causes rounding errors that accumulate in totals, taxes, and refunds. Use integer minor units or a fixed-decimal type. ([Fowler — Money](https://martinfowler.com/eaaCatalog/money.html))
- **One price column per currency.** Adds a column for every new market; cannot scope by channel or customer. Indicator: the variant table has `price_usd`, `price_eur`, `price_brl`. Migrate to a price-list model before the third column appears.
- **Storing tax-inclusive and tax-exclusive prices as separate fields.** They drift apart under editing. Store one; compute the other. The chosen canonical form must be documented and stable.
- **Resolving price every time without caching.** On hot paths (catalog browse), repeated price-list traversal is expensive. Cache the *resolved price* per (variant, channel, customer-group, region, quantity bucket) with explicit invalidation on price-list changes.
- **Caching the resolved price into the cart and never refreshing.** A buyer who opens a cart yesterday should see today's price (or be told the price changed). Define a revalidation rule; never silently honor stale prices to the system's loss.
- **Treating sale price as a discount on list price.** Storing only the discount percentage requires computing the sale price at every render; subtle bugs (rounding direction, tax order) ensue. Store sale prices as concrete amounts; compute and display the implied savings if needed.
- **No effective date on prices.** Prevents scheduling sales, rolling out price changes in advance, or running A/B price tests. Add `starts_at`/`ends_at` from the start; null = unbounded.
- **Mixing tax-inclusive and tax-exclusive prices in one price list.** Forces every consumer to check the flag per row. Make each price list one or the other; the flag lives at the price-list level if anywhere.
- **FX rates with no audit trail.** When a price is FX-converted, capture the rate used at that moment. Recomputing later with a different rate produces unexplainable order/refund mismatches.
- **Computing total as `unit_price × quantity` then applying tax.** Acceptable for tax-exclusive; wrong for tax-inclusive if you also need to display the net subtotal. Decide on the canonical order of operations and stick to it across cart, checkout, invoice, and refund.
- **Negative prices to model discounts.** Adds magic into the data model that breaks reports, refunds, and reconciliation. Discounts belong on a separate *line* or as an adjustment field, not as a negatively-priced variant.

## Sources

- Stripe Prices API — https://docs.stripe.com/api/prices
- Stripe Tax — https://docs.stripe.com/tax
- Medusa pricing module — https://docs.medusajs.com/resources/commerce-modules/pricing
- commercetools product prices — https://docs.commercetools.com/api/projects/products#prices
- commercetools price tiers — https://docs.commercetools.com/api/projects/products#pricetier
- Shopify B2B price lists — https://shopify.dev/docs/api/admin-graphql/latest/queries/pricelist
- Shopify multi-currency pricing — https://shopify.dev/docs/api/admin-graphql/latest/objects/MoneyV2
- Fowler — Money pattern — https://martinfowler.com/eaaCatalog/money.html
- ISO 4217 currency codes — https://www.iso.org/iso-4217-currency-codes.html
- EU Directive 98/6/EC (price indication) — https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:31998L0006
- AWS Marketplace SaaS pricing — https://docs.aws.amazon.com/marketplace/latest/userguide/pricing.html
- Azure — SaaS metering and billing patterns — https://learn.microsoft.com/en-us/azure/architecture/example-scenario/saas/saas-platform-billing
- Alibaba Cloud — retail solutions (dynamic pricing) — https://www.alibabacloud.com/solutions/retail
- Stripe Engineering blog (currency precision posts) — https://stripe.com/blog/engineering
- Werner Vogels — pricing under eventual consistency (All Things Distributed) — https://www.allthingsdistributed.com/
- Amazon Science — algorithmic pricing research — https://www.amazon.science/
- Walmart Global Tech — "Everyday Low Prices" pricing operations — https://medium.com/walmartglobaltech
- Mercado Libre Engineering — payment-method-aware pricing and installments — https://medium.com/mercadolibre-tech
- Ben Thompson — Stratechery on commerce pricing strategy (Amazon, Costco, marketplaces) — https://stratechery.com/
