---
name: order-lifecycle
description: Use when designing the order state machine, allowed transitions, side effects per transition, cancellation paths, refund flows, or how payment status and fulfillment status interact. Load before adding an order status field, writing a transition, or modeling sub-orders/fulfillments. Vocabulary (order, line item, cart, payment status, fulfillment status, authorization, capture, refund, void, shipment, location) is imported from domain-vocabulary.
---

# Order lifecycle

The order is the longest-lived, most-referenced commerce artifact. It survives the cart, the payment, the fulfillment, the returns window, and accounting reconciliation — sometimes for years. Getting the state model wrong shows up as invalid states ("paid but cancelled but partially refunded but somehow also shipped"), as ambiguous business rules ("can the customer still cancel?"), and as integrations that disagree on what stage the order is in.

This skill frames the state design. It does not prescribe an exact set of states — the right set depends on the variation dimensions enumerated below.

Read the `domain-vocabulary` skill first. Terms used here without redefinition: *order*, *order item*, *cart*, *quote*, *customer*, *variant*, *location*, *channel*, *shipment*, *fulfillment*, *authorization*, *capture*, *void*, *refund*, *payment status*, *fulfillment status*, *order status*.

## Vocabulary additions

The general terms come from `domain-vocabulary`. The following are specific to order lifecycle.

**State transition** — A move from one state to another, triggered by an event or command. Has preconditions (the current state must permit the transition), a guard (conditions that must hold), and side effects (work that must happen as part of the transition).

**Side effect** — Domain work attached to a transition: decrementing inventory, capturing payment, emitting a `domain-event`, sending an email. Side effects are part of the transition's contract — skipping or partially executing them leaves the order in an inconsistent state.

**Compensating transition** — A transition that reverses the buyer-visible effect of a previous transition. Cancellation compensates a placed order; refund compensates a capture. Not the same as an "undo" — compensation is a forward operation, not a backward one. See `saga-compensation`.

**Order sub-aggregate** — A piece of the order modeled as its own aggregate: *fulfillment*, *shipment*, *refund*, *return*. Each carries its own lifecycle and connects to the parent order by reference.

**Partial fulfillment** — Some, but not all, *order items* shipped. Requires the fulfillment status to recognize "partially fulfilled" or to model fulfillment as a sub-aggregate where some have a different status.

**Partial payment** — Some, but not all, of the order's monetary value authorized or captured. Common in B2B (deposits, milestones) and in split payments (gift card + card).

**Order snapshot** — A frozen copy of order data captured at a specific transition (placement, fulfillment, invoice). Used for receipts and audit; preserves what was true at the time, even if the underlying *variant* or *price* has since changed.

**Payment intent / payment object** — The processor-side aggregate that wraps the buyer's payment for an order. One order may have one or many. Stripe calls this a `PaymentIntent`; others call it a `Charge` or `Transaction`. ([Stripe](https://docs.stripe.com/payments/payment-intents))

**Order line vs fulfillment line** — An *order item* is the line at order time; a *fulfillment line* is the same item at fulfillment time, possibly with split quantities across shipments.

## Canonical model

Treat the order as governed by three orthogonal state machines plus a high-level rollup.

```
                      Order
                        │
        ┌───────────────┼────────────────┐
        │               │                │
   payment_state   fulfillment_state   lifecycle_state
        │               │                │
   (auth, capture,  (unfulfilled,    (draft, placed,
    refund, void)    partial, ship,   completed,
                     deliver,         cancelled)
                     return)
```

The high-level *order status* is a derived view. The two underlying state machines progress independently:

```
payment_state:
   pending
     ↓ authorize
   authorized
     ↓ capture                ↓ void
   paid                    voided
     ↓ refund (partial)
   partially_refunded
     ↓ refund (remaining)
   refunded

fulfillment_state:
   unfulfilled
     ↓ allocate
   allocated
     ↓ pick & pack
   ready_to_ship
     ↓ ship
   shipped
     ↓ deliver
   delivered
     ↓ return (partial)
   partially_returned
     ↓ return (remaining)
   returned
```

The lifecycle rollup is computed from the two underlying states plus cancellation:

| payment | fulfillment | lifecycle |
|:---|:---|:---|
| pending | unfulfilled | placed (awaiting payment) |
| authorized | unfulfilled / allocated | placed |
| paid | unfulfilled / allocated / ready_to_ship | in_progress |
| paid | shipped | shipped |
| paid | delivered | completed |
| refunded | returned | refunded |
| any | any (with explicit cancellation) | cancelled |
| paid | partially_returned | partially_completed |

Invariants:

1. **Transitions are explicit.** Every state change names the transition and runs its side effects atomically (or via a saga — see `saga-compensation`).
2. **Order items are immutable after placement.** Adding or removing items after the order is placed creates a new order or an amendment, not an edit.
3. **Snapshots, not pointers.** Address, price, tax, discount are snapshotted on the order at placement. Subsequent variant or list changes do not retroactively affect the order.
4. **Idempotent transitions.** Re-triggering a transition that already happened is a no-op, not an error. Webhooks and retries depend on this.
5. **No invalid combinations.** A "delivered but not paid" order is either a valid state (B2B net-30) or a bug — make the choice explicit at the state-machine level.

## Variation dimensions

- **Business model** — B2C (linear lifecycle, immediate payment), B2B (approval steps, net terms, partial payments, milestones), marketplace (per-vendor sub-orders with independent lifecycles), subscription (recurring "orders" derived from a plan).
- **Product type** — Physical (full fulfillment lifecycle), digital (skip ship/deliver — `paid → entitled`), service (booking, no shipment, scheduled delivery).
- **Payment timing** — Pay-up-front (typical B2C), pay-on-shipment (some B2B), pay-on-delivery (cash on delivery, regulated markets), net terms (invoiced, due in N days), milestone (deposit + balance).
- **Cancellation policy** — Free cancellation any time before ship, free until a deadline, charged cancellation, no cancellation. Reflected in which lifecycle states allow the `cancel` transition.
- **Return policy** — Open returns (any time), windowed returns (30 days from delivery), no returns, conditional (digital: no; physical: yes; perishable: case-by-case).
- **Fulfillment topology** — Single shipment (all items together), allowed-split (multi-warehouse, multi-shipment), forced-split (each line ships independently from its location).

## Decision frameworks

### Single state field vs orthogonal axes

Two positions:

| Position | Shape | When to choose |
|:---|:---|:---|
| Single combined `status` enum | One field with values like `pending_payment`, `paid_unfulfilled`, `paid_partially_shipped`, ... | Tiny domains; near-trivial flows; never going to grow. Almost never the right choice for real commerce. |
| Orthogonal axes (payment + fulfillment + lifecycle) | Three fields, transitions per axis | Anything non-trivial. Shopify, commercetools, Medusa, Sylius all converge here. |

Default to orthogonal axes. The single-status approach looks simpler on day one and becomes a swamp by year one as states multiply combinatorially.

### How to model fulfillment

Three positions:

1. **Status field on the order.** A single `fulfillment_status` field; partial fulfillment expressed as `partially_fulfilled`. Simple; loses detail (which items? from where? in which shipment?).
2. **Sub-aggregate (fulfillment as its own entity).** Each fulfillment is a record with its own lifecycle, items, location, and shipments. Tracks split shipments naturally; required for marketplace and multi-warehouse.
3. **Per-line fulfillment status.** Each *order item* carries its own fulfillment state. Most granular; most code.

Choose 1 only for single-shipment, single-location systems. Choose 2 as the default for anything with multi-location or split shipments. Choose 3 when partial-quantity fulfillment on the same line is common (selling units in bulk where 80 out of 100 ship now and 20 later).

### Side effects and transition atomicity

A transition runs side effects:

- `pay` → capture payment, decrement inventory commitment, emit `OrderPaid`, send receipt.
- `ship` → generate tracking, decrement on-hand, emit `OrderShipped`, send tracking email.
- `cancel` → release inventory commitments, refund or void payment, emit `OrderCancelled`, send cancellation email.

Two structural choices for executing the side effects:

| Approach | Behavior | Trade-off |
|:---|:---|:---|
| Synchronous, in-transaction | All side effects run in the same DB transaction as the state change | Atomic but blocks on slow side effects (external APIs); commits couple to external availability |
| Saga | State change commits first; side effects run as a workflow with compensations | Decoupled and resilient; requires explicit compensation logic; eventual consistency window |

Most non-trivial systems combine: in-transaction for cheap local effects (inventory commitment in the same DB), saga for cross-system effects (payment capture, email, shipping label). See `saga-compensation`.

### Cancellation policy

Cancellation is the most context-sensitive transition. Pin down four questions:

1. **Who can cancel?** Buyer (only before ship), merchant (any time), processor (chargeback), system (fraud, payment failure).
2. **When?** Lifecycle states where `cancel` is allowed; states where it is not (already shipped, already delivered, already returned).
3. **Fees and refunds?** Free cancellation (full refund), partial refund (restock fee), no refund (digital, non-refundable).
4. **Side effects?** Release inventory, void or refund payment, notify carrier (if shipped already, cancellation may be an in-transit recall), emit event.

Encode the rules explicitly. Hidden "if state in [X, Y, Z] and customer is gold-tier" logic across multiple handlers is unmaintainable.

### Idempotency at the order level

Every transition must be idempotent. The mechanics:

- Identify the transition with a key (event id from a webhook, idempotency-key header from the API).
- Persist the key with the transition outcome.
- On re-arrival, look up the key — return the prior outcome instead of re-executing.

See `idempotency` and `webhooks`. The order layer is the most common place to wire idempotency because payment confirmations and shipping updates arrive via webhook and re-deliver freely.

### Order amendments

Two positions on "the buyer wants to change something after placement":

1. **No amendments — cancel and re-order.** Cleanest model; clearest accounting; worst UX for trivial changes (address typo).
2. **Bounded amendments.** Specific fields editable (shipping address before label printed; quantity reduction with refund); each amendment captured as an event on the order.

Pick 2 only if the operational cost is justified by the volume of trivial changes. Even then, narrow the amendable surface aggressively.

### Order vs invoice

Two positions:

1. **Order is the invoice.** The order document, with totals, taxes, line items, is the financial record. Sufficient for B2C.
2. **Invoice is separate.** The order is the fulfillment-and-customer record; the invoice is a separate financial document with possibly different timing (issued on ship, monthly, on net-30 due date). Required for B2B with billing terms.

If the business has net-terms billing, partial billings, milestone invoices, or consolidated invoices, the invoice cannot be the order. Model them as separate aggregates linked by reference.

## Orchestrating side effects across systems

Once the order's side effects extend beyond the local database — payment capture at a processor, label generation at a carrier, email at a transactional provider, inventory commitment at a warehouse — the in-transaction approach fails. The processor is down, the carrier API is slow, the email service rate-limits. The order transition cannot synchronously couple to all of them.

The pattern across cloud-native commerce platforms is the *saga* ([AWS Step Functions saga sample](https://docs.aws.amazon.com/step-functions/latest/dg/sample-project-saga.html), [Azure saga reference](https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/saga/saga), [microservices.io saga](https://microservices.io/patterns/data/saga.html), [Alibaba Seata](https://www.alibabacloud.com/help/en/seata/)):

**Orchestrated saga.** A workflow engine (AWS Step Functions, Azure Durable Functions, Temporal, Cadence, Camunda) drives the sequence: place order → capture payment → commit inventory → generate label → send email. Each step is idempotent (see `idempotency`); on failure, the engine triggers compensating steps in reverse order (refund payment, release inventory). The engine persists workflow state so a crash mid-flow resumes correctly.

**Choreographed saga.** Each service reacts to events published by upstream services; there is no central coordinator. Loosely coupled but harder to reason about: tracing an order through five event-driven services without a coordinator requires investment in distributed tracing.

The vocabulary in `domain-events`-style integration applies: events are past tense, idempotent consumers (see `idempotency`), at-least-once delivery (see `webhooks`). The order aggregate publishes `OrderPlaced`, `OrderPaid`, `OrderShipped`; downstream services consume and react.

Two cross-cutting rules:

- **Compensation is a forward action, not a backward one.** Refunding a captured payment is a new `PaymentRefunded` operation with its own fees and timing — it is not "undoing" the capture. The compensation function does not assume the original transaction can be reversed; it issues a new transaction whose semantics counteract the original. ([Azure compensating transaction](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction))
- **Idempotency at every step.** A workflow that retries must not double-execute. Each step gets a key derived from the workflow run id; the executing service rejects replays.

See `saga-compensation` (v0.2) for the full pattern.

## Anti-patterns

- **Single string `status` field with 20+ values.** Combinatorial explosion of `paid_pending_shipped_partially_refunded`-style values. Refactor to orthogonal axes. Symptom: enum has 30 entries and growing; every new feature requires a new value.
- **No explicit transitions; just `update.status = "x"`.** Loses the side-effect contract; loses preconditions; loses the audit log. Each transition is a named method with a guard, not a bare assignment.
- **Cancelling doesn't release inventory.** The order is cancelled at the order aggregate; the inventory commitment is forgotten. Stock count silently lies. Treat inventory release as a transition side effect.
- **Cancelling doesn't refund or void.** The buyer is told "cancelled" but the charge stays. Either refund or void must be part of the cancel transition.
- **Refunding past the captured amount.** Refunds for $X when only $Y < X was captured. Processor will reject; the order shows "refund failed" with no remediation path. Validate refund amount against remaining captured.
- **Partial shipment that doesn't update fulfillment status.** Two of three items ship; status remains `unfulfilled`. Customers and carriers disagree with the order. Update on each fulfillment event.
- **No snapshot of price/tax at placement.** Variants change price; old orders silently re-total. Reports become non-reproducible. Snapshot at placement; never compute totals from current data on old orders.
- **Order edit changes the order in place after placement.** Loses the original commitment; breaks accounting reconciliation. Model as amendments (separate events on the same order) or as cancel-and-replace.
- **Payment webhook handlers that aren't idempotent.** Stripe retries the same event; double capture results, or double inventory decrement. Persist event ids; check before processing. See `idempotency` and `webhooks`.
- **Treating chargeback as a refund.** A chargeback is processor-initiated, has its own state and timing, and may have different fee implications. Model chargebacks explicitly (see `payment-flows`); do not collapse into refund.
- **Marketplace order with one set of states for the whole order.** Different vendors have different fulfillment timing, returns, and cancellation rules. Model per-vendor sub-orders, each with its own lifecycle.
- **Cancellation allowed in any state.** Cancelling a delivered order is not cancellation, it's a return. Cancelling a refunded order is a no-op-or-bug. Restrict the transition to states where it is meaningful.
- **B2B "completed" the moment payment captures.** B2B completion is delivery + invoice + payment per the contract. Capturing payment in advance and marking complete loses the rest of the lifecycle.
- **Side effects on read.** Computing or fixing state on a `GET` request — refreshing fulfillment_status from external system, "lazy" payment recheck. Side effects belong on writes; reads must be pure.
- **Conflating order id and payment id.** The processor's payment id is not the order id; orders can have multiple payments (retry, split tender) and vice versa (one payment authorization that covers multiple subscriptions). Treat them as independent identifiers with explicit relationships.

## Sources

- Sylius order state machine — https://docs.sylius.com/the-book/orders/states
- Sylius order concepts — https://docs.sylius.com/the-book/orders/orders
- Medusa order module — https://docs.medusajs.com/resources/commerce-modules/order
- Shopify order resource (financial_status, fulfillment_status) — https://shopify.dev/docs/api/admin-rest/latest/resources/order
- Shopify order edits — https://shopify.dev/docs/api/admin-graphql/latest/mutations/orderEditBegin
- Stripe PaymentIntent lifecycle — https://docs.stripe.com/payments/paymentintents/lifecycle
- Stripe refunds — https://docs.stripe.com/refunds
- commercetools order — https://docs.commercetools.com/api/projects/orders
- Fowler — State pattern — https://martinfowler.com/eaaCatalog/state.html
- Evans, *Domain-Driven Design*, Chapter 10 (Aggregates)
- Vernon, *Implementing Domain-Driven Design*, Chapter 10 (Aggregates) and Chapter 8 (Domain Events)
- AWS Step Functions — saga sample project — https://docs.aws.amazon.com/step-functions/latest/dg/sample-project-saga.html
- AWS Prescriptive Guidance — saga pattern — https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/saga.html
- Azure Architecture Center — saga reference — https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/saga/saga
- Azure compensating transaction pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction
- microservices.io — saga pattern — https://microservices.io/patterns/data/saga.html
- Alibaba Cloud — Seata distributed transaction — https://www.alibabacloud.com/help/en/seata/
- DoorDash Engineering — order orchestration patterns — https://doordash.engineering/
- Amazon Science — order pipeline and fulfillment optimization — https://www.amazon.science/
- Werner Vogels — *All Things Distributed* on order eventual consistency — https://www.allthingsdistributed.com/
- Walmart Global Tech — omnichannel order routing (BOPIS, ship-from-store) — https://medium.com/walmartglobaltech
- Mercado Libre Engineering — order state machines across LATAM markets — https://medium.com/mercadolibre-tech
- Target Tech — order orchestration in BOPIS-heavy operations — https://tech.target.com/
- Nygard, *Release It!* (2nd ed., 2018) — resilience patterns for order pipelines
