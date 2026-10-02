---
name: domain-vocabulary
description: Use when reasoning about any ecommerce system — establishes authoritative definitions for cart, quote, order, line item, on-hand, available, reserved, committed, customer, account, variant, SKU, channel, authorization, capture, fulfillment, shipment, and the other terms every other skill in this package depends on. Load this skill before discussing commerce primitives, designing data models, naming entities, or resolving ambiguity in requirements.
---

# Domain vocabulary

The single source of truth for terms used across this package. Every other skill imports its definitions from here. When a term appears here, other skills use it with the meaning defined here — not with a platform-specific or colloquial meaning.

The goal is not to invent terminology. It is to converge on the *least-ambiguous* definition observable across recognized sources, and to flag where sources disagree so the reader knows when a choice is being made.

## How to use this skill

1. **Define before using.** When a skill introduces a term, link the reader to this skill if the definition lives here. Do not redefine.
2. **Disambiguate explicitly.** If a platform uses one of these terms differently (Shopify's `fulfillment_status` ≠ this skill's `fulfillment`), say so once and continue with the canonical definition.
3. **When in doubt, prefer the more restrictive definition.** If "stock" could mean on-hand or available, force the choice — never use "stock" alone.

---

## Vocabulary

Organized by domain area. Cross-references use *italic*.

### Customers and accounts

**Customer** — A real-world buyer the system serves. Modeled as a record holding identity (name, contact info), addresses, payment methods, and order history. A *customer* exists whether or not the buyer is authenticated; the system can create a customer record from a guest *checkout*. Shopify, Medusa, and commercetools all use "customer" as the top-level commerce identity. ([Shopify](https://shopify.dev/docs/api/admin-rest/latest/resources/customer), [Medusa](https://docs.medusajs.com/resources/commerce-modules/customer))

**Account** — The customer's authenticated record in the system; the persistent state across sessions. "Account" is sometimes used interchangeably with *customer*, but the distinction matters when the customer can exist without an account (guest purchase) or when one account represents multiple customers (B2B parent company with branch buyers).

**User** — The authentication principal — the entity that logs in. A *user* is often linked to an *account* but is not the same: an admin user is not a customer; one user may switch between multiple customer contexts (B2B sales rep impersonating a customer); a guest *customer* has no user at all. Treat user, account, and customer as three orthogonal concepts and only collapse them when the business model truly requires only one.

**Guest** — A *customer* state in which no *account* has been created. Most platforms attach the order to an email-addressable customer record without persistent credentials; some delete the record after the order completes.

**B2B account hierarchy** — A modeling pattern where a parent legal entity (the company) contains child entities (branches, departments, individual buyers), each with their own addresses, price lists, and approval rules. commercetools formalizes this as "business units." ([commercetools](https://docs.commercetools.com/api/projects/business-units))

### Catalog and products

**Product** — A sellable concept. Carries a name, description, category, and marketing media. A product itself is not always stockable; in most platforms the stockable unit is a *variant*. ([Medusa](https://docs.medusajs.com/resources/commerce-modules/product), [Shopify](https://shopify.dev/docs/api/admin-rest/latest/resources/product))

**Variant** — A specific configuration of a *product* (size, color, material). Each variant has its own price, inventory, and *SKU*. A product with no configurable options still typically has exactly one variant under the hood — the variant is the unit of stock and price, not the product.

**SKU (Stock Keeping Unit)** — The identifier of a stockable unit. Conventionally the *variant* carries the SKU. SKUs must be unique within their scope (typically the store or tenant). "SKU" is also used colloquially to mean "the variant," but strictly it is the identifier, not the entity.

**Bundle (kit)** — A sellable unit composed of multiple *variants* sold together. Two main modeling approaches: as a *product* with its own *SKU* whose inventory is derived from component availability ("virtual bundle"), or as a separately stocked unit assembled in advance ("physical bundle"). The distinction matters for inventory and fulfillment.

**Configurable product** — A *product* exposing options the buyer selects to reach a specific *variant*. Sylius models this explicitly; Shopify exposes it through option values. ([Sylius](https://docs.sylius.com/the-book/products/products))

**Attribute vs option** — *Attributes* are descriptive (material: cotton; weight: 200g). *Options* generate variants (size: S/M/L). The same data point can be either depending on whether it differentiates a variant. Confusing the two leads to either combinatorial variant explosion (every attribute becomes a variant) or under-modeling (size is treated as a description and you cannot sell each size separately).

**Category, taxonomy, collection** — *Category* is a hierarchical placement (Clothing > Shirts > T-shirts). *Taxonomy* is the whole tree. *Collection* is a curated grouping that need not be hierarchical (Summer Sale, Staff Picks). Treat them as separate concerns; one product belongs to one place in the taxonomy but can belong to many collections.

### Pricing and money

**List price (regular price)** — The reference price for a *variant*, independent of promotions. Sometimes called "compare-at price" when shown struck-through next to a sale price.

**Sale price (promotional price)** — A reduced price effective for a period or condition. Distinct from a *discount* applied at the *cart* level: a sale price is intrinsic to the variant in a price list; a discount is computed during *cart* evaluation.

**Price list** — A scoped set of prices, typically keyed by *channel*, *customer* group, currency, and effective date. commercetools and Shopify B2B both expose price lists as first-class objects. ([commercetools](https://docs.commercetools.com/api/projects/products#prices), [Shopify](https://shopify.dev/docs/api/admin-graphql/latest/queries/pricelist))

**Tax-inclusive vs tax-exclusive pricing** — Whether the displayed price already includes tax (typical in EU B2C, AU, NZ) or excludes it (typical in US, B2B in most regions). The choice cascades through the entire stack: cart totals, invoices, refunds, reporting. ([Stripe Tax](https://docs.stripe.com/tax))

**Tiered pricing** — Different unit prices at different quantity thresholds. Volume discount is the common case (10+ units = 5% off per unit); graduated tiers add complexity (first 10 at one price, next 90 at another).

**Money** — Per Fowler's pattern, a value composed of an amount and a currency, never a bare float. Currency math requires fixed-point or integer-minor-units arithmetic to avoid rounding errors. Currency codes follow ISO 4217. ([Fowler](https://martinfowler.com/eaaCatalog/money.html), [ISO 4217](https://www.iso.org/iso-4217-currency-codes.html))

### Inventory

These four states are the foundation; conflating them is the most common source of inventory bugs.

**On-hand** — Physical units present at a *location*. This count changes only when goods physically arrive (receiving) or leave (shipping, damage, theft, count adjustment). It does *not* decrement when a customer places an order. ([Shopify](https://shopify.dev/docs/apps/build/orders-fulfillment/inventory-management-apps))

**Committed** — Units of *on-hand* that have been *reserved* against confirmed *orders* not yet shipped. The customer's order is "promised" these units but they are still physically present. Shopify uses this term explicitly.

**Reserved** — Units held against a *cart* or *checkout* that has not yet become an *order*. Reservations are short-lived and typically expire. Some platforms collapse this with *committed* and only track post-order reservations; the distinction matters when reservation policy differs by stage.

**Available (available-to-promise, ATP)** — The count the storefront shows as buyable. Computed as `on_hand − committed − reserved − safety_stock`. Available is a derived value; never store it directly.

**Safety stock** — A buffer subtracted from *available* to absorb forecast error, demand spikes, or counting drift. The threshold may vary by *variant* and *location*.

**Location (warehouse, store, fulfillment center)** — A physical place that holds *on-hand* inventory. Multi-location inventory means a single *variant* has independent counts per location. Available stock can be computed per-location or aggregated, depending on whether the storefront promises shipment from a specific location.

**Backorder** — An *order* placed when *available* is zero, accepted with the promise to fulfill once stock arrives. The order accumulates a *committed* claim that exceeds *on-hand*.

**Oversell** — When the system accepts more orders than *available* allows, by accident or by policy. "By policy" is acceptable (backorders, pre-orders); "by accident" is a bug, usually caused by reading stale *available* without coordination.

### Cart, quote, and order

These three are the core lifecycle artifacts. Treat them as three states of one underlying intent, not three independent concepts.

**Cart** — A buyer's working draft of an intended purchase. Mutable: *line items* can be added, removed, or quantity-adjusted; prices can re-evaluate as the cart changes. A cart belongs either to a *customer* or to an anonymous session. Carts expire. ([Medusa](https://docs.medusajs.com/resources/commerce-modules/cart))

**Quote** — A B2B-specific artifact: a *cart* that has been priced and offered to the buyer under specific terms (price, payment, validity period), pending acceptance. Distinguishing feature from a cart: a quote freezes terms; a cart re-evaluates. ([Spryker](https://docs.spryker.com/docs/pbc/all/cart-and-checkout/202410.0/base-shop/feature-overviews/quote-approval-feature-overview.html))

**Order** — A *cart* or *quote* that has been committed: the buyer has agreed to purchase under specific terms. Orders are append-only in the sense that the original commitment is preserved; subsequent state changes (cancellation, refund, fulfillment) attach to the order rather than replacing it. ([Sylius](https://docs.sylius.com/the-book/orders/states))

**Line item** — One row in a *cart*, *quote*, or *order*: a *variant* with a quantity, a unit price, applicable taxes, and discounts. The same word is used at all three stages; some platforms suffix with the stage (`cart_item`, `order_item`) for code clarity.

**Order item** — The *order*-stage version of a *line item*. Carries the price as of order commitment ("price at time of order"), which must not change even if the *list price* of the *variant* changes later.

**Sub-order (fulfillment group)** — A subset of an *order*'s *line items* fulfilled together — typically because they ship from different *locations*, ship at different times, or belong to different vendors in a marketplace. Some platforms model this as separate orders; others as fulfillment groups within one order. Be explicit about which model is in use.

### Order state — payment and fulfillment as orthogonal axes

An *order* has at least two independent state machines: payment and fulfillment. Treating them as one combined status leads to invalid states ("paid but unfulfillable") becoming hard to represent. Shopify exposes both: `financial_status` and `fulfillment_status`. ([Shopify](https://shopify.dev/docs/api/admin-rest/latest/resources/order))

**Payment status** — Where the order stands financially: `pending`, `authorized`, `paid`, `partially_paid`, `partially_refunded`, `refunded`, `voided`. Names vary by platform.

**Fulfillment status** — Where the goods stand physically: `unfulfilled`, `partially_fulfilled`, `fulfilled`, `shipped`, `delivered`, `returned`. Some platforms split shipped/delivered; some collapse them.

**Order status (lifecycle status)** — The high-level rollup: `draft`, `pending`, `confirmed`, `in_progress`, `completed`, `cancelled`. Treat as a view over the underlying machines, not as the authoritative source.

### Payments

Payment terminology converges around Stripe's vocabulary because Stripe formalized it most clearly. Other processors map onto these terms with minor naming differences.

**Authorization** — A confirmed reservation of funds on the buyer's payment method, valid for a limited period (typically 7–30 days for cards). The merchant has not yet received the money. ([Stripe](https://docs.stripe.com/payments/payment-intents))

**Capture** — Conversion of an *authorization* into a settled charge. The funds begin moving to the merchant. Capture can be full or partial; partial capture forfeits the remaining authorization.

**Sale (auth + capture)** — Single-step charge that authorizes and captures simultaneously. Use when goods ship immediately; prefer separate auth + capture when fulfillment is delayed.

**Void** — Cancellation of an *authorization* before *capture*. Releases the held funds without a charge. Once captured, void is no longer possible — only *refund*.

**Refund (full / partial)** — Return of captured funds to the buyer. Partial refunds reduce the captured amount. Refunds are not the inverse of capture financially — they may involve different fees and timing. ([Stripe Refunds](https://docs.stripe.com/refunds))

**Chargeback (dispute)** — A buyer-initiated reversal through their bank, bypassing the merchant. Disputes have their own lifecycle (needs response, under review, won/lost) and incur fees regardless of outcome. ([Stripe Disputes](https://docs.stripe.com/disputes))

**Hosted checkout** — Payment captured on the processor's domain (Stripe Checkout, PayPal redirect). The merchant never touches card data; PCI scope is minimal.

**Embedded checkout** — Payment fields rendered in the merchant's UI using processor-provided tokenizing components (Stripe Elements, Payment Element). Card data goes from the buyer's browser directly to the processor; PCI scope is reduced but not eliminated. ([Stripe](https://docs.stripe.com/payments/online-payments))

**Tokenization** — Replacement of sensitive payment data with a non-sensitive token redeemable by the processor. The merchant stores the token; the actual PAN never reaches merchant systems.

### Fulfillment and shipping

**Fulfillment** — The process of preparing and dispatching ordered goods. Encompasses pick, pack, label, hand off to carrier. A single *order* may have one or many fulfillments. ([Medusa](https://docs.medusajs.com/resources/commerce-modules/fulfillment))

**Shipment** — A single physical parcel handed to a carrier. One *fulfillment* may produce one shipment or multiple (split shipments).

**Carrier** — The shipping company (UPS, FedEx, DHL, local courier).

**Tracking number** — The carrier's identifier for a *shipment*. Tied to a tracking URL the customer can use.

**Pick-pack-ship** — The warehouse workflow: pick items off shelves, pack them into a parcel, generate label, ship.

**Split shipment** — When one *order* is divided across multiple *shipments*, typically because items ship from different *locations* or at different times.

### Channels and stores

**Channel (sales channel)** — A surface where the storefront is exposed: web, mobile app, marketplace integration (Amazon, eBay), physical POS, social commerce. Pricing, inventory, and tax policies can vary per channel. ([Medusa](https://docs.medusajs.com/resources/commerce-modules/sales-channel))

**Store (tenant)** — An isolated commerce instance within a multi-store deployment. May correspond to a brand, a country, or a customer segment. Sharing of catalog, customers, and orders across stores is a design decision, not a default.

**Region** — A geographic scope for pricing, currency, tax rules, and shipping options. A store may serve one or many regions.

### Cross-cutting

**Idempotency** — Property of an operation that produces the same result whether executed once or many times. *Idempotency keys* are client-supplied tokens that make non-idempotent operations safe to retry. See the `idempotency` skill. ([Stripe](https://docs.stripe.com/api/idempotent_requests), [RFC 7231 §4.2](https://datatracker.ietf.org/doc/html/rfc7231#section-4.2))

**Webhook** — An HTTP callback from one system to another, used to notify the merchant of asynchronous events (payment confirmed, dispute opened). Webhooks require signature verification, retry handling, and consumer-side deduplication. See the `webhooks` skill. ([Standard Webhooks](https://www.standardwebhooks.com/))

**Domain event** — A record that something significant happened in the domain, named in past tense (`OrderPlaced`, `PaymentCaptured`). Used to integrate bounded contexts asynchronously. Distinct from a *command*, which requests something happen. ([Fowler](https://martinfowler.com/eaaDev/DomainEvent.html))

**State machine** — A model of allowed transitions between states. Both *order* and *cart* are naturally state-machine-shaped; encoding transitions explicitly (as Sylius does) prevents invalid state combinations.

---

## Canonical model

The terms above describe one underlying mental model: commerce as a sequence of intent-becoming-commitment.

1. A *customer* (possibly guest) browses a *catalog* of *products* with *variants*.
2. They assemble a *cart* of *line items*, priced according to a *price list* scoped to a *channel* and possibly a *customer* group.
3. *Inventory* tracks four states (*on-hand*, *committed*, *reserved*, *available*); the storefront promises only *available* stock.
4. The cart converts to an *order* at *checkout*. The order locks in prices, addresses, and payment instrument.
5. The order moves on two orthogonal state machines: *payment* (authorize → capture → refund) and *fulfillment* (unfulfilled → fulfilled → shipped → delivered).
6. Asynchronous integration happens through *webhooks* and *domain events*; reliability comes from *idempotency*.

Treat this as one mental model with consistent vocabulary, not as a stack of independent concepts.

---

## Variation dimensions

Vocabulary stays constant across these dimensions; the *meaning* of certain terms shifts within them. Be explicit about which point in the space the system occupies.

- **Business model** — B2C, B2B, marketplace, subscription, hybrid. B2B introduces *quote* and *account hierarchy*; marketplace introduces *vendor* and *sub-order*; subscription redefines *order* as recurring.
- **Product type** — Physical, digital, service, mixed. Physical needs *fulfillment* and *shipment*; digital needs *entitlement* and *license key*; service needs *booking* and *appointment*.
- **Geography** — Single-region vs multi-region. Multi-region multiplies *price list*, *tax rules*, *currency*, and *channel*.
- **Customer model** — Guest-only, account-required, hybrid, B2B hierarchy. Affects whether *customer* and *account* must be distinguished and whether *user* is meaningful.
- **Inventory model** — Single-location, multi-location, drop-shipped (third-party fulfills), virtual (digital, no stock). Affects whether *on-hand* and *location* are needed at all.

---

## Decision frameworks

### When two terms seem interchangeable, pick the more specific one

- **"Stock"** — never use alone. Say *on-hand*, *available*, *committed*, or *reserved*.
- **"Customer"** vs **"account"** vs **"user"** — collapse only if the system truly has no need for the distinction. Most non-trivial systems eventually need all three.
- **"Status"** vs **"state"** — synonymous in casual use; choose one and apply consistently. This package uses *status* for the buyer-visible label and *state* for the engineering enum that drives transitions.
- **"Line item"** vs **"order item"** vs **"cart item"** — use the stage-specific name (`cart_item`, `order_item`) in code; use *line item* in prose when the stage is unambiguous.

### When a platform overloads a term

- Shopify uses `fulfillment_status` on the *order* level, not on individual line items, and treats partial fulfillment as a top-level state. Other platforms model fulfillment as a separate aggregate. Document which model the codebase uses and translate vocabulary at the boundary.
- Stripe's `Charge` and `PaymentIntent` are not synonymous: a `PaymentIntent` is a higher-level lifecycle wrapping zero or more `Charges`. Map to canonical *authorization* and *capture* terms in commerce code.
- Medusa's `Order` includes returns and exchanges as sub-modules of the order aggregate. Other platforms make returns top-level. The canonical vocabulary here puts returns adjacent to orders, not inside them; map at the boundary.

### When in doubt

Prefer the definition observed across two or more recognized sources. If only one source supports a definition, label it as platform-specific in the skill that uses it.

---

## Cross-platform vocabulary mapping

The canonical terms above are not invented for this package; they are the convergent vocabulary across commerce platforms, cloud providers, and academic references. The table below highlights how the canonical term maps onto specific systems, so the reader can translate at integration boundaries.

| Canonical | Stripe | Shopify | commercetools | AWS retail patterns | Alibaba Cloud retail |
|:---|:---|:---|:---|:---|:---|
| Customer | `Customer` | `Customer` | `Customer` | "buyer profile" | "buyer" / "member" |
| Variant | (n/a — no catalog) | `ProductVariant` | `ProductVariant` | "stockable unit" | "SKU" |
| SKU | (n/a) | `sku` field on variant | `sku` field on variant | "stockable identifier" | "SKU code" |
| Order | `Order` (Stripe Orders beta) or implied via PaymentIntent | `Order` | `Order` | "order record" | "order" / "dingdan" |
| Cart | (Checkout Session covers cart concerns) | `Cart` (Storefront) | `Cart` | "cart" — usually DynamoDB-backed | "shopping cart" |
| Authorization | `PaymentIntent` (status `requires_capture`) | financial_status `authorized` | `Payment` with `Authorization` transaction | (depends on processor) | Alipay "authorization" |
| Capture | `PaymentIntent.capture()` / Charge `captured=true` | financial_status `paid` | `Payment` with `Charge` transaction | (depends on processor) | Alipay "settlement" |
| On-hand | (n/a) | `available` on InventoryLevel — see anti-pattern below about Shopify's naming | `quantityOnStock` | "physical inventory" | "shíhuò" (现货) |
| Available | (n/a) | computed from on_hand minus reservations | `availableQuantity` | "available-to-promise" | "kěxiāo" (可销) |
| Reserved | (n/a) | "reserved" in fulfillment service apps | `Reservation` (custom) | "reservation" | "yùzhàn" (预占) |
| Committed | (n/a) | `committed` on InventoryLevel | implied via order allocation | "committed inventory" | "yǐzhàn" (已占) |

Important divergence: **Shopify's `available` field on `InventoryLevel` is what most platforms call `on_hand`** — it is the count of units not yet committed to fulfillment but does include units reservable. Shopify's `committed` is closest to the canonical `reserved + committed`. Translate at integration boundaries; do not let the platform's naming leak into domain code.

Other notable divergences:

- **commercetools "Inventory entry"** carries `availableQuantity` directly; the system computes it from on-hand minus reservations, but the API exposes it as a stored field. Treat as a read-through cache when wiring against it.
- **AWS retail reference architectures** typically model the cart and order as separate DynamoDB tables, with state transitions handled by Step Functions when the workflow spans multiple services.
- **Alibaba's commerce stack** ([Taobao/Tmall](https://www.alibabacloud.com/solutions/retail)) separates the "trading center" (cart, order, payment) from the "merchandising center" (catalog, inventory) — a natural bounded-context split that the canonical vocabulary above aligns with.

## Anti-patterns

- **Conflating *on-hand* with *available*.** Causes oversell when the storefront shows on-hand counts. Always derive available from on-hand minus commitments.
- **Treating *customer*, *account*, and *user* as one entity.** Works until the business needs guest checkout, multi-user accounts (B2B), or admin impersonation. Refactoring three months later costs more than modeling them separately on day one.
- **Storing prices as floats.** Causes rounding bugs in totals, taxes, and refunds. Use integer minor units or a decimal/Money type from the start.
- **Using "shipped" as the order completion signal.** Order completion happens after delivery, returns window, and dispute window — sometimes weeks after shipment. "Shipped" is a fulfillment state, not an order state.
- **Treating cart and order as one entity with a flag.** Causes invalid states ("a cart with a payment intent") and ambiguous business rules ("can the customer still edit?"). Model them as separate aggregates with an explicit conversion event.
- **Naming events as commands.** `CreateOrder` is a command (intent to do something). `OrderCreated` is an event (something happened). Mixing them produces unclear contracts in event-driven integrations.
- **Calling everything a "transaction."** "Transaction" overloads database transaction, payment transaction, business transaction, audit log entry. Pick one meaning per context and use a different word for the others.
- **Reusing variant SKUs across stores.** Causes accidental cross-store inventory bleed when integrations key on SKU alone. Either scope SKU to store or use a system-generated stockable ID and treat SKU as a label.
- **Treating refund as the financial inverse of capture.** A refund is a new operation with its own fees, timing, and failure modes. Modeling it as "undo capture" makes reconciliation impossible.

---

## Sources

- Medusa documentation — https://docs.medusajs.com/
- Sylius core concepts — https://docs.sylius.com/the-book/index.html
- Shopify Admin API — https://shopify.dev/docs/api/admin
- Shopify inventory states — https://shopify.dev/docs/apps/build/orders-fulfillment/inventory-management-apps
- Stripe API reference — https://docs.stripe.com/api
- Stripe payment intents — https://docs.stripe.com/payments/payment-intents
- Stripe refunds — https://docs.stripe.com/refunds
- Stripe disputes — https://docs.stripe.com/disputes
- Stripe Tax — https://docs.stripe.com/tax
- Stripe idempotent requests — https://docs.stripe.com/api/idempotent_requests
- commercetools business units — https://docs.commercetools.com/api/projects/business-units
- commercetools prices — https://docs.commercetools.com/api/projects/products#prices
- Spryker quote approval — https://docs.spryker.com/docs/pbc/all/cart-and-checkout/202410.0/base-shop/feature-overviews/quote-approval-feature-overview.html
- Standard Webhooks — https://www.standardwebhooks.com/
- Fowler — Money pattern — https://martinfowler.com/eaaCatalog/money.html
- Fowler — Domain Event — https://martinfowler.com/eaaDev/DomainEvent.html
- Evans, *Domain-Driven Design*, Chapter 2 (Ubiquitous Language)
- ISO 4217 — https://www.iso.org/iso-4217-currency-codes.html
- RFC 7231 §4.2 — https://datatracker.ietf.org/doc/html/rfc7231#section-4.2
- AWS retail architecture (cross-reference for cloud-aligned terms) — https://aws.amazon.com/retail/architecture/
- Alibaba Cloud retail solutions (Taobao/Tmall vocabulary) — https://www.alibabacloud.com/solutions/retail
- Azure Architecture Center — e-commerce reference architecture — https://learn.microsoft.com/en-us/azure/architecture/
- MACH Alliance — composable commerce vocabulary — https://machalliance.org/
- Bryar & Carr, *Working Backwards* (2021) — Amazon's vocabulary discipline (PR/FAQ, six-pagers, ubiquitous language)
- Werner Vogels — *All Things Distributed* — https://www.allthingsdistributed.com/
- Vernon, *Domain-Driven Design Distilled* (2016) — concise reference for commerce modelers
