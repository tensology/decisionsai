# Examples

Real output samples from `perfect-ui` runs. Each file demonstrates a single skill output — vibe, palette, typography, custom icons, motion intensity locked end-to-end.

## Files

- [`coffee-landing.html`](coffee-landing.html) — landing page, **editorial vibe** + agrarian wildcard. Subscription coffee service in Kyoto. Custom SVG icons (chair / envelope / pot / clock), silkscreen-style hero illustration, motion intensity 2/3 (GSAP scroll reveals + masked-line hero entrance), **Tier 3-4 Lenis** (v2.5.1+ type-gate rule — landing/portfolio auto-Lenis regardless of vibe; editorial config lerp 0.1 for restrained-but-premium feel + GSAP ticker sync).

- [`luxury-landing.html`](luxury-landing.html) — landing page, **luxury vibe**. Heritage perfumery (Maison Lumière) with 12-year aging premise. Custom SVG icons (leaf / amphora / vial / envelope) + ornate bottle hero illustration with botanical filigree. Cormorant Garamond + Cormorant (Vietnamese subset). Dark theme (ink #0E0E0C + cream + gold accent). **Tier 3-4 Lenis demo** with verbose copy-paste setup (luxury config lerp 0.08 for ultra-premium; hard guards + Lenis init + GSAP ticker sync + anchor routing — all commented for skill users to learn the pattern).

- [`playful-portfolio.html`](playful-portfolio.html) — **portfolio page**, **playful vibe** + kinetic wildcard. Mochi Studio (illustration + motion duo, Lisbon). **Bento Grid macrostructure** — 6 selected projects in dense playful tiles (Yume kids' app / Bloom Festival / Hoppa sleep / Donut Diner / Soft Skies / Birds in Margins). Custom SVG illustrations PER TILE (cloud + moon + stars / abstract flower / leaping rabbit silhouette / donut stack / smiling cloud / bird flight formation) + duo portrait illustration in About section. Fraunces + Plus Jakarta Sans (Vietnamese subset). Cream + ink + coral accent. **Motion intensity 3/3** with heavy GSAP choreography: bouncy word-by-word hero reveal (`back.out(2.2)`), 6 floating kinetic shapes with continuous rotation + drift, seamless horizontal marquee, tile-by-tile staggered Bento reveal + random rotate hover, magnetic cursor accent, all section reveals with `back.out(1.7)` Playful Brand Motion Identity. **Tier 4 Lenis + ScrollTrigger sync** (lerp 0.12 — faster, snappier than editorial/luxury).

Three examples now demonstrate distinct macrostructures (coffee=Letter, luxury=Letter, playful-portfolio=Bento Grid) + 3 vibes (editorial, luxury, playful) + 3 motion intensities (2/3, 2/3, 3/3) + 2 page types (landing, portfolio). All landings/portfolios use Lenis Tier 3-4 per v2.5.1+ type-gate rule, tuned per vibe (editorial lerp 0.1 / luxury 0.08 / playful 0.12).

## How to add a new example

1. Create a new file `<vibe>-<type>.html`.
2. Link the project tokens via `<link rel="stylesheet" href="../css/tokens.css">` — every example shares the same token names but overrides palette per vibe.
3. Add a watermark banner at the top: `Example output · <vibe> vibe · <product>`.
4. Link back to `../index.html` in the banner.
5. Keep file under 600 lines. Examples are illustrative, not full production pages.
