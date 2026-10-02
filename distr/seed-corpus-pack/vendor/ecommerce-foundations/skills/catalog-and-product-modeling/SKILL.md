---
name: catalog-and-product-modeling
description: Use when designing or modifying the catalog data model — defining how products, variants, SKUs, attributes, options, categories, taxonomies, collections, and media relate. Load before creating product tables, deciding whether a configurable item should be one product with variants or many products, structuring category hierarchies, or wiring SEO slugs. Vocabulary is imported from the domain-vocabulary skill.
---

# Catalog and product modeling

How to structure the sellable surface of a commerce system. This skill frames the modeling decisions; it does not prescribe a schema. The right schema depends on the business model, product type, and the variation dimensions enumerated below.

Read the `domain-vocabulary` skill first. Terms used here without redefinition are defined there: *product*, *variant*, *SKU*, *bundle*, *attribute*, *option*, *category*, *taxonomy*, *collection*, *channel*, *store*.

## Vocabulary additions

The general terms come from `domain-vocabulary`. The following are specific to catalog modeling.

**Master product (parent product)** — In platforms that distinguish parent and variant, the top-level *product* record holding shared fields (name, description, marketing media). The *variants* under it carry the differentiating fields (price, *SKU*, inventory, variant-specific media).

**Option type vs option value** — *Option type* is the dimension (Size, Color); *option value* is the specific choice (Medium, Red). A *variant* is identified by a tuple of option values, one per option type defined on its parent.

**Product type (product class)** — A reusable schema describing which *attributes* and *options* apply to a product. commercetools and Sylius both expose this; Shopify hints at it via product templates. ([commercetools](https://docs.commercetools.com/api/projects/productTypes), [Sylius](https://docs.sylius.com/the-book/products/products))

**Faceted attribute** — An *attribute* exposed to the storefront for filtering (brand, material, size). Indexed for query, often denormalized into a search index.

**Slug (URL handle)** — The URL-safe string identifying a product or category in storefront URLs (`/products/red-cotton-shirt`). Slugs are part of the SEO surface and must be stable; historical slugs typically need redirects.

**Media asset** — An image, video, or 3D model attached to a *product*, *variant*, or both. Carries metadata: alt text, position, type, and a reference (URL or CDN path).

**Catalog scope** — The set of products visible in a given *channel*, *store*, or *region*. The same product can be in-catalog for one channel and out-of-catalog for another without being deleted.

## Canonical model

Most ecommerce systems converge on this structure:

```
Product (1) ──< has many >── Variant (N)
   │                            │
   ├── name, description        ├── SKU
   ├── product_type             ├── price (or price list ref)
   ├── slug                     ├── option values { size: M, color: red }
   ├── categories (M)           ├── inventory (per location)
   ├── collections (M)          └── variant-specific media
   ├── product-level media
   └── attributes { brand, material, ... }
```

Key invariants:

1. **One variant per stockable unit.** Even a product with no apparent configurability has one variant under it; that variant is what carries inventory and price.
2. **Variants are identified by their option-value tuple.** Two variants of the same product cannot share the same set of option values.
3. **Attributes are descriptive; options generate variants.** A field is one or the other, not both. Re-classifying mid-life requires a data migration.
4. **Categories form a tree; collections form arbitrary sets.** A product has one canonical place in the taxonomy but many collection memberships.
5. **Slugs are part of the public API surface.** Treat them as immutable from the SEO and integration perspective; if they must change, plan redirects.

## Variation dimensions

The canonical model adapts along these axes. Be explicit about where the system sits before choosing a schema.

- **Business model** — B2C (single price list, simple catalog), B2B (price list per *account*, restricted catalog visibility), marketplace (vendor-owned products, per-vendor catalog), subscription (the product *is* a plan), hybrid.
- **Product type** — Physical (stockable, shippable variants), digital (no inventory, entitlement-based), service (booking-based, no shipment), bundles, configurable-to-order (CTO with generated variants), made-to-order.
- **Configurability complexity** — Fixed options (predefined size/color matrix) vs free configurators (build-your-own with rules) vs personalized (engraving, embroidery; per-line uniqueness, not pre-modeled variants).
- **Catalog scope** — Single-channel vs multi-channel vs multi-store. Multi-store decisions: shared product master with per-store overrides, or fully independent catalogs.
- **Localization** — Single-locale vs multi-locale. Multi-locale: translated name, description, slug, attributes; localized media.
- **Search and discovery** — Browse-driven (taxonomy-heavy) vs search-driven (attribute-heavy, faceted). Affects whether to over-invest in deep categories or in rich attribute schemas.

## Decision frameworks

### Variant vs separate product

Use a single *product* with multiple *variants* when:

- The buyer perceives them as the same thing in different forms (size, color, material).
- They share the same marketing copy and most media.
- They share a *price list* structure (one variant cheaper, one more expensive of the same item).

Use separate *products* when:

- The marketing description, hero media, or category placement differs meaningfully.
- The items are bought for different reasons (a leather wallet and a fabric wallet of the same brand — likely separate products).
- The buyer is unlikely to switch between them in a single shopping decision.

When in doubt: ask whether the storefront URL should be the same. If yes, one product; if no, two.

### Attribute vs option

A field is an *option* if and only if:

- Each value produces a separately stockable, separately priced unit, **and**
- The buyer selects the value on the product page before adding to cart.

Otherwise it is an *attribute* (descriptive metadata). Common mistakes:

- Treating "material" as an option when there is only ever one material per product — should be an attribute.
- Treating "brand" as an option — should always be an attribute (or its own entity).
- Treating "color" as an attribute when the customer can choose between several — should be an option.

The cost of getting this wrong: variant explosion (combinatorial blowup when an attribute is wrongly modeled as an option) or stocked-product collapse (cannot sell each color/size separately when an option is wrongly modeled as an attribute).

### Taxonomy shape

Three patterns appear most often:

| Pattern | Description | When to choose |
|:---|:---|:---|
| Deep tree | 4+ levels (`Apparel > Men > Tops > Shirts > Dress shirts`) | Catalog with thousands of products and clear hierarchical browsing intent. |
| Shallow tree + facets | 2–3 levels, rich faceted attributes | Catalog where buyers filter (electronics, fashion with many attributes). |
| Flat + collections | One level of categories, curated collections do the rest | Boutique catalogs, brand storefronts, content-driven commerce. |

Pick deliberately. Mixing them in one store means buyers encounter inconsistent navigation; the engineering complexity also compounds.

### Categories vs collections vs tags

| Mechanism | Cardinality | Buyer-facing? | Purpose |
|:---|:---|:---|:---|
| Category | One canonical per product (taxonomy is a tree) | Yes — navigation | Find by what the product is |
| Collection | Many per product (arbitrary set) | Yes — merchandising | Curate by editorial logic (sale, seasonal, themed) |
| Tag | Many per product | Often internal | Filtering, internal taxonomy, AI features |

If the system needs only one of these, prefer categories. The pull to add collections appears when merchandising needs decouple from taxonomy ("Summer Sale" cuts across the tree).

### Slug strategy

Three positions:

1. **Auto-generated, never editable.** Lowest operational cost; SEO-fragile if the auto rule changes.
2. **Auto-generated, editable, history kept.** Balanced: editors can refine, redirects auto-create on change. Most platforms default here.
3. **Manually authored, history kept.** Best SEO control; highest operational burden.

Choose 2 unless there is a specific reason to constrain editors.

### Multi-store catalog sharing

Three positions:

1. **Independent catalogs per store.** Maximum flexibility, maximum duplication.
2. **Shared master with per-store visibility.** Single source of truth, store-specific catalog scope.
3. **Shared master with per-store overrides.** Single source of truth, store-specific name/description/price/media as needed.

Choice 2 is the default for multi-region stores selling the same products at different prices. Choice 3 is needed when localized descriptions and media diverge meaningfully. Choice 1 is correct when stores represent independent brands sharing only infrastructure.

### Bundle modeling

Three patterns:

- **Virtual bundle (assembled at order):** the bundle is a product whose *available* inventory is `min(component_available)`. No bundle-level *on-hand*; fulfillment picks components.
- **Physical bundle (pre-assembled):** the bundle is its own stockable *variant* with independent *on-hand*. Components are not tracked as separate stockable units once assembled.
- **Configurable bundle (build-your-own):** the bundle is a configurator that emits a *cart* of independent *line items*; the bundle itself has no *SKU*.

The choice cascades into inventory, fulfillment, returns, and reporting. Decide before modeling the data, not after.

## Catalog at scale

The decisions above hold at any catalog size; the *physical* realization changes once the catalog crosses ~100k variants or experiences peak-traffic spikes. Three concerns surface:

**Browse vs search authority.** At low scale, the OLTP product database answers both browse (category listings) and search (queries, filters). At high scale, search is offloaded to a dedicated index (Elasticsearch, OpenSearch, Algolia, Meilisearch) and the OLTP database becomes the system of record but not the read source. The index is rebuilt asynchronously from product changes — eventual consistency the storefront must tolerate (newly created products are not searchable for seconds to minutes). AWS retail reference architectures and Alibaba's Taobao platform both follow this split. ([AWS retail architecture](https://aws.amazon.com/retail/architecture/), [Alibaba Cloud retail](https://www.alibabacloud.com/solutions/retail))

**Read-through caching.** Product reads outnumber writes by orders of magnitude. A read-through cache (Redis, Memcached, or a CDN edge cache) keyed by `(product_id, locale, channel)` reduces database load. The cache-aside pattern is canonical; invalidation must be triggered by every write that affects a cached projection. ([Azure cache-aside pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside))

**Faceted search index design.** Each *faceted attribute* requires index support. Adding a new facet on an existing catalog typically triggers a full re-index; design the attribute schema with the facets you expect, plus a generic "extension attributes" map for additions that do not need facet support. Shopify Engineering's posts on sharding the catalog illustrate the cost of late schema changes. ([Shopify Engineering](https://shopify.engineering/))

These are physical patterns; they do not change the canonical model above. The vocabulary stays product/variant/attribute/option — only the storage and read path change.

## Anti-patterns

- **One product per variant.** Loses the shared marketing surface; produces duplicated descriptions; breaks the "one URL per product" SEO model. Symptom: 12 nearly identical search results for one T-shirt in different sizes.
- **No master product, only variants.** Conversely, modeling everything as flat variants makes shared editorial impossible. Symptom: changing the product description requires updating N rows.
- **Storing price on the master product.** Variants must each carry their own price (or price-list reference). Master-level price breaks when variants legitimately differ in cost. Symptom: cannot price the L size higher than the S size.
- **Treating SKU as the primary key.** SKUs change for business reasons (rebrand, supplier change, merger). Use a system-generated stockable ID and treat SKU as a label. Symptom: cannot rename a SKU without rewriting integrations.
- **Variant explosion via attributes-as-options.** Marking every descriptive field as an option produces N×M×K variants where most are never stocked. Symptom: 800 variants per product, 780 of them with zero inventory.
- **Re-using slugs after deletion.** A deleted product's slug should be reserved (404 or redirect), not reassigned. Otherwise external links point to the wrong product when reused.
- **Embedding category in the slug as the primary identifier.** Tying the URL to category placement means recategorizing breaks links. Use category-free product slugs and let category be a route concern only.
- **Storing translations as separate products.** Locale-variant products produce orphan duplication and break analytics. Translations belong on the same product as locale-keyed fields.
- **Treating digital and physical products with one model.** Digital products lack shipping, inventory locations, and weight; physical products lack download URLs and license keys. Sharing one schema forces null-everywhere. Discriminate at the product-type level.
- **Collections doing the job of categories (or vice versa).** Mixing them confuses merchandisers and breaks navigation. Pick the responsibility of each up front: categories = what it is; collections = what it's for.

## Sources

- Medusa product module — https://docs.medusajs.com/resources/commerce-modules/product
- Shopify product and variant model — https://shopify.dev/docs/api/admin-rest/latest/resources/product
- Shopify metafields and product types — https://shopify.dev/docs/apps/build/custom-data
- Sylius products, variants, options — https://docs.sylius.com/the-book/products/products
- commercetools product types — https://docs.commercetools.com/api/projects/productTypes
- commercetools product projections — https://docs.commercetools.com/api/projects/productProjections
- Google — Product structured data — https://developers.google.com/search/docs/appearance/structured-data/product
- Schema.org Product — https://schema.org/Product
- AWS Prescriptive Guidance — DynamoDB data modeling (product catalog) — https://docs.aws.amazon.com/prescriptive-guidance/latest/dynamodb-data-modeling/welcome.html
- AWS retail architecture — https://aws.amazon.com/retail/architecture/
- Azure cache-aside pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside
- Alibaba Cloud retail solutions (Taobao catalog patterns) — https://www.alibabacloud.com/solutions/retail
- Shopify Engineering (catalog sharding) — https://shopify.engineering/
- Zalando Engineering — catalog modeling at European scale — https://engineering.zalando.com/
- Wayfair Tech — recommendation and big-ticket catalog — https://www.aboutwayfair.com/careers/tech-blog
- Amazon Science — product graph and personalization — https://www.amazon.science/
- Mercado Libre Engineering — multi-country catalog patterns — https://medium.com/mercadolibre-tech
- MACH Alliance — composable catalog principles — https://machalliance.org/resources
