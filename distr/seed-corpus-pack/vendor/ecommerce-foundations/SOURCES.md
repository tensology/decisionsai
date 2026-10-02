# SOURCES.md

Curated reference bibliography for AI agents authoring skills in this repository. Use this file as your starting research map — not as a substitute for verifying claims against primary sources before stating them in a skill.

## How to use this file

1. **Before writing or editing a skill,** read the entries in the relevant section below and visit at least two independent sources. Triangulating prevents platform leakage and bias.
2. **Cite by URL, not by reputation.** Every non-trivial claim in a skill must link to one of these sources or be marked as inferred.
3. **Prefer primary references over secondary commentary.** Use vendor documentation, specifications, and books by recognized authors. Blog posts are useful for context but should not be the sole source of a pattern.
4. **When sources disagree,** present both positions in the skill — do not pick a winner. The disagreement itself is information for the reader.
5. **When a source post-dates this file,** consult it but verify the URL still resolves. The web reorganizes.

---

## Primary references (consult for every skill)

These cover most of the commerce domain. Skim the homepage to learn each source's coverage; deep-link to specific pages when citing.

| Source | URL | Coverage strengths |
|:---|:---|:---|
| Medusa documentation | https://docs.medusajs.com/ | Modern headless commerce architecture, workflows with compensation, modular commerce primitives |
| Sylius documentation | https://docs.sylius.com/ | Checkout and order state machines, B2B/B2C nuances |
| Stripe documentation | https://docs.stripe.com/ | Payment flows, idempotency, webhooks, refunds, disputes, tax — de-facto industry reference |
| Shopify developer documentation | https://shopify.dev/docs | Inventory states, fulfillment, returns, multi-store |
| commercetools documentation | https://docs.commercetools.com/ | Enterprise B2B, multi-channel, multi-region pricing |
| Spryker documentation | https://docs.spryker.com/ | B2B quote-to-order, complex catalog modeling |
| Standard Webhooks | https://www.standardwebhooks.com/ | Industry specification for webhook design, adopted by OpenAI, Anthropic, Kong, Svix, Supabase, Vanta |

## Cloud-provider architecture references

Hyperscaler documentation for commerce-related patterns. Strong for at-scale operational guidance (caching, queuing, idempotency, orchestration) that platform-specific commerce docs gloss over.

| Source | URL | Coverage strengths |
|:---|:---|:---|
| AWS Builders' Library | https://aws.amazon.com/builders-library/ | First-party essays on idempotency, retries, throttling, exactly-once processing, caching — written by AWS principal engineers |
| AWS Prescriptive Guidance | https://docs.aws.amazon.com/prescriptive-guidance/ | Cross-domain patterns including saga, idempotent consumer, transactional outbox, queue-based load leveling |
| AWS Architecture Center — retail | https://aws.amazon.com/retail/architecture/ | Reference architectures for cart, catalog, recommendation, order management |
| AWS Well-Architected Framework | https://docs.aws.amazon.com/wellarchitected/ | Reliability, performance, and security pillars applicable to commerce stacks |
| AWS Step Functions docs | https://docs.aws.amazon.com/step-functions/ | Order orchestration via state machines and the AWS-flavored saga pattern |
| Azure Architecture Center — cloud design patterns | https://learn.microsoft.com/en-us/azure/architecture/patterns/ | Saga, Compensating Transaction, Cache-Aside, Circuit Breaker, Async Request-Reply, Throttling, Sequential Convoy — provider-neutral pattern catalog |
| Azure Architecture Center — saga pattern | https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/saga/saga | Orchestrated and choreographed saga reference |
| Alibaba Cloud Architecture Center | https://www.alibabacloud.com/architecture | Retail and finance reference architectures from the team behind Taobao/Tmall/Alipay |
| Alibaba Cloud retail solutions | https://www.alibabacloud.com/solutions/retail | Singles' Day-scale commerce patterns (peak-traffic catalog, inventory, payment) |
| Alibaba Cloud blog | https://www.alibabacloud.com/blog | Engineering posts on high-throughput inventory and order processing |
| Google Cloud Architecture Center — retail | https://cloud.google.com/architecture/retail | Retail reference architectures (catalog search, personalization, inventory) |
| Cloudflare blog | https://blog.cloudflare.com/ | Edge-side ecommerce patterns: cart caching, abuse mitigation, webhook fan-out |

## Practitioner perspectives — how leading retailers and commerce thinkers operate

These are **secondary, inspirational sources**, not primary authority for canonical patterns. Use them to understand how specific operators *interpret* commerce problems — their decisions are conditioned on their scale, market, and business model and do not generalize automatically. Read for context; verify against primary sources before stating a pattern.

### Retailer engineering blogs

| Source | URL | Coverage strengths |
|:---|:---|:---|
| Amazon — All Things Distributed (Werner Vogels) | https://www.allthingsdistributed.com/ | Distributed systems behind Amazon retail and AWS; eventual consistency, dynamo, peak-event ops |
| Amazon Science | https://www.amazon.science/ | Forecasting, personalization, recommendation, fulfillment optimization |
| Walmart Global Tech | https://medium.com/walmartglobaltech | Inventory accuracy, omnichannel order routing, ship-from-store, RFID at scale |
| Mercado Libre Engineering | https://medium.com/mercadolibre-tech | LATAM-specific commerce scale, payment-method diversity, fraud, logistics network |
| Shopify Engineering | https://shopify.engineering/ | Multi-tenant commerce at scale, checkout reliability, money handling, Ruby/Rails operations |
| Stripe — engineering and product | https://stripe.com/blog | Payment systems design, idempotency, FX, fraud, ML |
| DoorDash Engineering | https://doordash.engineering/ | Marketplace matching, real-time order orchestration, geofencing |
| Uber Engineering | https://www.uber.com/blog/engineering/ | Real-time dispatch, dynamic pricing, geographic fraud signals |
| Etsy — Code As Craft | https://www.etsy.com/codeascraft/ | Marketplace, search, recommendation, A/B testing at scale |
| Zalando Engineering | https://engineering.zalando.com/ | European commerce scale, catalog modeling, microservices in retail |
| eBay Tech | https://innovation.ebayinc.com/tech/ | Marketplace mechanics, search relevance, payments |
| Wayfair Tech | https://www.aboutwayfair.com/careers/tech-blog | Big-ticket commerce, recommendation, supply-chain optimization |
| Target Tech | https://tech.target.com/ | Omnichannel, BOPIS, inventory orchestration |
| Coupang Engineering | https://medium.com/coupang-engineering | Korea-scale commerce, rocket-delivery logistics |
| Booking.com — Tech | https://medium.com/booking-com | Inventory and pricing for travel commerce; A/B testing rigor |

### "Working Backwards" and Amazon-school practices

| Source | URL | Use for |
|:---|:---|:---|
| Bezos shareholder letters (1997–2020) | https://www.aboutamazon.com/news/company-news/2020-letter-to-shareholders | Long-form articulation of Day 1, customer obsession, two-pizza teams, written narratives over decks |
| Werner Vogels — "10 lessons from 10 years of Amazon Web Services" | https://www.allthingsdistributed.com/2016/03/10-lessons-from-10-years-of-aws.html | Foundational essay on building durable commerce-grade infrastructure |
| Werner Vogels — "Eventually Consistent" (CACM, 2009) | https://queue.acm.org/detail.cfm?id=1466448 | Consistency models in commerce systems |
| ACM Queue (curated) | https://queue.acm.org/ | Practitioner-quality essays on distributed systems and reliability |

### Commerce strategy commentators

These authors write about commerce *strategy* rather than *engineering*. Useful when reasoning about why a system exists (the business model), not how it is built.

| Source | URL / Reference | Use for |
|:---|:---|:---|
| Ben Thompson — Stratechery | https://stratechery.com/ | Strategic analysis of commerce platforms (Amazon, Shopify, marketplaces); aggregation theory |
| Andrew Chen | https://andrewchen.com/ | Marketplaces, network effects, retention curves |
| Bill Gurley — Above the Crowd | https://abovethecrowd.com/ | Investor lens on marketplace dynamics |
| Marc Andreessen — a16z blogs | https://a16z.com/ | DTC, marketplace, fintech-commerce intersections |
| Eric Ries — Lean Startup principles | https://theleanstartup.com/ | Iteration and validated learning, applied widely in DTC |
| Brian Balfour — Reforge | https://brianbalfour.com/essays | Growth, retention, lifecycle marketing in commerce |
| Nir Eyal — *Hooked* | https://www.nirandfar.com/ | Habit formation in consumer-facing commerce |
| Tobi Lütke (Shopify CEO) — public talks | https://shopify.engineering/ and Shopify investor letters | "Arming the rebels" — merchant-empowerment perspective |
| Patrick & John Collison (Stripe) — public writing | https://stripe.com/blog | Infrastructure-as-product philosophy applied to payments |

Important caveat: strategy commentators are useful for framing the *why* of design decisions, but their advice is rarely directly implementable. Never cite a strategy post as the sole authority for a technical pattern.

## Industry analysts, standards, and frameworks

| Source | URL | Use for |
|:---|:---|:---|
| MACH Alliance | https://machalliance.org/ | Composable commerce: Microservices, API-first, Cloud-native, Headless — vendor-neutral framework |
| NRF — National Retail Federation | https://nrf.com/ | US retail industry trends, retail standards, ARTS (Association for Retail Technology Standards) data model |
| ARTS retail data model (legacy but referenced) | https://www.omg.org/retail-depository/ | Common retail data model historically influential on commerce data design |
| eMarketer / Insider Intelligence | https://www.insiderintelligence.com/ | Quantitative ecommerce trend data (penetration, payment-method share, region) |
| Forrester Research | https://www.forrester.com/ | Analyst reports on commerce platforms; Forrester Wave for digital commerce |
| Gartner | https://www.gartner.com/ | Magic Quadrant for Digital Commerce; Hype Cycle for retail |
| McKinsey — Retail Practice | https://www.mckinsey.com/industries/retail/our-insights | Operational benchmarks, omnichannel research |
| BCG — Retail | https://www.bcg.com/industries/retail/overview | Strategic retail studies |
| Boston Consulting Group — Digital Commerce | https://www.bcg.com/ | Commerce transformation case studies |
| IDC Retail Insights | https://www.idc.com/ | Industry forecasts, technology spend |
| Retail Dive | https://www.retaildive.com/ | Daily industry news (use for context, not citation) |
| Digital Commerce 360 (formerly Internet Retailer) | https://www.digitalcommerce360.com/ | Ranking and benchmarking US/global ecommerce |

### Composable / headless commerce literature

| Source | URL | Use for |
|:---|:---|:---|
| MACH Alliance whitepapers | https://machalliance.org/resources | Microservices/API-first/Cloud-native/Headless principles, vendor-neutral |
| Jamstack at Scale (Netlify, Vercel ecosystem docs) | https://jamstack.org/ | Front-end implications of headless commerce |
| commercetools — composable commerce hub | https://commercetools.com/composable-commerce | Vendor view of the composable approach |
| Shopify — Hydrogen / Oxygen (headless framework) | https://hydrogen.shopify.dev/ | Headless storefront patterns |

## Books and long-form references

Books that have shaped how the industry thinks about commerce. Cite by chapter; editions vary.

| Book | Author | Use for |
|:---|:---|:---|
| *Working Backwards* (2021) | Colin Bryar & Bill Carr | Amazon's operational practices: PR/FAQ, six-pagers, two-pizza teams, Bar Raiser |
| *The Everything Store* (2013) | Brad Stone | Amazon's strategic playbook and culture |
| *Amazon Unbound* (2021) | Brad Stone | Amazon post-2010: marketplaces, Alexa, fulfillment scale |
| *Delivering Happiness* (2010) | Tony Hsieh (Zappos) | Customer-service-as-moat in commerce |
| *Shoe Dog* (2016) | Phil Knight (Nike) | DTC pre-internet; brand-driven commerce |
| *The Lean Startup* (2011) | Eric Ries | Iteration, MVPs, validated learning — applied widely in DTC |
| *Hooked* (2014) | Nir Eyal | Habit-formation loops in commerce UX |
| *The Innovator's Dilemma* (1997) | Clayton Christensen | Disruption frameworks applied to retail |
| *Don't Make Me Think* (2000, rev. 2014) | Steve Krug | UX fundamentals for storefront design |
| *Hit Refresh* (2017) | Satya Nadella | Cloud-era platform thinking; relevant to commerce-on-cloud |
| *Made in China 2025 / Smile Curve literature* | Various | Manufacturing-to-retail integration in Asia-Pacific |
| *Designing Data-Intensive Applications* (2017) | Martin Kleppmann | Storage, consistency, replication — the engineering substrate of any commerce system |
| *Building Microservices* (2nd ed., 2021) | Sam Newman | Service decomposition; commerce is the canonical example |
| *Release It!* (2nd ed., 2018) | Michael Nygard | Resilience patterns for production commerce systems |
| *The Tao of Microservices* (2017) | Richard Rodger | Microservices in commerce ecosystems |
| *Domain-Driven Design Distilled* (2016) | Vaughn Vernon | Compact DDD reference for commerce modelers |

## Engineering-blog references (vetted)

Per the "Sources to avoid as primary authority" rule, blog content is supporting, not authoritative. The following engineering blogs *do* qualify as authoritative for commerce patterns because they are written by the teams running large commerce platforms and the posts go through editorial review.

| Source | URL | Coverage strengths |
|:---|:---|:---|
| Shopify Engineering | https://shopify.engineering/ | Sharding, checkout reliability, inventory consistency, money handling |
| Stripe — blog and engineering | https://stripe.com/blog | Payment lifecycle, idempotency, webhook design, fraud, FX |
| DoorDash Engineering | https://doordash.engineering/ | Order orchestration, marketplace matching, retries |
| Mercado Libre Engineering | https://medium.com/mercadolibre-tech | Latin-American commerce scale, payment-method diversity, anti-fraud |
| Walmart Global Tech | https://medium.com/walmartglobaltech | Inventory accuracy at scale, omnichannel order routing |
| Etsy Code As Craft | https://www.etsy.com/codeascraft/ | Search, recommendation, payment, marketplace mechanics |
| Uber Engineering | https://www.uber.com/blog/engineering/ | Real-time order matching, dispatch, geographic pricing |
| InfoQ (commerce section) | https://www.infoq.com/commerce/ | Editorial-reviewed industry articles on commerce architecture |
| Chris Richardson — microservices.io | https://microservices.io/patterns/ | Saga, idempotent consumer, transactional outbox, CQRS in commerce contexts |
| ThoughtWorks Technology Radar | https://www.thoughtworks.com/radar | Adoption signals for commerce-relevant patterns and tools |
| High Scalability | http://highscalability.com/ | Case studies of how commerce platforms scaled (Amazon, eBay, Alibaba, Shopify) |

## Foundational references (theory and patterns)

These are not commerce-specific but underpin every skill in this package.

| Source | Reference | Use for |
|:---|:---|:---|
| *Domain-Driven Design* (Eric Evans, 2003) | Book — cite by chapter | Bounded contexts, ubiquitous language, aggregate roots |
| *Implementing Domain-Driven Design* (Vaughn Vernon, 2013) | Book — cite by chapter | Tactical DDD; commerce is the canonical example throughout |
| *Patterns of Enterprise Application Architecture* (Martin Fowler, 2002) | Book — cite by chapter | Money pattern, state pattern, identity map, unit of work |
| *Enterprise Integration Patterns* (Hohpe & Woolf, 2003) | https://www.enterpriseintegrationpatterns.com/ | Messaging patterns, dead letter, idempotent receiver, claim check |
| Martin Fowler's blog | https://martinfowler.com/ | State pattern, saga pattern, CQRS, event sourcing |
| ISO 4217 | https://www.iso.org/iso-4217-currency-codes.html | Currency codes — cite when a skill discusses multi-currency |
| ISO 3166-1 alpha-2 | https://www.iso.org/iso-3166-country-codes.html | Country codes for addresses, shipping zones |
| ISO 20022 | https://www.iso20022.org/ | Financial messaging standard increasingly relevant to payments interoperability |
| RFC 7231 §4.2 | https://datatracker.ietf.org/doc/html/rfc7231#section-4.2 | HTTP safe and idempotent methods — definitional |
| RFC 9110 (HTTP Semantics) | https://datatracker.ietf.org/doc/html/rfc9110 | Successor to RFC 7231; current HTTP semantics |
| RFC 9457 | https://datatracker.ietf.org/doc/html/rfc9457 | Problem Details for HTTP APIs — error responses |
| W3C Payment Request API | https://www.w3.org/TR/payment-request/ | Browser-native payment flow standard |
| Schema.org Product | https://schema.org/Product | Structured-data vocabulary for product markup |
| Google — Product structured data | https://developers.google.com/search/docs/appearance/structured-data/product | Search-engine-aligned product markup guidance |
| EU Directive 98/6/EC (price indication) | https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:31998L0006 | Tax-inclusive pricing requirement in EU B2C |
| EU PSD2 / SCA guidance | https://www.europeanpaymentscouncil.eu/ | Strong Customer Authentication requirements |
| PCI DSS | https://www.pcisecuritystandards.org/ | Cardholder data security standard |
| OWASP cheat sheets | https://cheatsheetseries.owasp.org/ | Webhook security, REST security, authentication |

---

## Per-skill sources

For each skill in the roadmap, the sources below are the starting point. Always cross-reference at least two before stating a pattern.

### `domain-vocabulary`

The foundation. Establish terms by reading how multiple platforms define them, then state the most coherent definition.

- Medusa glossary and module overviews: https://docs.medusajs.com/learn
- Shopify glossary: https://shopify.dev/docs/api/admin (read the noun definitions across resources)
- Stripe API reference: https://docs.stripe.com/api (definitions of charge, payment intent, customer)
- Sylius core concepts: https://docs.sylius.com/the-book/index.html
- commercetools business units (B2B vocabulary): https://docs.commercetools.com/api/projects/business-units
- Spryker quote approval (B2B quote vocabulary): https://docs.spryker.com/docs/pbc/all/cart-and-checkout/202410.0/base-shop/feature-overviews/quote-approval-feature-overview.html
- AWS retail glossary (cross-reference for cloud-aligned terms): https://aws.amazon.com/retail/
- Alibaba Cloud retail glossary (cross-reference): https://www.alibabacloud.com/solutions/retail
- DDD ubiquitous language: Evans, Chapter 2

What to extract: the canonical distinction between order, quote, cart, line item, order item; the four inventory states (on-hand, available, reserved, committed); customer vs account vs user.

### `catalog-and-product-modeling`

- Medusa product module: https://docs.medusajs.com/resources/commerce-modules/product
- Shopify product and variant model: https://shopify.dev/docs/api/admin-rest/latest/resources/product
- Shopify metafields and custom data: https://shopify.dev/docs/apps/build/custom-data
- Sylius product, variant, option: https://docs.sylius.com/the-book/products/products
- commercetools product types: https://docs.commercetools.com/api/projects/productTypes
- commercetools product projections: https://docs.commercetools.com/api/projects/productProjections
- Schema.org Product (structured-data vocabulary): https://schema.org/Product
- Google Product structured data: https://developers.google.com/search/docs/appearance/structured-data/product
- AWS — building a product catalog with DynamoDB: https://docs.aws.amazon.com/prescriptive-guidance/latest/dynamodb-data-modeling/welcome.html
- Alibaba Cloud — peak-load catalog patterns (Singles' Day): https://www.alibabacloud.com/blog (search "catalog scale")
- Shopify Engineering — sharding the catalog: https://shopify.engineering/

What to extract: product vs variant (SKU) distinction; how attributes differ from options; taxonomy patterns (hierarchical categories, faceted attributes); when to use a variant vs a separate product; how the data model holds up at million-SKU scale.

### `pricing-and-channels`

- Stripe Prices API: https://docs.stripe.com/api/prices
- Stripe Tax: https://docs.stripe.com/tax
- Medusa pricing module: https://docs.medusajs.com/resources/commerce-modules/pricing
- commercetools price tiers and embedded prices: https://docs.commercetools.com/api/projects/products#prices
- Shopify price lists (B2B): https://shopify.dev/docs/api/admin-graphql/latest/queries/pricelist
- Shopify MoneyV2 (multi-currency representation): https://shopify.dev/docs/api/admin-graphql/latest/objects/MoneyV2
- Fowler — Money pattern: https://martinfowler.com/eaaCatalog/money.html
- EU Directive 98/6/EC (price indication / tax-inclusive requirement): https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:31998L0006
- AWS Marketplace SaaS pricing patterns: https://docs.aws.amazon.com/marketplace/latest/userguide/pricing.html
- Azure — SaaS metering and billing patterns: https://learn.microsoft.com/en-us/azure/architecture/example-scenario/saas/saas-platform-billing
- Alibaba Cloud — dynamic pricing solutions: https://www.alibabacloud.com/solutions/retail/dynamic-pricing
- Stripe Engineering — currency precision: https://stripe.com/blog/engineering (search "money / currency")

What to extract: list price vs sale price; price lists scoped by channel/customer-group/region; tax-inclusive vs exclusive representation; tiered pricing; the Money pattern (amount + currency, never a bare float); rounding and FX policy.

### `inventory-management`

- Shopify inventory states: https://shopify.dev/docs/apps/build/orders-fulfillment/inventory-management-apps
- Shopify InventoryLevel API: https://shopify.dev/docs/api/admin-rest/latest/resources/inventorylevel
- Medusa inventory module: https://docs.medusajs.com/resources/commerce-modules/inventory
- Medusa stock-location module: https://docs.medusajs.com/resources/commerce-modules/stock-location
- commercetools inventory entries: https://docs.commercetools.com/api/projects/inventory
- Sylius inventory: https://docs.sylius.com/the-book/products/inventory
- Fowler — Optimistic offline lock: https://martinfowler.com/eaaCatalog/optimisticOfflineLock.html
- Fowler — Pessimistic offline lock: https://martinfowler.com/eaaCatalog/pessimisticOfflineLock.html
- AWS Prescriptive Guidance — inventory data on DynamoDB: https://docs.aws.amazon.com/prescriptive-guidance/latest/dynamodb-data-modeling/use-cases.html
- AWS Architecture Blog — order inventory ledger patterns: https://aws.amazon.com/blogs/architecture/
- Azure Architecture Center — Materialized View / Event Sourcing for stock: https://learn.microsoft.com/en-us/azure/architecture/patterns/materialized-view
- Alibaba Cloud — high-concurrency inventory deduction patterns: https://www.alibabacloud.com/blog (search "inventory deduction Singles Day")
- Walmart Global Tech — inventory accuracy at scale: https://medium.com/walmartglobaltech
- Stripe — *not relevant; payment processors don't manage inventory*

What to extract: the four states (on-hand, available, reserved, committed); multi-location semantics; reservation timing options (at cart, at checkout-start, at order-create, at payment-confirm); oversell vs friction trade-off; safety stock and threshold alerting; concurrency control for hot-SKU decrements.

### `cart-lifecycle`

- Sylius cart state machine: https://docs.sylius.com/the-book/orders/states
- Sylius cart concepts: https://docs.sylius.com/the-book/orders/orders
- Medusa cart module: https://docs.medusajs.com/resources/commerce-modules/cart
- Shopify cart API (Storefront): https://shopify.dev/docs/api/storefront/latest/objects/Cart
- Shopify abandoned checkout: https://shopify.dev/docs/api/admin-graphql/latest/objects/AbandonedCheckout
- commercetools cart: https://docs.commercetools.com/api/projects/carts
- Stripe Checkout session lifecycle: https://docs.stripe.com/payments/checkout/how-checkout-works
- AWS — shopping cart reference architecture on DynamoDB: https://docs.aws.amazon.com/prescriptive-guidance/latest/dynamodb-data-modeling/use-cases.html
- Azure Architecture Center — Cache-Aside pattern (cart caching): https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside
- Cloudflare — edge ecommerce / cart caching: https://blog.cloudflare.com/
- Shopify Engineering — checkout reliability: https://shopify.engineering/

What to extract: anonymous vs authenticated cart; merge-on-login strategies; cart expiration and abandonment; price and inventory revalidation policies; the cart state machine (Sylius's model is particularly clear); cart caching and edge persistence.

### `checkout-flow`

- Sylius checkout state machine: https://docs.sylius.com/the-book/customers/checkout
- Stripe Checkout (hosted): https://docs.stripe.com/payments/checkout
- Stripe Payment Element (embedded): https://docs.stripe.com/payments/payment-element
- Shopify checkout extensibility: https://shopify.dev/docs/apps/build/checkout

What to extract: step structure (address → shipping → payment → review → confirm); guest vs registered checkout; tax calculation timing; the quote-to-order conversion moment.

### `order-lifecycle`

- Sylius order state machine: https://docs.sylius.com/the-book/orders/states (canonical reference)
- Sylius order concepts: https://docs.sylius.com/the-book/orders/orders
- Medusa order module: https://docs.medusajs.com/resources/commerce-modules/order
- Shopify order states (fulfillment_status, financial_status): https://shopify.dev/docs/api/admin-rest/latest/resources/order
- Shopify order edits: https://shopify.dev/docs/api/admin-graphql/latest/mutations/orderEditBegin
- commercetools order: https://docs.commercetools.com/api/projects/orders
- Stripe payment intent state machine: https://docs.stripe.com/payments/paymentintents/lifecycle
- Stripe refunds: https://docs.stripe.com/refunds
- AWS Step Functions — order orchestration: https://docs.aws.amazon.com/step-functions/latest/dg/sample-project-saga.html
- AWS Prescriptive Guidance — saga pattern: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/saga.html
- Azure Architecture Center — Saga / Compensating Transaction: https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/saga/saga
- microservices.io — Saga: https://microservices.io/patterns/data/saga.html
- Alibaba Cloud — distributed transaction (Seata): https://www.alibabacloud.com/help/en/seata/
- DoorDash Engineering — order orchestration: https://doordash.engineering/
- Fowler — State pattern: https://martinfowler.com/eaaCatalog/state.html
- Evans Chapter 10 (Aggregates); Vernon Chapters 8 (Domain Events) and 10 (Aggregates)

What to extract: the canonical states (draft, pending_payment, paid, fulfilling, fulfilled, shipped, delivered, completed, cancelled, refunded, returned, disputed); allowed transitions and side effects per transition; the difference between payment status and fulfillment status as orthogonal state machines; orchestration of cross-system order flows.

### `payment-flows`

- Stripe payment intents (auth/capture/sale): https://docs.stripe.com/payments/payment-intents
- Stripe online payments overview (hosted vs embedded): https://docs.stripe.com/payments/online-payments
- Stripe Payment Element: https://docs.stripe.com/payments/payment-element
- Stripe Checkout (hosted): https://docs.stripe.com/payments/checkout
- Stripe refunds: https://docs.stripe.com/refunds
- Stripe disputes: https://docs.stripe.com/disputes
- Stripe SCA: https://docs.stripe.com/strong-customer-authentication
- Stripe — save and reuse payment methods: https://docs.stripe.com/payments/save-and-reuse
- Adyen payment lifecycle: https://docs.adyen.com/online-payments/payment-result-codes
- Mercado Pago developer docs: https://www.mercadopago.com/developers/en/docs
- PCI Security Standards Council: https://www.pcisecuritystandards.org/
- W3C Payment Request API: https://www.w3.org/TR/payment-request/
- EU PSD2 / SCA guidance: https://www.europeanpaymentscouncil.eu/
- AWS Payment Cryptography: https://docs.aws.amazon.com/payment-cryptography/
- Azure Architecture Center — payment patterns: https://learn.microsoft.com/en-us/azure/architecture/
- Alibaba Cloud / Ant Group — Alipay flow overview: https://global.alipay.com/docs/ac/Platform/intro
- Stripe Engineering — building reliable payment systems: https://stripe.com/blog/engineering

What to extract: authorization vs capture vs sale; hosted vs embedded vs Payment Request API; webhook-driven confirmation vs synchronous response; full vs partial refunds; dispute lifecycle; tokenization and PCI scope reduction; regional payment-method specifics (LATAM, China, EU).

### `fulfillment-and-shipping`

- Shopify fulfillment service: https://shopify.dev/docs/apps/build/orders-fulfillment
- Medusa fulfillment module: https://docs.medusajs.com/resources/commerce-modules/fulfillment
- ShipEngine docs (carrier abstraction): https://www.shipengine.com/docs/
- EasyPost API (multi-carrier): https://docs.easypost.com/docs

What to extract: rate calculation timing; carrier selection criteria; the pick-pack-ship workflow; split shipments and multi-warehouse fulfillment; tracking events and their semantics.

### `returns-and-rmas`

- Shopify returns API: https://shopify.dev/docs/apps/build/orders-fulfillment/returns
- Medusa return module: https://docs.medusajs.com/resources/commerce-modules/order#return
- Stripe refunds (financial half of returns): https://docs.stripe.com/refunds

What to extract: return reasons taxonomy; restocking decision (inspect-first vs auto-restock); refund vs exchange vs store-credit branches; partial returns.

### `promotions-and-discounts`

- Medusa promotion module: https://docs.medusajs.com/resources/commerce-modules/promotion
- Shopify discount API: https://shopify.dev/docs/api/admin-graphql/latest/queries/discountNodes
- Stripe coupons and promotion codes: https://docs.stripe.com/billing/subscriptions/coupons
- commercetools cart discounts: https://docs.commercetools.com/api/projects/cartDiscounts

What to extract: percent vs fixed-amount vs free-shipping discount types; auto-apply vs code-required; conditions (minimum cart, specific products, customer segment); stacking and exclusivity rules; the per-line vs cart-level distinction.

### `customer-accounts`

- Shopify customer model: https://shopify.dev/docs/api/admin-rest/latest/resources/customer
- Medusa customer module: https://docs.medusajs.com/resources/commerce-modules/customer
- commercetools B2B business units: https://docs.commercetools.com/api/projects/business-units

What to extract: customer vs account vs user (they are not the same); address book modeling; order history scoping; B2B account hierarchies (parent company, branches, buyers); GDPR/CCPA data subject rights interaction.

### `taxes`

- Stripe Tax: https://docs.stripe.com/tax
- Avalara docs (tax engine reference): https://developer.avalara.com/api-reference/avatax/rest/v2/
- Shopify taxes: https://shopify.dev/docs/api/admin-rest/latest/resources/tax
- commercetools tax categories: https://docs.commercetools.com/api/projects/taxCategories

What to extract: tax-inclusive vs exclusive pricing display; calculation timing (cart, checkout, invoice); jurisdictional rules and nexus; tax-exempt products and customers; the difference between sales tax (US), VAT (EU), GST (AU/CA), IVA (LATAM).

### `idempotency`

- Stripe idempotency keys: https://docs.stripe.com/api/idempotent_requests
- RFC 7231 §4.2.2: https://datatracker.ietf.org/doc/html/rfc7231#section-4.2.2
- RFC 9110 (HTTP semantics; supersedes 7231): https://datatracker.ietf.org/doc/html/rfc9110
- AWS Builders' Library — making retries safe with idempotent APIs: https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/
- AWS Well-Architected Serverless Lens — idempotency tokens: https://docs.aws.amazon.com/wellarchitected/latest/serverless-applications-lens/serverless-applications-lens.html
- AWS Prescriptive Guidance — idempotent consumer: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/idempotent-consumer.html
- Azure Architecture Center — Idempotency Patterns: https://learn.microsoft.com/en-us/azure/architecture/microservices/design/api-design#idempotent-operations
- microservices.io — Idempotent consumer: https://microservices.io/patterns/communication-style/idempotent-consumer.html
- "Patterns of Distributed Systems" — Idempotent Receiver (Fowler): https://martinfowler.com/articles/patterns-of-distributed-systems/idempotent-receiver.html
- Adyen — API idempotency: https://docs.adyen.com/development-resources/api-idempotency/
- Square — idempotency keys: https://developer.squareup.com/docs/working-with-apis/idempotency
- Alibaba Cloud — idempotency in distributed systems: https://www.alibabacloud.com/blog (search "idempotency")
- Stripe Engineering — designing robust APIs with idempotency: https://stripe.com/blog/idempotency

What to extract: idempotency key generation and lifetime; client-supplied vs server-derived keys; storage and replay semantics; the distinction between idempotent operations (naturally safe) and idempotency keys (making non-idempotent operations safe to retry); concurrent-replay handling.

### `webhooks`

- Standard Webhooks specification: https://www.standardwebhooks.com/ and https://github.com/standard-webhooks/standard-webhooks
- Stripe webhooks best practices: https://docs.stripe.com/webhooks
- Stripe webhook signatures: https://docs.stripe.com/webhooks#verify-official-libraries
- Shopify webhooks: https://shopify.dev/docs/apps/build/webhooks
- GitHub webhooks (signature pattern): https://docs.github.com/en/webhooks
- Adyen webhooks: https://docs.adyen.com/development-resources/webhooks/
- AWS Prescriptive Guidance — receiving webhooks at scale: https://docs.aws.amazon.com/prescriptive-guidance/latest/modernization-integrating-microservices/
- AWS Architecture Blog — async webhook fan-out: https://aws.amazon.com/blogs/architecture/
- Azure Architecture Center — Async Request-Reply pattern: https://learn.microsoft.com/en-us/azure/architecture/patterns/async-request-reply
- Azure — webhook receiver pattern: https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-http-webhook
- Alibaba Cloud — event-driven architecture: https://www.alibabacloud.com/help/en/eventbridge
- Cloudflare — webhook delivery at scale: https://blog.cloudflare.com/
- OWASP REST Security Cheat Sheet (signature, timing attacks): https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html

What to extract: signature verification (HMAC patterns); retry policies and exponential backoff; at-least-once delivery and consumer-side deduplication; dead letter handling; replay protection via timestamps; async-processing patterns on the receiver side.

### `saga-compensation`

- Medusa workflows with compensation: https://docs.medusajs.com/learn/fundamentals/workflows/compensation-function
- Microsoft — Saga pattern: https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/saga/saga
- AWS — Saga pattern in serverless: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/saga.html
- Chris Richardson — Saga pattern: https://microservices.io/patterns/data/saga.html
- Temporal documentation (durable workflows): https://docs.temporal.io/

What to extract: orchestrated vs choreographed sagas; compensation function design (rollback is a new action, not an inverse transaction); idempotent compensation; the difference from database transactions.

### `domain-events`

- Vernon, *Implementing Domain-Driven Design*, Chapter 8 (Domain Events)
- Fowler — Domain Event: https://martinfowler.com/eaaDev/DomainEvent.html
- Fowler — Event Sourcing: https://martinfowler.com/eaaDev/EventSourcing.html
- AWS EventBridge schema design: https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-events-structure.html

What to extract: event vs command; event naming conventions (past tense, domain language); eventual consistency implications; the audit-trail-by-construction property; event vs message vs notification distinctions.

---

## Sources to avoid as primary authority

Be wary of these. They are useful for context but should not be the *sole* source for a pattern in a skill.

- **Individual blog posts and Medium articles** unless authored by recognized authorities (Fowler, Vernon, Richardson, etc.). The signal-to-noise ratio is low.
- **Stack Overflow answers** — fine as a hint, never as the citation.
- **Marketing pages** of commerce vendors (the `/why-X` pages, not the `/docs` pages).
- **AI-generated summary sites** that repackage other sources without attribution.
- **Wikipedia** for commerce-specific patterns (general concepts like "saga pattern" are fine; specific architecture choices are not).
- **Outdated tutorials** (anything pre-2022 for cloud-native patterns; pre-2020 for headless commerce).

## Triangulation rules

When sources disagree:

1. **Vendor docs disagree with each other** → present both positions, note the divergence as a real choice the reader makes.
2. **Vendor docs disagree with academic references** → favor academic for vocabulary and pattern definitions, vendor docs for current implementation conventions.
3. **A vendor doc contradicts the Standard Webhooks spec or an RFC** → favor the standard/RFC; note the vendor deviation.
4. **A claim has only one source** → mark it explicitly in the skill ("Stripe's convention is X; not all platforms follow this").
5. **No source can be found** → either remove the claim or mark it as inferred. Never present an unsourced opinion as established practice.

## When a skill needs a vertical-specific source

Vertical packs (pharmacy, marketplace, subscriptions, etc.) live in separate repos and have their own SOURCES.md. The core skills in this repo do not cite vertical-specific regulations or vendors. If you find yourself reaching for the ISP (Chilean health authority) or for Stripe Billing's recurring features, you are probably writing in the wrong repo.

## Maintaining this file

When you add a new authoritative source while writing a skill, add it here in the appropriate section so the next author can find it. When you discover a broken link, fix it in the same change. This file evolves with the skills.