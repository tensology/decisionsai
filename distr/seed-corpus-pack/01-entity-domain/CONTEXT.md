# Context — Seed commerce glossary

## Glossary

### Customer
A real-world buyer the system serves. May exist as Guest without an Account.

### Account
Authenticated persistent record linked to one or more Customers (B2B hierarchy possible).

### User
Authentication principal. Orthogonal to Customer (admin Users are not Customers).

### Product
Sellable concept (name, media, taxonomy). Stock and price usually live on Variant.

### Variant
Specific configuration of a Product; carries SKU, price, and inventory levels.

### SKU
Identifier of a stockable unit (usually on Variant). Not the entity itself.

### Cart
Mutable purchase draft; expires; may be anonymous or owned by Customer.

### Quote
B2B offer that freezes terms (unlike Cart, which re-evaluates).

### Order
Committed Cart/Quote. Payment and fulfillment are orthogonal state axes.

### Line item / Order item
Variant + quantity + priced snapshot. Order items are immutable after placement.

### On-hand / Committed / Reserved / Available
Physical stock; promised to orders; held for carts; derived ATP respectively.

### Authorization / Capture / Refund / Void
Payment reservation; settlement; money return; cancel unused authorization.

### Fulfillment / Shipment
Sub-aggregate of items shipped together; carrier tracking unit.

### Channel
Scoped storefront/market for pricing, tax, and catalog.

> Implementation notes and ADRs do **not** belong here — see `copied-skills/*/domain-modeling`.
