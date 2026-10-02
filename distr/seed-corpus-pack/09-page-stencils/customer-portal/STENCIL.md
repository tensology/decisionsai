# Stencil: Customer portal — SYNTHESIZED

Refs: Merrypak `pages/shop`, `pages/auth`, `components/checkout`.

## Surfaces
- Catalog list + product detail (variants, gallery, ATP)
- Cart drawer/page
- Checkout steps: address → shipping → payment → review
- Account: orders, addresses, profile
- Auth: login/register/forgot

## Layout
- Storefront chrome (header search, cart badge, account).
- Product detail: gallery + buy box sticky on desktop.
- Checkout: progress indicator; order summary aside.

## Validation / AC
- [ ] Variant selection required before add-to-cart
- [ ] Cart survives refresh; price revalidate messaging
- [ ] Checkout idempotent submit (no double charge UX)
- [ ] Guest vs account path explicit
- [ ] Order confirmation shows snapshot totals
- [ ] Premium gate green (brand tokens, not admin density)
