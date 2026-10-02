---
name: inventory-management
description: Use when designing stock tracking, reservation timing, oversell policy, multi-location allocation, or any code that decrements/holds inventory. Load before writing the function that checks "is this in stock", choosing when to deduct stock in the cart-to-order flow, or modeling stock across warehouses. Vocabulary (on-hand, available, reserved, committed, safety stock, location, backorder, oversell) is imported from domain-vocabulary.
---

# Inventory management

The single most error-prone area of commerce. Inventory bugs are almost always vocabulary bugs: the system conflates *on-hand* with *available*, or "reserved" with "committed," and the storefront promises units that can't be shipped.

This skill frames the modeling decisions and the timing decisions. It does not pick a stock policy — that depends on the business model, fulfillment topology, and tolerance for friction vs oversell.

Read the `domain-vocabulary` skill first. Terms used here without redefinition: *on-hand*, *available*, *committed*, *reserved*, *safety stock*, *backorder*, *oversell*, *location*, *variant*, *SKU*, *cart*, *order*, *fulfillment*.

## Vocabulary additions

The general terms come from `domain-vocabulary`. The following are specific to inventory.

**Stock movement (inventory transaction)** — A discrete change in *on-hand* at a *location*: receiving (+), shipping (−), adjustment (±), transfer between locations (− at source, + at destination). The append-only ledger of these movements is the source of truth; *on-hand* counts are derivable from the ledger.

**Reservation** — An explicit hold on stock against a *cart*, *checkout*, or *order*, decrementing *available* without decrementing *on-hand*. Reservations have an owner (the cart/order id), a quantity, a location, and typically an expiry.

**Stock-out** — The state where *available* reaches zero for a *variant* at a *location*. Distinct from *on-hand* being zero — stock-out can happen with positive *on-hand* if reservations or commitments consume it.

**Replenishment** — Restoring *on-hand* via receiving (new units arrive at a location) or transfer (units move from another location).

**Cycle count** — A periodic physical recount that reconciles *on-hand* against the ledger. Discrepancies become adjustment movements.

**Allocation** — The decision of which *location*'s units fulfill an *order*. May happen at order creation, at fulfillment scheduling, or as late as the warehouse pick step.

**Fulfillability** — Whether an order *can* be shipped given current commitments and location allocation rules. Not the same as availability at order time, because commitments and locations may have shifted.

**Demand signal** — Any non-confirmed indicator that stock may be needed: views, add-to-cart events, abandoned carts, forecast. Used for safety stock and replenishment, not for hard reservation.

**Inventory snapshot** — A point-in-time read of *on-hand* and *reserved* across all *variants* and *locations*. Used for reporting; the live counts diverge immediately.

**Pre-order vs backorder** — *Pre-order*: stock has not yet existed (new product, expected arrival date known). *Backorder*: stock existed and is currently zero but will replenish. The buyer experience and the operational handling can differ, even if the data model is similar.

## Canonical model

The four-state model is the foundation. Use it; do not invent alternatives.

```
on_hand        units physically present at a location
  − committed  − units promised to confirmed orders not yet shipped
  − reserved   − units held against carts/checkouts not yet ordered
  − safety     − defensive buffer for forecast error
  ───────────
  = available  units the storefront can promise to a new buyer
```

Invariants:

1. **`on_hand` changes only via stock movements** (receive, ship, adjust, transfer). It does not move when a customer adds to cart, places an order, or pays.
2. **`reserved` and `committed` are derived sums** over open reservations of the appropriate kind, scoped to the *variant* and *location*.
3. **`available` is never stored.** It is a query. Storing it directly creates two sources of truth that drift.
4. **Reservations are owned.** A reservation has a unique owner (cart id, order id) so it can be released cleanly when the owner's lifecycle ends.
5. **Reservations expire.** A cart that disappears must not silently consume stock forever; an expiry mechanism is mandatory.

The data shape converged on by most platforms ([Shopify](https://shopify.dev/docs/apps/build/orders-fulfillment/inventory-management-apps), [Medusa](https://docs.medusajs.com/resources/commerce-modules/inventory), [commercetools](https://docs.commercetools.com/api/projects/inventory)):

```
StockLocation
  ├── id
  ├── name
  └── address, contact, capabilities

InventoryLevel
  ├── stockable_id (variant ref)
  ├── location_id
  ├── on_hand
  └── (safety_stock?)        per-location override

Reservation
  ├── id
  ├── stockable_id
  ├── location_id
  ├── owner_type (cart|order)
  ├── owner_id
  ├── quantity
  ├── kind (reserved|committed)
  ├── expires_at?
  └── created_at

Movement (ledger; append-only)
  ├── id
  ├── stockable_id
  ├── location_id
  ├── delta (signed)
  ├── reason (received|shipped|adjusted|transferred_in|transferred_out)
  ├── reference (order_id, transfer_id, count_id)
  └── created_at
```

## Variation dimensions

- **Physicality** — Physical (real units, finite, in locations) vs digital (no finite quantity; entitlement rather than stock) vs service (capacity per slot, not units) vs hybrid.
- **Location topology** — Single-warehouse, multi-warehouse, store-as-warehouse (BOPIS), drop-ship (third-party fulfills, merchant relays inventory feed), marketplace (each vendor has its own inventory).
- **Reservation timing** — When the system holds stock against a buyer. Options range from "at add-to-cart" (highest friction, lowest oversell) to "at payment confirmation" (lowest friction, highest oversell). See decision framework below.
- **Oversell tolerance** — Strict (never oversell — refuse the order if uncertain) vs pragmatic (oversell occasionally; cancel and apologize) vs deliberate (oversell as policy — backorder, pre-order).
- **Stock sync frequency** — Real-time (every change reflects instantly) vs near-real-time (eventual within seconds) vs batch (periodic export from ERP/WMS to storefront). Slower sync widens the oversell window.
- **Authority** — Storefront-owned inventory (the commerce platform is the system of record) vs ERP/WMS-owned (an external system is authoritative; storefront has a cached view).

## Decision frameworks

### When to reserve stock

The single highest-impact inventory decision. Each option has a different oversell vs friction trade-off.

| Timing | Reserved at | Released on | Friction | Oversell window |
|:---|:---|:---|:---|:---|
| **At add-to-cart** | First item added | Cart expiry, item removal, checkout completion | Highest — visitors block stock for non-buyers | Smallest |
| **At checkout start** | Buyer enters checkout flow | Checkout abandonment, completion | Medium — committed buyers only, but abandoned checkouts hold stock | Moderate |
| **At order creation** | Order is placed (pre-payment) | Payment failure, cancellation | Low — orders are mostly real | Larger |
| **At payment confirmation** | Payment succeeds | Cancellation, refund | Lowest | Largest |
| **At fulfillment scheduling** | Warehouse picks the order | Never (it's already picked) | None | Maximum |

How to choose:

- **High-traffic flash sales** (limited stock, intense demand burst) → reserve earlier (cart or checkout-start). Friction is acceptable because the cost of overselling is reputational damage at scale.
- **Low-traffic catalog with abundant stock** → reserve later (order or payment). Friction loses sales; oversell risk is negligible.
- **High-margin custom products** → reserve at order. Buyer commitment is high; the customer-impact cost of cancellation is high.
- **Marketplace or drop-ship** → defer reservation to the partner system; cache their availability with revalidation. The storefront cannot promise units it doesn't control.

The choice may legitimately differ per *variant* or per *product type*. Encode the policy explicitly; do not let the implementation drift into one timing by accident.

### Multi-location allocation

When *on-hand* is split across locations, two questions arise:

1. **Availability for promise** — does the storefront show aggregated stock or per-location stock?
2. **Order allocation** — which location's stock fulfills the order?

| Allocation rule | Behavior | When to choose |
|:---|:---|:---|
| Nearest to shipping address | Pick the location minimizing transit time / cost | Fast-shipping promise; multi-region warehouses |
| Highest-stock first | Pick the location with the most units | Balances stockouts across the network |
| Lowest-cost first | Pick the location with cheapest landed-shipping cost | Cost-optimized fulfillment |
| Priority order | Editor-assigned per-location priority | Brand-controlled (flagship store has priority for in-store pickup) |
| Split shipment | Pick from multiple locations, ship separately | When no single location can fulfill the full order |

The choice is a business decision, not an engineering preference. The skill of the engineering side is to express the rule declaratively, log which rule fired and why, and not silently degrade to a fallback ("nearest" silently became "any-available").

### Oversell policy

Three positions:

1. **Strict (refuse-on-uncertainty).** Any concurrency conflict, stale data, or partial commitment causes the order to be refused. Buyer-facing friction; reliable promise.
2. **Pragmatic (oversell-and-resolve).** Accept the order; if stock cannot be allocated at fulfillment, cancel with apology and refund. Lower friction; reputational cost when invoked.
3. **Deliberate (oversell-as-policy).** Accept orders beyond *on-hand*; mark them as *backorder* or *pre-order*; communicate expected fulfillment date.

A single store can mix all three by *product type* or by *channel* (the website is strict, the marketplace listing is pragmatic to keep up with feed lag).

### Concurrency control for decrements

Multiple buyers buying the last unit. The race is real and must be handled deterministically.

- **Atomic decrement** — A single DB statement (`UPDATE inventory SET on_hand = on_hand − 1 WHERE on_hand >= 1 AND ...`) that succeeds or fails with no intermediate read. Simple, fast, only works when the decision rule is expressible in one statement.
- **Optimistic locking** — Read with a version, compute, write with a version check; retry on conflict. Works for complex multi-row logic; latency increases under contention.
- **Pessimistic locking** — `SELECT ... FOR UPDATE` over the affected rows; serializes contention. Strong consistency; can become a hotspot under high concurrency on a single SKU.
- **Single-writer queue** — All decrement requests funnel to one writer per SKU. Predictable; the queue becomes the scaling bottleneck.

The platform's storage choice (Postgres vs DynamoDB vs Cassandra vs custom inventory service) dictates which is feasible. Do not bolt arbitrary concurrency primitives onto a store that doesn't support them.

### Reservation expiry

Reservations without expiry leak stock. The expiry needs three behaviors:

- **Timely release** — a background job or TTL mechanism that returns the units to *available* once the reservation expires.
- **Refresh on activity** — extending the expiry when the buyer interacts with the cart, but bounded (a cart can't be refreshed indefinitely).
- **Idempotent release** — releasing an already-released reservation must be a no-op, not an error.

### Stock visibility on the storefront

Three positions:

1. **Show exact count.** ("3 left.") Transparent; creates urgency; reveals supply-chain information competitors can exploit.
2. **Show binary status.** ("In stock" / "Out of stock.") Standard; hides supply detail.
3. **Show low-stock badge only.** ("Only a few left.") Soft urgency without exact counts.

Show what the business has decided to commit to. From an engineering standpoint, all three need the same *available* computation; the difference is the display layer.

## High-concurrency decrement patterns

The four-state model holds at any scale. The *physical* implementation of `available = on_hand − committed − reserved − safety` changes dramatically once a single SKU sees thousands of buyers per second — flash sale, drop release, Singles' Day, Black Friday. Three architectural patterns recur across high-scale commerce platforms ([AWS retail architecture](https://aws.amazon.com/retail/architecture/), [Alibaba Cloud Singles' Day patterns](https://www.alibabacloud.com/blog), [Walmart Global Tech](https://medium.com/walmartglobaltech)):

**Pre-allocated stock pool.** Move the contended decrement off the main inventory table into an in-memory key-value store (Redis, DynamoDB with conditional updates, or a custom inventory service). The relational DB tracks ground truth; the in-memory pool absorbs the per-request decrements and reconciles back. Alibaba's Taobao platform uses this pattern for Singles' Day at millions of TPS per SKU.

**Token-based admission.** For each SKU with limited stock, generate N tokens at sale start; only token-holders may enter the buy-flow. This shifts the contention from the inventory decrement to the token grant — which can be a simple counter or a queue. Buyers without a token see "sold out" immediately, eliminating spurious reservation pressure.

**Materialized-view inventory.** Inventory deltas are recorded as append-only events; available counts are derived by a materialized view. Cheap to write, cheap to scale; the read view may lag the write log by milliseconds to seconds. Pair with a reservation-on-read mechanism so a buyer who sees "in stock" is granted a short hold while their checkout proceeds. ([Azure Materialized View pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/materialized-view))

These are physical patterns; the canonical vocabulary above (`on_hand`, `available`, `committed`, `reserved`) still applies. The skill of designing the high-concurrency path is keeping the vocabulary consistent across the in-memory pool, the relational system of record, and the materialized view.

## Anti-patterns

- **Decrementing `on_hand` when a cart is created.** Conflates intent with movement. Cart-stage holds belong in *reserved*; *on_hand* only changes on physical events. Symptom: receiving never balances because purchases that didn't ship still decremented.
- **Storing `available` directly.** Creates two truths that drift. Compute it every time, cache with explicit invalidation, but never persist as the source. Symptom: a stockout fix updates `on_hand` but not `available`, and the storefront shows different numbers than the admin.
- **No reservation expiry.** Carts age, customers leave, units silently disappear. Symptom: storefront shows zero stock; warehouse has shelves full.
- **Treating "low stock" as a status, not a query.** Hard-coding `is_low_stock` per row makes the threshold uneditable per variant. Express the threshold as data and compute the status.
- **Ignoring multi-location at the data model layer.** Single-location-only schemas have inventory directly on the variant. When the business expands to a second location, the schema needs a destructive migration. Model as `InventoryLevel(variant_id, location_id, on_hand)` from day one even with one location.
- **Optimistic UI without server reconciliation.** The cart says "added"; the server later finds the unit is gone. The buyer learns at checkout. Mitigate with synchronous availability check on critical actions (checkout start, payment) and explicit revalidation policy.
- **Cancellation that doesn't release inventory.** Order cancelled at the order aggregate, reservation/commitment forgotten. Symptom: stock count stays artificially low after cancellations. Make release a compensating action of cancellation, not a manual step.
- **Sale-time deltas as the ledger.** Recording "sold 1" as a movement is fine; using it as the only ledger means stockouts from receiving errors, theft, damage, count adjustment never get recorded. The ledger must accept all reasons, including non-sale ones.
- **External feed overwrites local reservations.** When inventory comes from an ERP feed, the feed reflects on_hand; reservations live in the storefront. Overwriting the local view destroys reservations. Treat ERP as authoritative for on_hand only; reservations remain local.
- **Allocating at order creation in a multi-warehouse network with hours-of-lag feeds.** The chosen location may be out of stock by the time the order reaches the warehouse. Either allocate late (at fulfillment scheduling) or build the feed to be near-real-time.
- **Hard-coding "in stock" if `on_hand > 0`.** Misses the entire reservation model. Buyer sees "in stock" while reservations have actually consumed the units. Always compute `available`, not `on_hand > 0`.
- **Negative `on_hand` to model backorders.** Backorder is a state, not a negative count. Negative on_hand confuses every report and makes reconciliation impossible. Use a separate `backordered` field or `is_backorderable` flag with a positive backorder quantity.

## Sources

- Shopify inventory states (on-hand / committed / reserved / available) — https://shopify.dev/docs/apps/build/orders-fulfillment/inventory-management-apps
- Shopify InventoryLevel API — https://shopify.dev/docs/api/admin-rest/latest/resources/inventorylevel
- Medusa inventory module — https://docs.medusajs.com/resources/commerce-modules/inventory
- Medusa stock location module — https://docs.medusajs.com/resources/commerce-modules/stock-location
- commercetools inventory entries — https://docs.commercetools.com/api/projects/inventory
- commercetools standalone prices and inventory — https://docs.commercetools.com/api/projects/inventory
- Sylius inventory — https://docs.sylius.com/the-book/products/inventory
- Fowler — Optimistic offline lock — https://martinfowler.com/eaaCatalog/optimisticOfflineLock.html
- Fowler — Pessimistic offline lock — https://martinfowler.com/eaaCatalog/pessimisticOfflineLock.html
- AWS Prescriptive Guidance — inventory data on DynamoDB — https://docs.aws.amazon.com/prescriptive-guidance/latest/dynamodb-data-modeling/use-cases.html
- AWS retail reference architectures — https://aws.amazon.com/retail/architecture/
- Azure Materialized View pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/materialized-view
- Azure Sequential Convoy pattern (per-SKU serialization) — https://learn.microsoft.com/en-us/azure/architecture/patterns/sequential-convoy
- Alibaba Cloud — high-concurrency inventory patterns — https://www.alibabacloud.com/blog
- Walmart Global Tech — inventory accuracy and RFID — https://medium.com/walmartglobaltech
- Amazon Science — fulfillment-network optimization and demand forecasting — https://www.amazon.science/
- Werner Vogels — eventual consistency and inventory ledgers — https://www.allthingsdistributed.com/
- Mercado Libre Engineering — multi-country logistics and inventory network — https://medium.com/mercadolibre-tech
- Coupang Engineering — rocket-delivery inventory placement — https://medium.com/coupang-engineering
- Target Tech — store-as-fulfillment-center inventory — https://tech.target.com/
- NRF / ARTS retail data model — https://nrf.com/
