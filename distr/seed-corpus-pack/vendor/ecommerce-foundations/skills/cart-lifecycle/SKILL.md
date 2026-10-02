---
name: cart-lifecycle
description: Use when designing cart identity, persistence, merging, revalidation, expiry, or the transition from anonymous browsing to checkout. Load before deciding how guest carts attach to accounts on login, how prices/inventory refresh as the cart ages, or how the cart converts into an order. Vocabulary (cart, line item, customer, account, guest, variant, available, reserved, quote, order) is imported from domain-vocabulary.
---

# Cart lifecycle

The cart is the most-touched commerce artifact: every visitor with intent passes through it, and every issue with prices, inventory, or sessions surfaces here first. Modeling it well means deciding identity, persistence, revalidation, merge behavior, and expiry — *before* writing the first endpoint.

This skill frames those decisions. It does not pick the storage backend or the wire format.

Read the `domain-vocabulary` skill first. Terms used here without redefinition: *cart*, *line item*, *customer*, *account*, *user*, *guest*, *variant*, *available*, *reserved*, *quote*, *order*, *channel*, *price list*.

## Vocabulary additions

The general terms come from `domain-vocabulary`. The following are specific to cart lifecycle.

**Cart identity** — The token or key that lets a server retrieve the cart on the next request. May be a session cookie, an opaque token returned to the client, the *customer* id (for an authenticated cart), or a composite.

**Cart binding** — The relationship between a *cart* and a *customer* or *account*. An anonymous cart is unbound; an authenticated cart is bound. Binding happens on login, signup, or guest-checkout-completion.

**Cart merge** — The operation that combines an anonymous cart with a customer's existing cart at login time. Several strategies (see decision framework).

**Cart revalidation** — Re-resolving prices, taxes, *available* stock, applicable discounts, and shipping options against the current state of the system. The cart may have been built minutes or weeks ago; nothing carried in it is guaranteed to still be valid.

**Cart expiry** — The moment a cart is considered abandoned by the system and either purged or moved to a long-term archive. Distinct from session expiry, which is the auth-side notion.

**Abandoned cart** — A cart with no buyer activity for some defined window, where one or more *line items* are still present. The boundary between "stale" and "abandoned" is a business choice.

**Cart state machine** — The explicit set of states a cart can occupy and the allowed transitions between them. Sylius models this rigorously. ([Sylius](https://docs.sylius.com/the-book/orders/states))

**Persistent cart** — A cart that survives across sessions and devices for an authenticated *customer*. Distinct from the session-scoped cart that lives only as long as the browser session.

**Cart token (cart key)** — An opaque, unguessable string identifying a specific cart. Sometimes used in URLs (share-this-cart features) or in tracking links.

**Cart adjustment** — A non-product line on a cart: discount, shipping, tax, gift wrap, fee. Adjustments are not *line items* in the product sense but participate in totals.

**Order conversion** — The point at which the cart becomes an *order*. Some platforms model this as a status change on a single aggregate (Sylius's `Order` carries cart states); others as a discrete copy from one aggregate to another (Medusa). The vocabulary here treats them as separate aggregates with an explicit conversion event.

## Canonical model

The cart has two structural variants depending on identity strategy. Most non-trivial systems eventually support both.

**Anonymous (guest) cart:**

```
Cart
  ├── id (server-generated)
  ├── token (opaque, returned to client)
  ├── channel_id, store_id, region
  ├── currency
  ├── line_items: [{ variant_id, quantity, unit_price_snapshot, ... }]
  ├── adjustments: [{ type, amount, reason }]
  ├── shipping_address?, billing_address?
  ├── shipping_method?
  ├── customer_email?     (captured during checkout, optional pre-checkout)
  ├── totals (derived)
  ├── created_at, updated_at, expires_at
  └── state: open | converted | abandoned | expired
```

**Authenticated (bound) cart:** the same shape with a `customer_id` field. The token may still exist for cross-device handoff or short-lived auth-free flows.

Cart state machine (minimal version aligned with Sylius's vocabulary):

```
       new
        ↓ add/update/remove
       open ──────────────┐
        ↓ start checkout  │ inactivity
   checkout_in_progress   │
        ↓ submit          ↓
      converted        abandoned
                          ↓
                       expired (purged or archived)
```

Invariants:

1. **A cart has one currency.** Mixing currencies on a single cart is a downstream simplification — express FX at price-resolution time, never on a per-line currency.
2. **Line item prices are snapshots.** The price stored on a line item is the *resolved price* at the time of last revalidation, not a live pointer. The cart re-resolves on revalidation.
3. **`available` is never copied into the cart.** Stock is rechecked at the moments revalidation policy specifies.
4. **The cart belongs to exactly one identity at a time.** Anonymous OR customer-bound. Merging is an explicit operation that updates the binding and resolves conflicts.
5. **Expired carts do not silently re-open.** A cart past `expires_at` is treated as non-existent; the client gets a fresh cart.

## Variation dimensions

- **Identity model** — Cookie-only, server-side session, opaque token in client storage, customer-account-bound, hybrid (anonymous token that promotes to bound on login).
- **Cross-device** — Single-device (cart lives in browser storage; lost on device change) vs cross-device (server-persisted; resumable from another device).
- **Cart authority** — Server-authoritative (cart lives on server; client is a view) vs client-authoritative (cart lives in localStorage; server is a sync target) vs hybrid. Server-authoritative is the default for any cart that affects business logic; client-authoritative is acceptable only for ephemeral wish-list-like surfaces.
- **B2B vs B2C** — B2C carts convert to orders. B2B carts may convert to *quotes* (awaiting approval) before becoming orders. Modeling the quote stage requires either a separate aggregate or an extra state in the cart machine.
- **Channel scope** — A cart bound to one *channel* (browsing the mobile app cannot resume the web cart) vs shared across channels (the same authenticated customer sees the same cart on web and mobile).
- **Editability after checkout starts** — Cart frozen at checkout entry vs cart mutable throughout checkout vs cart enters a "checkout draft" copy that is mutable while the original is preserved.

## Decision frameworks

### Cart identity strategy

The identity strategy determines almost every other cart decision.

| Strategy | Identity source | Pros | Cons |
|:---|:---|:---|:---|
| Session cookie | Server session id | Simple; ties to existing auth | Lost on cookie clear; hard to share; cross-device requires login |
| Opaque cart token | Token returned at first add-to-cart | Survives session loss if the client persists the token; shareable | Client must store and re-send; longer-lived means longer-lived stale state |
| Customer-bound | `customer_id` foreign key | Cross-device by construction; clean ownership | Only works for authenticated buyers; needs merge strategy for anonymous-to-authenticated |
| Hybrid (token + bind on login) | Token until login, then bound | Best of both | Most complex; merge policy must be explicit |

Default to hybrid for any consumer storefront. Default to customer-bound for B2B portals where every buyer authenticates.

### Merge-on-login strategy

When an anonymous cart exists and the buyer logs in to an account that already has a saved cart, four positions:

| Strategy | Behavior | Use when |
|:---|:---|:---|
| Replace | New (anonymous) cart wins; existing cart is discarded | Buyers expect fresh sessions to start clean (rare default) |
| Keep existing | Existing (authenticated) cart wins; anonymous is discarded | Cross-device persistence matters more than current-session work |
| Union (deduplicated) | Combine line items; same variant gets one line with summed quantity | Most consumer-friendly default |
| Prompt user | Ask the buyer which to keep, or which lines to merge | Highest UX cost; safest when the carts could meaningfully conflict |

Pitfalls regardless of strategy:

- Merged carts must revalidate prices and *available*; the saved cart may carry stale data.
- Discounts and promotions may have applied differently to each cart; do not preserve adjustments across the merge — re-evaluate.
- Address and shipping method may not transfer; clear and re-prompt unless the buyer's account has defaults.

### Revalidation policy

Three positions on *when* to revalidate:

1. **On every read.** Safest; most expensive. Every cart view costs a price-resolution and stock check.
2. **On key moments.** Add/remove line item, checkout entry, payment submission. Cheapest; risks stale data between moments.
3. **TTL-based.** Cached resolution valid for N seconds; refresh past TTL. Tunable; needs careful invalidation when prices change.

Most non-trivial systems combine 2 and 3: always revalidate at checkout entry and payment submission; use TTL for the browsing cart view.

What to revalidate:

- Resolved price (price lists, sales, customer-group pricing).
- *Available* stock (the variant may be sold out since the cart was built).
- Discount applicability (the promo code may have expired or changed conditions).
- Shipping options (the address may have changed eligibility).
- Tax (location, customer, product changes affect rates).

Revalidation produces buyer-facing events when values change:

- Price increase: prompt the buyer; do not silently raise the price.
- Price decrease: silently apply the better price.
- Stock-out: remove or flag the line item; do not silently complete checkout.
- Discount expired: remove and notify; do not silently honor expired codes.

### Cart expiry

Three layers:

| Layer | Purpose | Typical window |
|:---|:---|:---|
| Idle expiry | Reclaim *reserved* stock from carts no one is using | Hours (15 min – few hours), depending on reservation timing |
| Soft expiry | Mark the cart as abandoned for marketing follow-up; line items still recoverable | Days (1–14) |
| Hard expiry | Purge or archive the cart | Weeks to months |

The idle-expiry window is tightly coupled to the *reservation timing* chosen in the `inventory-management` skill. Reserving at cart-add and expiring after 24 hours means the storefront sits with 24-hour stock holds per browsing visitor — usually too long.

### Cart-to-order conversion

Two structural choices:

1. **Status change on one aggregate.** The same record progresses from `cart` to `order`. Simplest; the order keeps the cart's id and history. Sylius takes this approach.
2. **Copy to a new aggregate.** A new `Order` is created from the `Cart`; the cart is marked converted (or deleted). Cleaner separation; lets cart and order schemas evolve independently. Medusa, commercetools, and Stripe-style flows take this approach.

Whichever is chosen, the conversion must:

- Snapshot every field that must not change post-conversion: prices, tax rates, discounts, addresses, shipping option.
- Replace cart *reservations* with order *commitments* atomically. No window where the items are neither reserved nor committed.
- Be idempotent. A buyer who clicks "Submit" twice must not create two orders.

### Editability during checkout

Three positions:

1. **Cart frozen at checkout entry.** Any change kicks the buyer back to cart. Strongest data integrity; harshest UX.
2. **Cart mutable through checkout.** Each mutation revalidates the in-progress checkout. Best UX; most edge cases.
3. **Checkout-draft copy.** Cart is copied at checkout entry into a draft order; the original cart remains editable. Best of both; most code.

Choose deliberately. Pick 1 only if the catalog is high-cost-of-error (custom configurations, prescription medication, regulated goods). Pick 2 for typical B2C. Pick 3 if the order schema is fundamentally different from the cart schema and a copy is needed anyway.

## Cart storage and caching

The model above describes the cart's logical shape. The *physical* storage path matters because the cart is the most-frequently-read commerce artifact — every page view on the storefront typically references it.

Three storage patterns appear across cloud-native commerce architectures ([AWS shopping cart on DynamoDB](https://docs.aws.amazon.com/prescriptive-guidance/latest/dynamodb-data-modeling/use-cases.html), [Azure cache-aside](https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside), [Shopify Engineering on checkout](https://shopify.engineering/)):

**Direct key-value cart store.** The cart lives in a key-value database (DynamoDB, Redis, Cosmos DB) keyed by cart id. Sub-millisecond reads; horizontally scalable. The shape must be denormalized — line items embedded in the cart document, not joined. Variant detail snapshotted into each line so the cart can be displayed without a catalog round-trip.

**Cache-aside over a relational cart.** The system of record is a relational database; a cache (Redis, Memcached) holds the read projection. Reads hit the cache first; writes update both. The cache is invalidated on every write that affects a cart. Adequate up to moderate scale; the invalidation discipline becomes brittle at scale.

**Edge-resident cart.** The cart is replicated to a CDN edge or to client-side storage with periodic sync to the origin. Used by latency-sensitive storefronts (mobile-first, global). Trade-off: revalidation is more expensive because edge state can diverge from origin truth.

Whichever storage path is used, two invariants from the model above must hold:

- **The server is authoritative for any cart that affects business logic.** Client-side or edge-resident state is a *view*, not the source.
- **Snapshots are denormalized.** The cart carries enough variant detail (name, image, snapshot price) to render without joining live catalog/pricing data. Catalog changes mid-cart-session are caught by revalidation, not by live joins.

## Anti-patterns

- **No cart expiry mechanism.** Carts accumulate forever; reservations leak; queries slow. Set idle and hard expiry from the start, even if generous. Symptom: weekly batch job needed just to purge ancient carts.
- **Storing live `variant.price` references in line items.** When the variant's price changes, every old cart silently shifts. Snapshots are mandatory; refresh on revalidation. Symptom: a customer sees `$10` on their cart page and `$12` at checkout with no announcement.
- **Anonymous-to-authenticated merge that loses items.** Buyers do not forgive losing items they added. If merge strategy can discard items, prompt explicitly. Symptom: support tickets about "the things I added are gone."
- **Authenticating mid-checkout doesn't re-evaluate pricing.** Logging in often unlocks a different price list (customer-group, loyalty tier). Failing to re-resolve means the buyer pays the public price. Re-validate on every binding change.
- **Cart binding to user, not to customer.** When B2B introduces sales reps impersonating customers, the cart vanishes for them because the user changed even though the customer didn't. Bind to *customer*, with user as an authorization layer above it.
- **Server treats cart as a write-only log.** Each PATCH appends a new line instead of mutating; totals computed from the log. Becomes opaque and slow; reconciliation is a nightmare. Treat the cart as a mutable aggregate; maintain a separate event log if auditing is needed.
- **Cart accepts items from outside the buyer's catalog scope.** A cart bound to channel A receives a variant only sold on channel B. Validate at every add-to-cart against the catalog scope (see `catalog-and-product-modeling`).
- **No idempotency on add-to-cart.** A retried POST adds the item twice. Idempotency-key the operation (see `idempotency` skill) or de-duplicate by line.
- **Checkout completion succeeds but cart not marked converted.** The next visit shows the same cart. Buyer re-orders by accident. Convert-the-cart and create-the-order must succeed or fail as one unit.
- **Treating cart as the order before payment.** A "paid" flag on the cart instead of a separate order aggregate (or status) muddies the lifecycle. Be explicit about when intent becomes commitment.
- **Allowing currency change on a non-empty cart.** Mixing line-item prices snapshotted in one currency with new lines in another silently breaks totals. Either re-price the whole cart or refuse currency change while items exist.
- **No revalidation between cart view and checkout submission.** Stock can be sold to someone else, prices can change, discounts can expire — all unnoticed until the buyer is rejected at payment. Revalidate at checkout entry at minimum.
- **Sharing cart tokens by URL with no scoping or auth.** Anyone with the link can modify the cart. If share-this-cart is a feature, scope the shared token to read-only or to a copy.

## Sources

- Sylius cart state machine — https://docs.sylius.com/the-book/orders/states
- Sylius cart concepts — https://docs.sylius.com/the-book/orders/orders
- Medusa cart module — https://docs.medusajs.com/resources/commerce-modules/cart
- Shopify Storefront Cart API — https://shopify.dev/docs/api/storefront/latest/objects/Cart
- Shopify cart abandonment — https://shopify.dev/docs/api/admin-graphql/latest/objects/AbandonedCheckout
- commercetools cart — https://docs.commercetools.com/api/projects/carts
- Stripe Checkout session lifecycle — https://docs.stripe.com/payments/checkout/how-checkout-works
- Fowler — State pattern — https://martinfowler.com/eaaCatalog/state.html
- Evans, *Domain-Driven Design*, Chapter 10 (Aggregates)
- AWS Prescriptive Guidance — shopping cart on DynamoDB — https://docs.aws.amazon.com/prescriptive-guidance/latest/dynamodb-data-modeling/use-cases.html
- Azure Architecture Center — Cache-Aside pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside
- Cloudflare blog — edge ecommerce and cart persistence — https://blog.cloudflare.com/
- Shopify Engineering — checkout reliability and cart handling — https://shopify.engineering/
- Alibaba Cloud — retail solutions (cart at peak load) — https://www.alibabacloud.com/solutions/retail
- Amazon — "1-Click" buying as an extreme cart-collapse design (US Patent 5960411, expired 2017) — https://patents.google.com/patent/US5960411
- Mercado Libre Engineering — cart abandonment recovery — https://medium.com/mercadolibre-tech
- Booking.com Tech — cart and funnel A/B testing rigor — https://medium.com/booking-com
- Bryar & Carr, *Working Backwards* — customer-obsession framing applied to cart UX
- Steve Krug, *Don't Make Me Think* (rev. 2014) — usability fundamentals for cart and checkout
