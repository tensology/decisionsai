# 2D Illustration Catalog

11 vibes × recommended 2D illustration styles. Used in Phase 4 to pick HOW to generate visual assets matching the locked vibe.

## Why 2D First

Direct evidence from `plans/260509-ai-vs-human-analysis/synthesis.md`: 0/7 human-crafted landings used real-time 3D models in hero. The strongest visual moments came from 2D craft — silkscreen posters (Paperclip), bubble logotypes (Marblex), ransom-note typography (OWO), static 3D renders treated as 2D images (Augen).

**Rule:** 2D illustration is the default visual asset. Use real-time 3D ONLY for shader/particle/atmospheric effects (Phase 5), never as primary subject.

## Illustration Style Catalog

### 1. Silkscreen Poster
**Look:** Hand-pulled print aesthetic. Limited palette (2-4 inks). Slight registration imperfection. Bold flat shapes with optional grain overlay.
**Vibes:** Editorial, organic, hand-crafted, art-deco
**Reference artists:** Saul Bass, Cassandre, contemporary risograph studios
**Capability:** Text-to-image with style control (search prompts include "silkscreen", "poster art")
**Avoid:** Photographic shading, chrome highlights, gradient meshes

### 2. Hand-drawn Ink (line art)
**Look:** Single-weight or weight-varied ink lines. White space dominant. Slight imperfection in curves.
**Vibes:** Editorial, hand-crafted, luxury (engraved variant), organic
**Reference artists:** Alvin Lustig, mid-century book illustrators, contemporary tattoo artists
**Capability:** High-quality text-to-image with palette + lighting + composition control, prompt anchors "single-line ink drawing", "expressive contour line"
**Avoid:** Heavy fills, crosshatching unless intentional, AI-generated character clichés

### 3. Geometric Flat (vector)
**Look:** Hard-edged shapes, pure colors, no shading, mathematical composition. SVG-friendly.
**Vibes:** Minimal, brutalist, art-deco, industrial
**Capability:** Direct SVG generation by LLM (inline code) OR text-to-image with style control "geometric vector illustration"
**Avoid:** Gradients, shadows, soft edges (against vibe)

### 4. Cut-paper Collage
**Look:** Layered colored paper aesthetic. Visible edges. Slight shadows under cut shapes. Sometimes with hand-cut imperfection.
**Vibes:** Playful, hand-crafted, editorial
**Reference artists:** Henri Matisse cut-outs, Eric Carle, contemporary children's book art
**Capability:** Text-to-image with style control "cut-paper collage" or "Matisse-style cut-out"
**Avoid:** Photographic textures, AI-default 3D blob shapes

### 5. Risograph / Photocopy
**Look:** Imperfect duotone or limited palette overlay (typically pink + blue or cyan + red). Visible printing artifacts. Mid-century industrial feel.
**Vibes:** Brutalist, hand-crafted, editorial
**Reference:** Risograph studios (Hato Press, Risotto, ISO50)
**Capability:** Text-to-image with style control, "risograph print" prompt anchor
**Avoid:** Smooth gradients, polished finish

### 6. Watercolor / Ink Wash
**Look:** Translucent layered washes, organic edges, paper bleed. Loose, expressive.
**Vibes:** Organic, hand-crafted, hand-drawn-ink companion
**Capability:** Text-to-image with style control "watercolor illustration", "ink wash"
**Avoid:** Sharp digital edges, vibrant saturation

### 7. Engraved Line Art
**Look:** Thin parallel lines or crosshatching for shading, monogram-style precision, vintage scientific drawing.
**Vibes:** Luxury, art-deco, editorial
**Reference:** Vintage patent illustrations, Whisky/Spirits packaging, line engraving on banknotes
**Capability:** Text-to-image with creative direction freedom ("wild" mode) using "vintage 1800s patent" — OR text-to-image with style control ("search" mode) using "engraved illustration"
**Avoid:** Modern digital shading, gradient fills

### 8. Architectural Schematic / Blueprint
**Look:** Technical drawing aesthetic — exploded views, cross-sections, dimensional lines, monospace annotations.
**Vibes:** Industrial, brutalist, retro-futuristic
**Capability:** Text-to-image with style control "architectural schematic", "isometric blueprint"
**Avoid:** Soft shading, decorative flourishes

### 9. Static 3D Render (export → 2D)
**Look:** 3D scene rendered in Blender/Spline/KeyShot, exported as PNG/WebP. Visitor sees a 2D image, NOT real-time 3D.
**Vibes:** Glass-tech, retro-futuristic, luxury (if product photography style)
**Reference:** Augen.pro hero head, Apple keynote product shots, Spline community examples
**Capability:** 3D modeling tool (Blender / Spline / KeyShot) exporting PNG/WebP — OR high-quality text-to-image with palette + lighting + composition control, "studio 3D render" prompt
**Critical rule:** This is a 2D ASSET (`<Image src="/landing/hero.webp" />`), NEVER imported as `.glb`
**Avoid:** Default Octane render aesthetic, generic glass-balls-on-marble, AI-generated 3D blob characters

### 10. Photographic / Editorial Photo
**Look:** Real photography — environmental, situational, editorial. Color-graded to match palette.
**Vibes:** Editorial, luxury, organic, glass-tech (product photography)
**Reference:** Saul Leiter, Wolfgang Tillmans, Annie Leibovitz environmental editorial
**Capability:** Real photos (preferred for portfolios with real work) OR text-to-image with photorealism + anti-stock negatives
**Avoid:** Stock-photo aesthetic ("diverse team smiling at laptop"), fake-feeling smile, generic office

### 11. Synthwave Gradient / Vaporwave
**Look:** Vibrant gradient skies, vector wireframe horizons, retro digital aesthetic. Strong magenta/cyan/violet.
**Vibes:** Retro-futuristic ONLY
**Reference:** TRON, vaporwave Tumblr-era art, synthwave album covers
**Capability:** Text-to-image with style control, "synthwave aesthetic" or "vaporwave"
**Avoid:** Using outside retro-futuristic vibe — instantly dates the design

## Vibe → Style Mapping (lookup table)

| Vibe | Primary style | Secondary | Avoid |
|------|--------------|-----------|-------|
| **Minimal** | Geometric flat (SVG) | Hand-drawn ink | Watercolor, photocopy |
| **Editorial** | Silkscreen poster | Hand-drawn ink, photographic | Synthwave, schematic |
| **Brutalist** | Risograph / photocopy | Geometric flat | Watercolor, engraved |
| **Retro-futuristic** | Synthwave gradient | Schematic | Watercolor, hand-drawn ink |
| **Organic** | Watercolor | Hand-drawn ink | Schematic, geometric flat |
| **Luxury** | Engraved line art | Photographic, static 3D render | Risograph, synthwave |
| **Playful** | Cut-paper collage | Hand-drawn ink | Engraved, schematic |
| **Industrial** | Architectural schematic | Geometric flat | Watercolor, cut-paper |
| **Art-deco** | Engraved line art | Geometric flat | Synthwave, photocopy |
| **Glass-tech** | Static 3D render | Photographic | Watercolor, hand-drawn |
| **Hand-crafted** | Hand-drawn ink | Cut-paper, watercolor, risograph | Static 3D render, synthwave |

## Asset Cohesion Rules

**Within a single site, ALL 2D illustrations must share a style language.** Mixing silkscreen posters with synthwave gradients = AI slop.

### Cohesion checklist
1. **Single illustration style** across hero, dividers, section accents (pick one row from above table)
2. **Single palette** — all illustrations use the locked palette from `visual-direction.md`, no rogue colors
3. **Single line weight / stroke philosophy** if line-based (matches icon set stroke from `custom-icon-pipeline.md`)
4. **Single composition language** — if asymmetric heroes, asymmetric mid-section illustrations too
5. **Single texture treatment** — grain everywhere OR grain nowhere; not "grainy hero, clean section illustrations"

### Cross-asset cohesion
The site's icon set + 2D illustrations + photos + (optional) static 3D render must all feel produced by the same hand. Audit:
- Do icon corner family + illustration line endings match? (rounded everywhere or sharp everywhere)
- Do illustration colors come from the locked palette only?
- Do photos/3D renders share the lighting language? (warm afternoon vs cool studio vs harsh flash)
- Do all assets share a depth-of-field discipline? (all flat OR all dimensional, not mixed)

## Asset Generation Workflow

### Step 1 — Pick style from vibe table
Open this catalog, find vibe row, pick primary style. Secondary only if primary fails to deliver hero.

### Step 2 — Build prompt (per `visual-asset-prompt-library.md`)
Use the prompt anatomy template with:
- `Style references` field populated from this catalog's "Reference artists / sources" line
- `Forbidden` field includes default forbidden + style-specific forbidden from this catalog

### Step 3 — Generate via capability
- Style 1, 2, 4, 5, 6, 7, 11 → text-to-image with style control (curated style prompt library)
- Style 9 (static 3D render) → 3D modeling tool (Blender / Spline / KeyShot) OR high-quality text-to-image with palette + lighting + composition control
- Style 10 (photographic) → real photos preferred; AI fallback uses text-to-image with photorealism + anti-stock negatives
- Style 3 (geometric flat) → direct SVG generation by LLM (inline code) preferred
- Style 8 (architectural schematic) → text-to-image with style control, or vector tool

### Step 4 — Validate cohesion
After EACH generation:
1. View image with a vision-capable model (analyze image content)
2. Compare to locked palette (extract dominant colors)
3. Compare style to catalog row (does it match the style description?)
4. If drift > 30%, regenerate with stronger negative prompt
5. After ≥ 3 assets generated, run cross-asset cohesion check (do they all feel one hand?)

## Examples by Vibe (concrete output)

### Editorial — Specialty Coffee Subscription
- Hero: silkscreen poster of folded letter releasing coffee beans (style 1)
- Section dividers: hand-drawn ink line motifs (style 2)
- Background: cream paper texture (style 1 / 6 hybrid)
- OG image: typographic + small silkscreen icon

### Glass-Tech — Wearable Device
- Hero: static 3D render of device on infinite gradient background, exported PNG (style 9)
- Section accents: photographic detail shots of device (style 10)
- Background: subtle CSS gradient + grain (no illustration)
- OG image: device shot cropped tight + display headline

### Playful — Kids Learning App
- Hero: cut-paper collage scene (style 4)
- Mascot: cut-paper character (style 4)
- Section illustrations: hand-drawn doodles (style 2)
- Background: warm cream + organic grain

### Brutalist — Dev Tool
- Hero: risograph-style print of mechanical metaphor (style 5)
- Section accents: hard-edged geometric flat shapes (style 3)
- Background: photocopy texture
- All type in mono with imperfect alignment

## Anti-Patterns

- ❌ Mixing styles across sections (silkscreen hero + synthwave footer = chaos)
- ❌ Using a style outside its vibe column (synthwave on a luxury site)
- ❌ AI-generated 3D character / blob as hero subject (use static 3D render or 2D illustration instead)
- ❌ Stock illustrations from `unDraw` / `Storyset` libraries — instantly identifiable as generic
- ❌ Multiple illustration styles competing for visual weight
- ❌ Illustration palette drift — using colors NOT in the locked palette
- ❌ Generic AI cute illustrations (penguin with laptop, generic 3D blob people)

## Cross-Reference

- **Prompt templates per style:** `visual-asset-prompt-library.md`
- **Color palette per vibe:** `visual-direction-guide.md` § Vibe → Palette Mapping
- **Icon set cohesion (must align with illustrations):** `custom-icon-pipeline.md`
- **What's allowed in Phase 5 (effects, NOT models):** `visual-effect-patterns.md`
