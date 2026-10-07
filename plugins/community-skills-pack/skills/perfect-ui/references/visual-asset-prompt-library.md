# Visual Asset Prompt Library

Battle-tested prompt templates for hero illustrations, backgrounds, OG images, avatars, project covers. Always inject the locked palette and vibe from `visual-direction.md`. Templates work for both `landing` and `portfolio` types — type-specific sections noted.

## Asset Checklist by Type

### If type = landing
- Hero illustration / scene (1)
- Section dividers / accents (2-4)
- Background texture (1, tileable)
- Open Graph image (1, 1200×630)
- Testimonial avatars (3-6, depending on count)
- Favicon source (1, square)

### If type = portfolio
- Hero portrait / abstract intro visual (1) — ONLY if vibe matches; often the work grid IS the hero
- Project cover images (per featured project — usually 4-8)
- Process / sketch visuals (3-6, optional, for case study)
- Background texture (1, tileable)
- Open Graph image (1, 1200×630, typography-driven preferred)
- Owner portrait (1, optional — only for About section)
- Favicon source (1, square — often a monogram)

## Prompt Anatomy (apply to ALL)

```
{SUBJECT}
{VIBE_ANCHOR} + {WILDCARD_ADJECTIVE}
Palette: {3 hex codes with role descriptions}
Lighting: {atmospheric keyword}
Composition: {asymmetric | rule-of-thirds | center}
Negative space: {top-left | bottom-right | empty corner}
Style references: {2-3 artists / movements}
Forbidden: AI purple/blue gradient, neon glow, generic tech illustration,
  Microsoft clip-art aesthetic, gradient mesh, default Octane render,
  cyberpunk hologram unless vibe explicitly requests
Aspect ratio: {1:1 | 16:9 | 9:16 | 21:9}
```

## Hero Illustration Templates

### Template: Editorial Hero (asymmetric, organic warmth)
```
A single hand-pulled silkscreen poster of {product metaphor — e.g., "a folded
letter releasing seeds"}, editorial and luxury vibe with hand-crafted wildcard.
Palette: cream background #F5F1E8, deep ink #1A1715, single dusk-rose accent
#B8635A. Lighting: late-afternoon side light from window, soft and dimensional.
Composition: subject anchored to right third, large empty space top-left for
copy overlay. Style references: Saul Bass posters, Dieter Rams product
photography, editorial print of the 1960s. Forbidden: AI purple/blue gradient,
neon, generic tech illustration, gradient mesh, "modern" digital art aesthetic.
Aspect ratio: 16:9.
```

### Template: Brutalist Hero (raw geometry)
```
A photograph-style image of three primitive geometric forms (cube, cone,
cylinder) arranged in deliberate misalignment on raw concrete. Brutalist vibe
with industrial wildcard. Palette: cement #E5E5E5, raw orange accent #FF3B00,
hard black #000000. Lighting: single harsh studio flash from upper-left,
sharp-edged shadows. Composition: forms cluster in lower-right, large empty
sky-like negative space upper-left and right. Style references: Tadao Ando
architecture photography, Wolfgang Tillmans, post-Bauhaus product. Forbidden:
soft gradients, glow, glassmorphism, AI rendering. Aspect ratio: 16:9.
```

### Template: Retro-Futuristic Hero (synth particles)
```
An abstract scene of a particle grid receding to a vanishing point in a deep
twilight space, retro-futuristic vibe with cinematic wildcard. Palette: deep
navy #0A0E27, vapor pink accent #FF6B9D, electric cyan accent #7DF9FF (use
sparingly). Lighting: single backlight casting volumetric rays through
particles, soft fog. Composition: vanishing point at lower-third intersection,
horizon implied not drawn. Style references: TRON 1982, early synthwave album
art, Syd Mead concept paintings. Forbidden: modern AI cyberpunk hologram
aesthetic, neon overload, generic grid wireframe, character figures. Aspect
ratio: 21:9.
```

### Template: Luxury Hero (product on glass)
```
A photorealistic product shot of {product} on a brushed brass podium against
a softly lit dark backdrop, luxury vibe with editorial wildcard. Palette:
near-black #0E0E0C, warm cream highlight #F0EBE0, champagne gold accent
#C9A961. Lighting: two-light setup — soft key from upper-right, rim from
behind for separation. Composition: product centered with deliberate symmetry,
horizon line at lower-third. Style references: Apple keynote photography,
Hermès editorial, Vogue product styling. Forbidden: AI render aesthetic,
purple/blue tint, glow halos, dramatic blur, faux bokeh. Aspect ratio: 1:1.
```

### Template: Organic Hero (natural materials)
```
A still-life photograph of {product metaphor — e.g., "a clay cup of warm
liquid beside dried herbs"} on a linen surface, organic vibe with hand-crafted
wildcard. Palette: linen cream #F4EFE6, moss green #5C7A4A, terracotta accent
#A8693A. Lighting: morning window light, soft diffuse, warm temperature.
Composition: rule-of-thirds with subject in lower-left, sprig of herb leading
eye to upper-right. Style references: Vincent Van Gogh interior paintings,
Kinfolk magazine, contemporary cookbook photography. Forbidden: tech
aesthetic, digital glow, AI gradient, plastic/synthetic materials. Aspect
ratio: 16:9.
```

### Template: Playful Hero (toy-like primitives)
```
A 3D scene of soft-edged primitive shapes (sphere, capsule, torus) in a
deliberately playful arrangement, playful vibe with handmade wildcard.
Palette: cream-yellow background #FFF8E7, coral accent #FF6B4A, deep brown
ink #2D1F12. Lighting: warm soft three-point setup, gentle shadows. Style
references: Memphis Group 1980s, claymation, Bruno Munari toys. Forbidden:
chrome material, neon, AI default 3D render aesthetic, MeshNormalMaterial
rainbow. Aspect ratio: 1:1.
```

## Portfolio-Specific Hero Templates

Portfolios often use a featured-project visual or abstract mark as hero, NOT a stock illustration. Use sparingly — usually the work grid is the hero.

## Static 3D Render → 2D Image Templates

**Critical:** These produce a 3D-rendered scene that is exported as PNG/WebP and used as `<Image>`. The visitor sees a 2D image. **Never imported as `.glb`.** This is the Augen.pro pattern.

### Template: Static 3D Render — glass-tech device on infinite gradient
```
A photorealistic 3D rendering of {product description — e.g., "a sleek
wireless wearable headset with brushed-metal accents"}, glass-tech vibe with
clinical wildcard. Render style: Blender Cycles / Spline / KeyShot studio
output, exported as 2D image. Palette: off-white background #F8F6F1 with
gradient to lavender, ink-gray product surfaces, single muted blue-gray
accent #A2D8F0 in product detail. Lighting: studio three-point with HDRI
neutral environment, soft shadows beneath product, single rim from upper-
right. Composition: product floats slightly off-center (asymmetric, not
dead-center), generous negative space top-left for type overlay. Style
references: Augen.pro hero, Apple keynote product photography, contemporary
industrial design portfolios. Forbidden: AI default Octane render aesthetic,
chrome highlights, generic glass-balls-on-marble, AI-generated 3D blob
characters, cyberpunk neon glow, default OctaneMaterial pink-blue. Aspect
ratio: 1:1 or 4:3.
```

### Template: Static 3D Render — sci-fi figure silhouette (Augen pattern)
```
A photorealistic 3D rendering of {subject — e.g., "a human head silhouette
viewed in three-quarter profile, eyes closed, completely smooth surface
without facial features, deep matte material"}, glass-tech vibe with
cinematic wildcard. Render style: Blender Cycles studio output, exported as
2D image. Palette: pure off-white background #F8F6F1, dark matte form in
warm graphite, single accent — soft warm rim light only. Lighting: backlight
silhouette with subtle skin-tone rim from right, ambient occlusion under
form. Composition: subject centered with massive negative space all around
(80% empty), face softly fading into background where it meets light.
Style references: Augen.pro hero head, Apple introductory product films,
contemporary editorial 3D portrait work. Forbidden: detailed facial features,
generic 3D-character aesthetic, glossy plastic feel, default Octane render,
chrome highlights, neon. Aspect ratio: 1:1.
```

### Template: Static 3D Render — playful / claymation
```
A 3D rendering of {subject — e.g., "soft-edged primitive shapes (sphere,
capsule, torus) in deliberately playful arrangement"}, playful vibe with
handmade wildcard. Render style: clay-material soft shading, exported as 2D
image. Palette: cream-yellow background #FFF8E7, coral accent #FF6B4A, deep
brown ink #2D1F12 for shadows. Lighting: warm soft three-point, gentle
shadows, no harsh specular. Style references: Memphis Group 1980s,
claymation, Bruno Munari toys, Spline community handmade aesthetic.
Forbidden: chrome material, neon, AI default 3D render aesthetic,
MeshNormalMaterial rainbow, cyberpunk hologram. Aspect ratio: 1:1.
```

### When to use static 3D render
- Glass-tech vibe → primary visual language
- Luxury vibe (product photography style) → if real product photo unavailable
- Playful vibe → claymation aesthetic only
- Forbidden vibes for static 3D: editorial, hand-crafted, art-deco, brutalist, organic (all conflict with rendered look)

### Production tools
- **Blender** (free) — Cycles renderer, full control
- **Spline** (web-based) — quick 3D scene + export PNG
- **KeyShot** (paid) — product photography quality
- **High-quality text-to-image with palette + lighting + composition control** — fallback when no 3D tool, but use ONLY with strict negative prompt list to avoid AI-default aesthetic

### Validation
After generation, verify:
- Output is a single PNG/WebP (not multiple frames implying real-time)
- Palette matches locked direction (extract dominant colors)
- No "AI default 3D render" aesthetic (the tell: balanced lighting + glossy plastic + saturated rim color)
- File size < 500KB after WebP optimization

## SVG Illustration Templates

For geometric flat / engraved line / geometric vector illustrations. Claude can write SVG directly OR generate via AI then trace.

### Template: Geometric flat hero (minimal / brutalist / art-deco)
```
A geometric flat illustration of {metaphor — e.g., "interlocking arcs forming
a sun motif"}, brutalist/art-deco vibe. Constraint: composed entirely of
basic shapes (circle, rectangle, triangle, arc) — no curves beyond simple
arcs. Palette: 2-3 flat colors from locked palette, no gradients. Style:
SVG-friendly hard edges, mathematical composition. Reference: Saul Bass
posters, Massimo Vignelli signage, mid-century geometric posters. Forbidden:
soft gradients, shadows, photographic detail. Aspect ratio: 1:1 or 16:9.
```

After generation, trace to clean SVG (Inkscape Trace Bitmap) OR rewrite by hand for production-quality vector.

### Template: Hand-drawn ink illustration
```
A loose hand-drawn ink illustration of {subject — e.g., "a folded paper
crane mid-flight"}, editorial / hand-crafted vibe. Single weight or weight-
varied ink line, expressive imperfection. Palette: ink only on cream paper
background; no fills. Style: contemporary editorial line art, Alvin Lustig
references, mid-century book illustration. Forbidden: digital perfect
curves, gradient fills, photographic shading. Aspect ratio: 1:1 or 3:4.
```

### Template: Engraved line art (luxury / art-deco)
```
A vintage-style engraved illustration of {subject}, luxury / art-deco vibe.
Style: thin parallel lines for shading (crosshatching), monogram-style
precision, vintage scientific drawing aesthetic. Palette: single ink on
cream paper. Reference: vintage patent illustrations, banknote engraving,
heritage brand identity work (Hermès, Aesop). Forbidden: digital flat fills,
modern gradients, AI-default rendering. Aspect ratio: 1:1.
```

### Direct SVG (Claude inline) — preferred for simple geometric

```tsx
// app/components/illustrations/sun-motif.tsx
export const SunMotif = ({ size = 200 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 200 200" aria-hidden="true">
    {/* 12 rays */}
    {Array.from({ length: 12 }).map((_, i) => (
      <line
        key={i}
        x1={100 + Math.cos((i * Math.PI) / 6) * 60}
        y1={100 + Math.sin((i * Math.PI) / 6) * 60}
        x2={100 + Math.cos((i * Math.PI) / 6) * 90}
        y2={100 + Math.sin((i * Math.PI) / 6) * 90}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    ))}
    <circle cx="100" cy="100" r="40" fill="none" stroke="currentColor" strokeWidth="1.5" />
  </svg>
);
```

Style mapping per vibe: see `references/2d-illustration-catalog.md` § Vibe → Style Mapping.

## Asset Cohesion Rules

**Within a single site, ALL 2D illustrations + photos + (optional) static 3D renders must share style language.**

### Cohesion checklist (run before shipping)
1. **Single illustration style** — pick one row from `2d-illustration-catalog.md` vibe table; apply to hero, mid-section, dividers
2. **Single palette** — every asset uses ONLY locked palette colors; no rogue colors
3. **Single line weight / stroke philosophy** if line-based — matches icon set stroke from `custom-icon-pipeline.md`
4. **Single composition language** — if asymmetric heroes, asymmetric mid-section illustrations too
5. **Single texture treatment** — grain everywhere OR grain nowhere; not "grainy hero, clean rest"
6. **Single lighting language** (if photographic / static 3D) — warm afternoon vs cool studio vs harsh flash; pick one
7. **Single depth-of-field discipline** — all flat OR all dimensional, not mixed

### Cross-asset audit
At end of Phase 4, run quick audit:
- Open hero image + 2 mid-section images + 1 OG side-by-side
- Ask: "Do these look like one designer made them?"
- If "no" or "uncertain" → identify the outlier, regenerate with stronger style anchors

### Anti-pattern: style mixing
- ❌ Silkscreen poster hero + synthwave gradient mid-section
- ❌ Hand-drawn ink illustrations + photographic testimonials with no color-grade tie
- ❌ Geometric flat icons + watercolor section dividers
- ❌ Static 3D render hero + cut-paper collage section visuals

### Template: Portfolio abstract intro (editorial, mark-like)
```
A single typographic monogram of letters "{initials}" rendered as overlapping
display-serif glyphs in deep ink on cream paper, editorial vibe with
hand-crafted wildcard. Palette: cream paper #F5F1E8, deep ink #1A1715, single
warm accent #B8635A used only on a small detail. Lighting: flat editorial,
no dramatic shadows. Composition: monogram anchored bottom-right, large empty
top-left for headline. Style references: Massimo Vignelli signage, Saul Bass
title sequences, Mid-century editorial monograms. Forbidden: glow, gradient,
3D extrusion, generic SaaS branding aesthetic. Aspect ratio: 1:1.
```

### Template: Portfolio process visual (sketch / artifact)
```
A flat-lay photograph of design process artifacts — pencil sketches, fabric
swatches, color chips, post-it notes — arranged on a wooden surface, organic
vibe with hand-crafted wildcard. Palette: warm wood tones, raw paper, single
accent matching brand. Lighting: morning window light. Composition:
asymmetric scatter, NOT grid. Style references: Eames studio photographs,
process documentation in design monographs. Forbidden: stock-photo aesthetic,
overly arranged, MacBook in shot. Aspect ratio: 16:9.
```

### Template: Portfolio owner portrait (when used)
```
A natural environmental portrait of {owner description — e.g., "a designer in
their studio, mid-30s, wearing simple workwear"}, looking off-camera in mid-
gesture, editorial vibe. Palette: tinted to match locked palette, color
graded to match cream + ink + accent. Lighting: window side-light, soft
fall-off, natural shadows. Composition: subject in lower-third, environmental
context (studio, materials) visible. Style references: August Sander
portraits, Annie Leibovitz environmental editorials. Forbidden: white-
backdrop studio aesthetic, fake smile, LinkedIn corporate look, AI face
artifacts (asymmetric ears, wrong fingers, melted accessories). Aspect ratio:
4:5 or 1:1.
```

### Template: Project cover (for work grid tiles)
```
{Specific project description — e.g., "Brand identity system for a Vietnamese
specialty coffee roaster, showing logo on packaging mockups in the actual
shop environment"}. Photograph the work in real context, NOT on white
backdrop. Palette: derived from the project's actual brand colors, NOT the
portfolio palette. Lighting: realistic, situational. Composition: work
should fill 60-80% of frame. Forbidden: phone-mockup-on-marble aesthetic,
isometric mockup composition with multiple devices, "generic startup brand
showcase" aesthetic. Aspect ratio: project-specific (use whatever shows the
work best — don't force consistency across projects).
```

**Note:** Project covers should NOT be AI-generated for real client work. Use real photos / screenshots / scans of the actual work. AI gen here is only acceptable when the user has no real shot AND is doing speculative work or self-initiated projects.

## Background Texture Templates

### Subtle grain / noise (tileable, all vibes)
```
A seamless tileable grain texture, very subtle, suitable as overlay on flat
color backgrounds. Color: warm gray on transparent. No discernible pattern,
just organic noise. Aspect ratio: 1:1, optimized for tiling.
```

### Editorial paper texture
```
A high-resolution scan of cream-colored uncoated paper with subtle fiber
texture, no folds, no marks. Color: #F5F1E8 cream with warm gray fibers.
Aspect ratio: 1:1, edges should tile seamlessly.
```

### Brutalist concrete
```
A photograph of raw poured concrete surface, harsh side-lighting revealing
texture. Color: cement gray. No pattern, just stochastic surface. Aspect
ratio: 16:9, edges should tile.
```

## Section Divider Templates

### Editorial: hand-drawn line
```
A single hand-drawn ink line on cream paper, slight imperfection, expressive
weight variation. Color: #1A1715 ink. Length: full width. Style: thin
calligraphy line, single stroke. Aspect ratio: 21:9.
```

### Brutalist: raw block
```
A solid black rectangle with hand-stamped texture, slight ink bleeds at
edges. Color: #000000 on transparent. Aspect ratio: 21:1, full-width
divider strip.
```

## OG Image Templates

OG images are 1200×630, render-safe (no thin text, no off-canvas elements).

### Template: typographic OG
```
A typography-driven Open Graph image, 1200×630px. Headline "{H1 from hero}"
in {display font from visual-direction} at 96px, color {ink from palette},
left-aligned with negative space on right third. Background: {background from
palette} with subtle paper texture. Brand mark in upper-right corner at 40px.
No imagery — pure typography and texture. Forbidden: gradients, generic stock
photo, AI template aesthetic.
```

### Template: hero-image OG
```
A 1200×630px composition combining the hero illustration (cropped to focus on
subject) on left 60%, with bold display typography on right 40% containing
"{H1}" and "{tagline}". Match all colors and lighting from hero illustration.
Brand mark bottom-right.
```

## Avatar Templates (testimonials)

Avatars must feel real, diverse, contextual to product.

### Template: editorial avatar
```
A natural portrait photograph of {role description — e.g., "a 40s freelance
designer in their home studio"}, looking slightly off-camera, soft editorial
lighting. Color treatment: {tinted to match palette accent}. Style references:
August Sander portraits, contemporary editorial. Forbidden: stock photo
aesthetic, fake smile, generic office background, perfect symmetry, AI face
artifacts. Aspect ratio: 1:1.
```

### Template: hand-illustrated avatar
```
A loose ink-and-watercolor portrait of {role description}, single sitting,
expressive line, not overly polished. Palette: ink lines + single accent wash
matching {accent color}. Style references: Alvin Lustig, Saul Bass character
work, mid-century editorial illustration. Aspect ratio: 1:1.
```

## Capability Routing

| Asset type | Capability needed | Notes |
|-----------|-------------------|-------|
| Hero illustration (artistic) | Text-to-image with style control (curated style prompt library) | `--mode search` style match |
| Hero photo (realistic) | High-quality text-to-image with palette + lighting + composition control | Strict palette + composition spec; suppress AI-default aesthetic via negative prompts |
| Background texture | Fast text-to-image (lower fidelity, large output) | Tileable; soft / subtle aesthetic |
| OG image | Multi-platform social image composition (HTML→screenshot or text-to-image) | Brand-consistent typography + crop |
| Avatar | Text-to-image with photorealism + anti-stock negatives | Avoid stock-photo "diverse team smiling" tells |

## Validation

After every generation:
1. Open via a vision-capable model (analyze image content)
2. Ask Claude: "Does this image match {palette hex codes} and feel like {vibe}? Score 1-10. List drift."
3. If score < 7 OR palette drift > 30%, regenerate with stronger negative prompt.
4. If score ≥ 8, save to `public/landing/`.

## Negative Prompt Library

Reuse these to suppress common AI defaults:

```
no AI purple/blue gradient
no neon glow, no outer glow on text
no generic tech illustration with circuits/data-flow lines
no Microsoft clip-art aesthetic
no gradient mesh background
no default Octane / Cycles render aesthetic
no cyberpunk hologram unless explicitly requested
no character figures unless explicitly requested
no stock-photo "diverse team smiling at laptop"
no isometric 3D illustration of "abstract concepts"
no chrome / liquid metal material unless vibe is glass-tech
no MeshNormalMaterial rainbow (3D)
no cliché icons inside illustrations (no rocket, no shield, no lightbulb)
```
