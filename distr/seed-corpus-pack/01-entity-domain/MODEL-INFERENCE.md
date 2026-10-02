# Model relationship inference — SYNTHESIZED (hard rule)

When a request names an entity, **expand** it into the related subgraph before scaffolding. Do not build an Order table with no Customer, LineItems, or payment/fulfillment axes.

## Algorithm

1. Match named terms to `CONTEXT.md` / `entity-graph.yaml` / `vendor/ecommerce-foundations/skills/domain-vocabulary`.
2. Pull the entity’s `relations` plus orthogonal state axes.
3. Add Merrypak structural hooks (shop pages, checkout components, Django apps `shop`/`accounts`/`d3_api` as integration examples).
4. List **minimum viable related models** in the FRAC Requirements section.
5. Only drop a related model if user explicitly scopes it out.

## Expansion cheat-sheet

| User says | Always expand to include |
|-----------|--------------------------|
| **Order / orders** | Customer (or Guest), OrderItem/LineItem, Money snapshot, payment_state, fulfillment_state, Address (ship/bill), PaymentIntent, Fulfillment/Shipment (if physical) |
| **Cart** | LineItem, Customer/session, Variant, price revalidate, Reservation policy |
| **Product** | Variant, SKU, Media, Category/Collection, PriceList/channel, InventoryLevel |
| **Customer** | Account?, User?, Address[], PaymentMethod[], Order history link |
| **Checkout** | Cart→Order commit, tax, shipping method, payment, idempotency key |
| **Refund / return** | Order, payment capture ref, Return/RMA, restock decision, compensating inventory |
| **Inventory / stock** | Variant, Location, on_hand/committed/reserved/available |
| **Shipment** | Fulfillment, FulfillmentLine, OrderItem refs, Carrier, tracking |
| **Invoice / billing** | Order snapshot, bill-to Address, Money, tax lines, PDF convention (05) |
| **Admin products** | CRUD Product+Variant, status, open-dashboard list/detail stencil |
| **Dashboard** | Stats from Order/Payment aggregates — not a new orphan entity |

## Non-ecommerce

If profile is `trading-robot`, **do not** force commerce expansion. Use currency-trader docs instead.

## Output shape (put in FRAC)

```yaml
named: [Order]
inferred_models: [Customer, OrderItem, PaymentIntent, Fulfillment, Address]
axes: [payment_state, fulfillment_state, lifecycle_state]
explicitly_out_of_scope: []
```
