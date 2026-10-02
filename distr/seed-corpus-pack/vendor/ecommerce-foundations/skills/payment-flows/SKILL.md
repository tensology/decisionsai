---
name: payment-flows
description: Use when integrating any payment processor or designing how the system handles authorization, capture, sale, void, refund, dispute, hosted vs embedded checkout, webhook confirmation, tokenization, and PCI scope. Load before adding a payment provider, deciding when to capture, modeling stored payment methods, or wiring 3DS/SCA. Vocabulary (authorization, capture, sale, void, refund, chargeback, hosted checkout, embedded checkout, tokenization, payment status, order) is imported from domain-vocabulary.
---

# Payment flows

Payment is the most-regulated, most-error-amplifying, most-vendor-coupled layer of commerce. Mistakes here cost money directly — chargebacks, double captures, lost transactions, compliance fines. The pattern of avoiding mistakes is the same pattern: separate intent from settlement, treat the processor's webhook as the truth, never store what PCI doesn't permit.

This skill frames the integration decisions. It does not pick a processor — that choice depends on geography, mix, and partnership terms.

Read the `domain-vocabulary` skill first. Terms used here without redefinition: *order*, *payment status*, *authorization*, *capture*, *sale*, *void*, *refund*, *chargeback*, *tokenization*, *hosted checkout*, *embedded checkout*.

## Vocabulary additions

The general terms come from `domain-vocabulary`. The following are specific to payments.

**Payment processor (PSP)** — The vendor that authorizes, captures, and settles payments: Stripe, Adyen, Braintree, Mercado Pago, PayU, Worldpay, Cybersource, regional providers. Distinct from the *acquiring bank* (the bank that holds the merchant's account and settles funds) and the *issuing bank* (the bank that issued the buyer's card).

**Payment method** — The instrument the buyer uses: card (Visa, Mastercard, Amex), wallet (Apple Pay, Google Pay, PayPal), bank transfer (ACH, SEPA, Pix, OXXO), local payment method (iDEAL, Klarna, Affirm, Boleto). Each has different flows, settlement times, and fee structures.

**Payment intent** — A processor-side aggregate representing the buyer's intent to pay for an *order*: tracks the amount, currency, payment method, status (requires-payment-method, requires-confirmation, requires-action, processing, succeeded, canceled), and any actions the buyer must take. Stripe formalized this term; most modern processors follow. ([Stripe](https://docs.stripe.com/payments/payment-intents))

**Charge** — A specific, settled transaction within a payment intent. One intent can have multiple charges (retry after card declined; rebill after auth expiry); each charge has its own id and outcome.

**Token (payment token)** — A non-sensitive reference to a payment method, returned by the processor when the buyer's actual card data is submitted directly to it. The merchant stores the token; the actual PAN never reaches the merchant's systems. ([PCI DSS](https://www.pcisecuritystandards.org/))

**Card-on-file (saved payment method)** — A *token* persisted for repeat use. The buyer associates a payment method with their account once; subsequent payments use the token. Regulatory rules (SCA in Europe, CITs/MITs) constrain how saved methods are used.

**SCA (Strong Customer Authentication)** — Regulatory requirement (EU PSD2) that requires the buyer to authenticate (typically via 3DS2) for certain transactions. Triggered by amount thresholds, transaction type (customer-initiated vs merchant-initiated), and exemptions.

**3DS / 3DS2 (Three-Domain Secure)** — The protocol used to satisfy SCA. Adds a challenge step (push notification, OTP, biometrics) during the transaction. 3DS2 is the modern frictionless-when-possible variant; 3DS1 is deprecated.

**CIT (Customer-Initiated Transaction) vs MIT (Merchant-Initiated Transaction)** — A regulatory distinction: CITs require the buyer's active presence at the moment of payment; MITs are triggered by the merchant from a saved token (subscription renewals, retries). MITs are exempt from SCA but require prior buyer consent.

**Settlement** — The actual movement of money from the issuing bank, through the card network, through the acquiring bank, to the merchant's account. Happens hours or days after capture. Distinct from the *capture* event the processor reports.

**Reconciliation** — The merchant-side process of matching processor reports (charges, refunds, fees, disputes, payouts) against the system's order and accounting records. Daily or per-payout; differences must be explained.

**Authorization hold expiry** — Authorizations are not permanent. Typical windows: 7 days for cards (varies by issuer and method), shorter for some networks. After expiry, the auth lapses and capture is no longer possible — a new transaction is needed.

**Payment retry** — A second attempt at a previously declined or failed payment, typically with the same payment method. Distinct from dunning (multiple retries on a recurring schedule for subscriptions).

**Webhook event** — An HTTP callback from the processor reporting a state change in a payment intent: succeeded, requires-action, payment-failed, refunded, dispute-created. See `webhooks` for the cross-cutting pattern. ([Stripe webhooks](https://docs.stripe.com/webhooks))

## Canonical model

Most processors converge on the same shape, though names differ:

```
Order (1) ──< has one or more >── PaymentIntent (N)
                                    │
                                    ├── id (processor)
                                    ├── amount, currency
                                    ├── payment_method (token)
                                    ├── state (requires_payment, processing,
                                    │          succeeded, canceled, requires_action)
                                    ├── client_secret (for embedded confirmation)
                                    └── charges: [Charge]

Charge
  ├── id (processor)
  ├── amount captured
  ├── refunded (sum of refunds)
  ├── outcome (succeeded, failed, ...)
  └── failure_code, failure_message
```

Lifecycle viewed as state transitions:

```
   created
      ↓ confirm (sync or with 3DS challenge)
   requires_action ──→ buyer completes 3DS
      ↓
   processing
      ↓ processor settles or rejects
   succeeded                  payment_failed
      ↓ capture (if separate from confirm)
   captured
      ↓ refund (full / partial)
   refunded / partially_refunded
```

Cross-cutting invariants:

1. **The processor is the source of truth.** The system's payment status is a projection of webhook events, not the source.
2. **Synchronous response is advisory, not authoritative.** The HTTP response to `confirm` may say "succeeded" but the actual settlement is the webhook. Code must handle both signals — and not double-count when both arrive.
3. **Idempotency keys on every request.** A retried `confirm` must not produce two charges. See `idempotency` for the cross-cutting pattern. ([Stripe](https://docs.stripe.com/api/idempotent_requests))
4. **Webhooks are at-least-once.** Every event handler must be idempotent. The same `payment_intent.succeeded` will arrive twice or more across retries.
5. **Never store PAN.** Card numbers go directly from the buyer to the processor. The merchant stores tokens, never raw card data. Anything else is PCI DSS violation.

## Variation dimensions

- **Capture timing** — Sale (auth + capture in one step) vs auth-then-later-capture vs auth-then-capture-on-fulfillment. Depends on how long between order and shipment.
- **Integration model** — Hosted (processor's UI), embedded (processor's JS in merchant's page), Payment Request API (browser-native), redirect (bank-hosted flow for some methods), API-only (server-to-server, no buyer UI involvement — for MITs).
- **Payment methods** — Cards-only, multi-method (wallets, BNPL, local methods), region-specific bundles. Each method has different flow, timing, and reversibility.
- **Recurring vs one-time** — One-off payments are simpler; recurring adds dunning, retry logic, and SCA exemption tracking.
- **Merchant model** — Single-merchant (your store, your processor account), marketplace (you facilitate; payments split between you and vendors via Stripe Connect, Adyen MarketPay, etc.), platform (you're a PSP-as-aggregator).
- **Geography** — Single-country (one processor often enough) vs multi-region (multiple processors needed for coverage + cost optimization).

## Decision frameworks

### Hosted vs embedded vs Payment Request API

| Approach | PCI scope | UX control | Implementation cost | When to choose |
|:---|:---|:---|:---|:---|
| Hosted checkout (Stripe Checkout, PayPal redirect, Mercado Pago Wallet) | SAQ-A (minimal) | Low — redirect or modal in processor UX | Lowest | First integration, low-engineering teams, regulated markets |
| Embedded (Stripe Elements, Payment Element, drop-in UI) | SAQ-A-EP | High — merchant controls the page | Moderate | Brand-controlled checkout, conversion-focused stores |
| Payment Request API (Apple Pay, Google Pay JS) | Depends on flow | Native browser UX | Moderate (per device) | Mobile-first, low-friction one-click flows |
| Full custom (raw card input via processor's JS tokenizer) | SAQ-D | Total | High; specialized knowledge required | Rare; only when no other option matches the brand |

The trend across the industry is from hosted to embedded as embedded tooling has matured. Start hosted; migrate to embedded only when a conversion-rate analysis justifies the engineering and PCI burden.

### Sale vs auth-then-capture

| Pattern | Behavior | When to choose |
|:---|:---|:---|
| Sale (auth + capture together) | One step; funds move immediately | Digital goods (delivered immediately), B2C with same-day shipping promise, low fulfillment latency |
| Auth, capture on ship | Authorize at order; capture when the package leaves | Standard physical-goods retail; reduces refunds (no charge if order can't fulfill) |
| Auth, capture incrementally | Authorize total; capture per fulfillment | Multi-shipment orders; reservation systems (hotels, car rentals, marketplaces) |

Constraints:

- **Auth expiry.** If capture is delayed past the auth window (7 days typical for cards), re-authorize before capturing. Failing to do so means capture fails and the buyer's card must be charged anew — a new SCA challenge in some regions.
- **Some methods don't support separate auth/capture.** Local bank-transfer methods, BNPL providers, some wallets. For these, sale is the only option; capture timing is dictated by the method.
- **Marketplace.** When the merchant facilitates payment between buyer and a third-party vendor (Stripe Connect destination charges), capture timing is constrained by the platform's payout flow.

### Webhook-driven vs synchronous-driven completion

The bug here is the most common payment bug in the industry. Pick one as authoritative; the other is advisory.

| Position | Source of truth | Pitfall |
|:---|:---|:---|
| Webhook-driven | The `payment_intent.succeeded` webhook completes the order | The buyer may see the success page before the webhook lands; UX must reconcile |
| Synchronous-driven | The HTTP response to `confirm` completes the order | Webhook arrives later and may double-trigger; must be idempotent |

The canonical pattern: synchronous response is used to redirect the buyer to a success page; the webhook is the system's authoritative trigger for the `pay` transition on the order. The order's `pay` transition is idempotent — whichever signal arrives second is a no-op.

### Saved payment methods (card-on-file)

Three positions:

1. **No card-on-file.** Buyer enters card details each time. Friction-heavy; cleanest from a compliance angle.
2. **Card-on-file with explicit re-auth.** Buyer must re-authenticate (SCA) on each use. Lower friction than re-entry; some friction remains.
3. **Card-on-file as MIT.** Buyer consents once; subsequent charges run as merchant-initiated (subscription, auto-reload, one-click). Lowest friction; requires explicit consent capture and processor-side MIT setup.

For B2C non-recurring stores, 2 is common. For subscriptions and BNPL, 3 is required. For B2B with finance/AP-managed payment, 1 or 2 (3 is rare due to corporate control).

When implementing 3, regulatory diligence applies: the consent must be captured at the original transaction, the agreement terms must be in the customer's record, and the MIT flag must travel with each subsequent charge so the processor classifies it correctly.

### Multi-PSP routing

Three positions:

1. **Single PSP.** One processor for everything. Simplest; concentrated risk; geographic coverage may be limited.
2. **Geographic routing.** Different PSPs for different regions (Stripe in US/EU, Mercado Pago in LATAM, PayU in India). Optimizes coverage and fees.
3. **Cascading.** Failed transactions retry on a secondary PSP. Increases success rate at the cost of complexity.

The system must abstract over the processor: an internal `PaymentService` interface with PSP-specific adapters. Avoid leaking processor-specific objects (e.g., `Stripe.Customer`) into the domain layer.

### Refund vs void vs cancel

These are different operations with different timings and outcomes:

| Operation | Pre-condition | Effect | Reversibility |
|:---|:---|:---|:---|
| Void | Authorization exists; not yet captured | Cancels the authorization; no charge; no funds movement | None needed — never happened |
| Refund | Capture happened | New transaction; funds return to buyer; fees may or may not be refunded depending on PSP | None (cannot un-refund) |
| Cancel | Order-level concept; map to void or refund depending on payment state | See order-lifecycle | — |

When in doubt: prefer void over refund when possible. Voids are cheaper, faster, and have no fees in most processor relationships.

### Disputes and chargebacks

Treat as their own state machine, separate from refund:

```
   inquiry → needs_response → under_review → won
                                          ↘ lost
                            → accepted (no response)
```

Implications for the order:

- Funds are removed from the merchant's account at dispute creation (held in reserve until resolution).
- The merchant has a deadline (typically 7–21 days) to respond with evidence.
- Most disputes are lost regardless of evidence; the structural fix is to prevent the conditions (fraud screening, clear refund policy, recognizable descriptor).
- Chargeback fees apply regardless of outcome.

Model disputes as a separate aggregate linked to the order; do not silently mark the order "refunded" when a dispute is lost (it's a different financial event with different accounting treatment).

## Regional payment methods

The canonical model above is processor-agnostic, but real implementations face a region-specific reality: the dominant payment method changes drastically by geography, and each method has its own flow, fee structure, and reversibility profile. A US-centric design that assumes "Visa/Mastercard + Apple Pay" will fail in markets where those are not the dominant rails.

Key regional landscapes:

| Region | Dominant rails | Notable flow specifics |
|:---|:---|:---|
| US / Canada | Visa, Mastercard, Amex, Apple Pay, Google Pay; ACH for high-value | Auth/capture model standard; instant refund; chargeback driven |
| EU | Cards (SCA required), SEPA (account-to-account), iDEAL (NL), Bancontact (BE), Sofort, P24 (PL) | SCA 3DS2 challenge per PSD2; SEPA refunds take days; some methods are push (buyer-initiated transfer) |
| UK | Cards, Open Banking (PIS), Apple/Google Pay | Open Banking flows are buyer-redirected and auth-then-settle |
| Brazil | Cards (parcelado: installments), Pix (instant transfer), Boleto (cash voucher) | Pix is instant and irreversible; Boleto has multi-day pay window with eventual confirmation; parcelado obligations are processed by the issuer |
| Mexico | Cards, OXXO (cash voucher), SPEI (transfer) | OXXO confirmations arrive via webhook hours after voucher generation |
| LATAM (broad) | Cards, BNPL (Mercado Crédito, Addi), local wallets (Mercado Pago, PicPay) | High-fee cards; alternative methods often cheaper |
| China | Alipay, WeChat Pay; cards are minority | Hosted-flow redirect or QR-code scan; settlement model differs from cards; Alibaba/Ant Group's Alipay docs are authoritative ([Alipay docs](https://global.alipay.com/docs/ac/Platform/intro)) |
| India | UPI (account-to-account), cards, wallets, BNPL | UPI is the dominant rail; auth-and-debit is the same step; no separate capture |
| Southeast Asia | E-wallets (GrabPay, GoPay, Touch 'n Go), local bank transfer, cards | Wallet flow is QR or app-redirect; settlement aggregator-dependent |
| Africa | Mobile money (M-Pesa, MTN MoMo), cards in urban centers, USSD | Mobile money has its own confirmation flow via SMS/USSD callback |

Implications for the canonical model:

- **Some methods do not support separate auth/capture.** Pix, UPI, Alipay, M-Pesa — for these, only "sale" is available; capture timing is dictated by the method.
- **Some methods are not reversible.** Pix and UPI are irreversible by design; refund means a separate transfer back to the buyer, possibly requiring buyer consent.
- **Some methods are push (buyer-initiated).** SEPA, Open Banking, Boleto — the merchant generates a payment instruction; the buyer completes the transfer through their bank. Confirmation arrives via webhook hours later.
- **Settlement timing varies by an order of magnitude.** Card auth: instant. SEPA: 1–3 business days. Boleto: up to a week.

The system architecture must abstract over these so the order can accept a payment intent in any method without bespoke per-method order code. The PSP's normalization (Stripe, Adyen, Worldpay all aggregate many methods) is the most common abstraction layer; in markets without strong aggregator coverage (LATAM beyond Stripe, Africa), the merchant builds the abstraction.

## Anti-patterns

- **Storing card numbers, CVVs, or full magnetic stripe data.** PCI DSS violation. CVV cannot be stored at all, even encrypted; PAN can be stored only with very specific controls. Tokenize via the processor instead. Symptom: a `card_number` column exists anywhere in the database.
- **Completing the order on synchronous response only.** Webhook arrives 5 seconds later and re-triggers logic; double inventory decrement, duplicate emails. Handle both signals; make the transition idempotent.
- **Completing the order on webhook only, with no UX bridge.** Buyer sees a loading spinner indefinitely while waiting for the webhook. Use synchronous response for UX, webhook for system truth, with a polling fallback for delays.
- **No idempotency key on `confirm` or `capture` requests.** Network retry produces two charges. Always include an idempotency key — typically derived from the order id or a per-attempt UUID.
- **Treating webhook order as guaranteed.** Webhook events may arrive out of order, especially under retry. Handle each event by checking the *current state* of the underlying object (refetch from processor or check local state), not by assuming sequence.
- **Webhook handlers without signature verification.** Anyone can POST to the webhook URL and trigger order completion. Verify the signature per the processor's documentation. See `webhooks`.
- **Refunding past the captured amount.** Processor rejects; the order shows a confusing "refund failed" with no clear remediation. Validate refund amount against `captured - already_refunded` before requesting.
- **One PSP-specific object leaks into the domain.** A Stripe `Charge` becomes the order's payment record. Switching processors requires touching every reference. Wrap PSP responses in a domain `Payment` aggregate at the boundary.
- **No reconciliation against PSP reports.** Local order totals are taken as truth; PSP-reported fees, refunds, and disputes never compared. Discrepancies accumulate undetected. Schedule daily reconciliation against PSP exports.
- **Capturing on cart submission instead of on order confirmation.** Buyer thinks they're checking out; card is already charged before the merchant has even committed to the order. The order must be created (and inventory committed) before the buyer is charged.
- **Saving cards without explicit buyer consent.** Regulatory and reputational risk. Capture consent (checkbox + clear copy) on the transaction that creates the token; record the consent on the customer record.
- **Treating chargeback as a refund event.** Different accounting, different fees, different timing, different recovery options. Model separately.
- **Hardcoded delay for "wait for payment to settle".** Settlement is asynchronous and variable. Drive completion by webhook, not by a `sleep`.
- **Polling the PSP every N seconds for status.** Webhook is the intended mechanism. Polling burns rate limits and lags reality. Use polling only as a fallback when webhook delivery is delayed past a threshold.
- **Mixing CIT and MIT without flagging.** Subscription renewal sent as a CIT request triggers an SCA challenge with no buyer present; payment fails. Tag MITs explicitly per processor docs.

## Sources

- Stripe Payment Intents — https://docs.stripe.com/payments/payment-intents
- Stripe online payments overview (hosted vs embedded) — https://docs.stripe.com/payments/online-payments
- Stripe Payment Element — https://docs.stripe.com/payments/payment-element
- Stripe Checkout (hosted) — https://docs.stripe.com/payments/checkout
- Stripe refunds — https://docs.stripe.com/refunds
- Stripe disputes — https://docs.stripe.com/disputes
- Stripe SCA — https://docs.stripe.com/strong-customer-authentication
- Stripe webhooks — https://docs.stripe.com/webhooks
- Stripe idempotent requests — https://docs.stripe.com/api/idempotent_requests
- Stripe — saved payment methods — https://docs.stripe.com/payments/save-and-reuse
- Adyen payment result codes — https://docs.adyen.com/online-payments/payment-result-codes
- Mercado Pago Checkout — https://www.mercadopago.com/developers/en/docs
- PCI Security Standards Council — https://www.pcisecuritystandards.org/
- W3C Payment Request API — https://www.w3.org/TR/payment-request/
- EU PSD2 / SCA — https://www.europeanpaymentscouncil.eu/sites/default/files/kb/file/2019-10/EPC172-19%20v1.0%20Guidance%20on%20SCA.pdf
- AWS Payment Cryptography — https://docs.aws.amazon.com/payment-cryptography/
- Alibaba / Ant Group — Alipay platform docs — https://global.alipay.com/docs/ac/Platform/intro
- Stripe Engineering — payment system design — https://stripe.com/blog/engineering
- Mercado Pago — developer docs (LATAM-specific methods) — https://www.mercadopago.com/developers/en/docs
- UPI specification (India) — https://www.npci.org.in/what-we-do/upi/product-overview
- Pix (Brazil) — https://www.bcb.gov.br/en/financialstability/pix_en
- Mercado Libre Engineering — LATAM payment-method diversity, Mercado Pago design — https://medium.com/mercadolibre-tech
- Coupang Engineering — Korean payment landscape — https://medium.com/coupang-engineering
- Adyen — Insights on multi-acquirer routing — https://www.adyen.com/blog
- Stripe — engineering posts on PaymentIntents and SCA — https://stripe.com/blog
- a16z fintech writing (Marc Andreessen / Angela Strange) — embedded payments in commerce — https://a16z.com/
- Patrick Collison — public talks on payment infrastructure — https://stripe.com/blog
