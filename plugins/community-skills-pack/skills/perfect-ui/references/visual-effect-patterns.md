# Visual Effect Patterns (Phase 5)

How to add a Visual Effect Layer (shaders, particles, atmospheric) to a Next.js landing or portfolio. **No 3D models** as hero subjects — only effects that enhance the page.

## Scope Distinction (effect vs motion vs model)

| Category | File | Status | Examples |
|----------|------|--------|----------|
| **3D model as hero subject** | n/a | FORBIDDEN | Rotating product GLB, AI-generated 3D character, GLTF showcase, OrbitControls demo |
| **Static 3D render → 2D image** | `2d-illustration-catalog.md` (Phase 4) | OK | Blender/Spline render exported as PNG, used in `<Image>` |
| **Visual effect (this file)** | `visual-effect-patterns.md` (Phase 5) | OK | Shader background, particle field, scroll-driven distortion, displacement plane, atmospheric layer |
| **Motion (UI animation)** | `motion-patterns.md` (locked Phase 2e, applied Phase 7) | OK — vibe-scaled | Entrance reveals, hover lifts, scroll-linked transforms, smooth scroll baseline |
| **User-provided real-product GLB** | n/a | OK with logged override | Hardware brand wants product showcase; document in `plans/{date}-{slug}/overrides.md` |

**Rule of thumb:**
- **Geometry as SUBJECT** → 3D model — forbidden
- **Geometry as CANVAS** for shader/atmosphere → visual effect — Phase 5
- **DOM elements transforming** (translate, opacity, stagger) → motion — Phase 2e/7, see `motion-patterns.md`

## When to Add a Visual Effect Layer

Effects earn their place when they:
1. **Set atmosphere** for the vibe (dawn fog for organic, refractive light for glass-tech)
2. **Reward scroll** with motion that feels narrative-driven (not decorative)
3. **Anchor a single hero moment** (overlay's subtle color gradient on portrait)

Effects DON'T earn their place when they:
- Are added "for engagement"
- Show off Three.js skills
- Require 200KB+ to display something CSS could deliver

**Default = no effect.** Restraint is more confident.

## Decision Tree

```
Need visual movement / atmosphere?
├─ Can pure CSS deliver it? (gradient, animation, transition, blur, noise overlay)
│   └─ YES → use CSS. Don't load WebGL for what CSS can do.
├─ Need real-time procedural generation (noise, fluid, distortion)?
│   └─ YES → shader (GLSL fragment, or TSL on RTF)
├─ Need many particles (>200 instances)?
│   └─ YES → instanced points/meshes + shader on RTF, OR particle library
├─ Need scroll-linked transformation across long page?
│   └─ YES → GSAP ScrollTrigger + Lenis smooth-scroll, optionally driving shader uniform
├─ Need cursor-reactive subtle effect?
│   └─ YES → shader uniform driven by mouse coords, OR CSS conic-gradient + JS
└─ Just adding "wow" with no specific need?
    └─ STOP. Don't add. Page reads better without it.
```

## Stack Choices

### Tier 1 — CSS only (preferred for atmosphere)
- Linear / radial / conic gradients
- `backdrop-filter: blur()`
- Noise PNG overlay with `mix-blend-mode`
- CSS animations (e.g., slow-rotating background)
- `mask-image` for organic shapes

**Bundle cost:** 0 KB. **First choice.**

### Tier 2 — CSS + light JS (animation choreography)
- **Lenis** for smooth scroll (~10 KB)
- **Framer Motion** for scroll-linked transforms (~25 KB)
- **GSAP + ScrollTrigger** for complex sequencing (~50 KB)

**When:** Need scroll-driven motion across multiple sections.

### Tier 3 — Shader effects via RTF (only when CSS can't)
- React Three Fiber as shader runner (NOT 3D model viewer)
- Custom GLSL fragment shaders OR TSL (Three Shader Language)
- Postprocessing for bloom/grain/distortion if vibe demands

**When:** Procedural noise, fluid simulation, displacement, gradient that CSS can't reach.

### Tier 4 — Lottie / SVG animation (alt to shaders)
- Lottie for After-Effects-style 2D motion graphics
- SMIL / CSS-animated SVG for icon-scale animation

**When:** 2D motion that benefits from designer-controlled timing.

## Approved Patterns

### A. Atmospheric gradient mesh (CSS first, shader fallback)

**CSS version (preferred):**
```css
.hero {
  background:
    radial-gradient(at 20% 30%, hsl(var(--accent) / 0.4), transparent 50%),
    radial-gradient(at 80% 70%, hsl(var(--accent) / 0.3), transparent 50%),
    hsl(var(--bg));
  background-blend-mode: overlay;
}
.hero::after {
  content: '';
  position: absolute; inset: 0;
  background-image: url('/landing/grain.png');
  mix-blend-mode: multiply;
  opacity: 0.04;
}
```

**Shader version (when palette needs animated transition):**
Use a single fullscreen plane in RTF with a fragment shader that interpolates colors over noise. Single `<mesh>` + `<planeGeometry>` + `<shaderMaterial>` — no model.

### B. Particle field (shader-driven points)

**Use case:** Retro-futuristic vibe, glass-tech vibe.

**Pattern:**
- `<bufferGeometry>` with N points (e.g., 5000)
- `<pointsMaterial>` or custom shader for size + color
- Animate via shader uniforms (time, scroll progress) — don't update positions in CPU loop

**Pseudocode:**
```tsx
'use client';
import { Canvas, useFrame } from '@react-three/fiber';
import { useRef, useMemo } from 'react';
import * as THREE from 'three';

function ParticleField() {
  const ref = useRef<THREE.Points>(null);
  const positions = useMemo(() => {
    const arr = new Float32Array(5000 * 3);
    for (let i = 0; i < 5000; i++) {
      arr[i * 3] = (Math.random() - 0.5) * 10;
      arr[i * 3 + 1] = (Math.random() - 0.5) * 10;
      arr[i * 3 + 2] = (Math.random() - 0.5) * 10;
    }
    return arr;
  }, []);

  useFrame((state) => {
    if (ref.current) ref.current.rotation.y = state.clock.elapsedTime * 0.05;
  });

  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial size={0.02} color="hsl(var(--accent))" />
    </points>
  );
}
```

### C. Displacement plane (shader-driven wave)

**Use case:** Organic vibe, atmospheric vibe.

**Pattern:**
- Single `<planeGeometry>` (high segment count)
- Custom vertex shader displaces Z based on noise + time
- Fragment shader colors based on displacement amount

This is shader-as-effect, not 3D model.

### D. Scroll-driven distortion / reveal

**Use case:** Editorial portfolio (isadeburgh), retro-futuristic landing.

**Pattern:**
- Lenis smooth scroll
- GSAP ScrollTrigger drives shader uniform `uScrollProgress`
- Shader distorts texture / color / position based on uniform

Pure CSS alternative: `clip-path` + `transform` driven by IntersectionObserver — no WebGL needed.

### E. Cursor-reactive accent (subtle)

**Use case:** Interactive accent element (a logo mark, a single "live" element).

**CSS version (preferred):**
```css
.accent {
  background: radial-gradient(
    circle at var(--mx, 50%) var(--my, 50%),
    hsl(var(--accent)),
    transparent 60%
  );
}
```
```js
el.addEventListener('pointermove', (e) => {
  const r = el.getBoundingClientRect();
  el.style.setProperty('--mx', `${(e.clientX - r.left) / r.width * 100}%`);
  el.style.setProperty('--my', `${(e.clientY - r.top) / r.height * 100}%`);
});
```

**Shader version:** uniform `uMouse` drives fragment shader.

### F. Postprocessing for film/grain feel

**Use case:** Editorial, organic, hand-crafted vibes.

**Pattern (CSS-only):**
- Static grain PNG with `mix-blend-mode: overlay` and low opacity
- No WebGL needed

**Pattern (shader-only when CSS can't, e.g., animated grain):**
- RTF + `EffectComposer` + `Noise` from `postprocessing` package

## Forbidden Patterns

These STAY forbidden even within "effects" framing:

### F1. 3D model as hero subject
- Rotating product GLB
- AI-generated 3D character / blob / mascot
- Imported `.glb` / `.gltf` files (unless user-provided real product, logged override)

### F2. Stock Three.js demos
- "Cube/sphere/torusKnot rotates in middle of viewport" — generic AI-default
- `MeshNormalMaterial` rainbow
- Default lighting + perspective camera + OrbitControls = "viewer demo" aesthetic

### F3. Effect-for-effect's-sake
- Particle field with no compositional purpose
- Distortion that obscures readability
- Custom cursor that fights screen-reader / mobile

### F4. Always-on render loop
- `frameloop="always"` for static scenes (burns CPU)
- Particles that animate when off-screen
- No `prefers-reduced-motion` respect

### F5. Heavy bundle for decorative purpose
- 200KB+ added for effect that adds no narrative weight
- Multiple shader passes when one would suffice
- Postprocessing chain with 4+ effects

### F6. OrbitControls enabled in landing/portfolio
- Signals "viewer demo" not "designed page"
- Visitor doesn't want to "explore 3D"

## User-Provided GLB Exception

If user explicitly says "I have a GLB of my real product, I want it on the landing":
1. Confirm the GLB shows a real, shippable product (not a generic shape)
2. Log the override in `plans/{date}-{slug}/overrides.md`:
   ```
   - 3D model in hero — user provided real-product GLB at /public/landing/product.glb
   - Reason: hardware brand showcasing actual product
   - Date: 2026-MM-DD
   ```
3. Apply standard React Three Fiber guidance (Draco compression, `<Suspense>` fallback, `dpr={[1, 2]}` cap, lazy-load with `dynamic({ ssr: false })`)
4. Performance budget: GLB ≤ 500KB, total 3D bundle ≤ 200KB JS

## Performance Guardrails (all effects)

- Bundle: total effect JS ≤ 100KB gzipped (CSS preferred for ≤ 30KB ceiling)
- `prefers-reduced-motion`: pause / disable shader uniforms updates
- `IntersectionObserver`: pause render loop when off-screen
- `frameloop="demand"` for static-state shaders (only redraw on input/scroll)
- `dpr={[1, 2]}` if RTF Canvas used (cap pixel ratio)
- Mobile: degrade to static gradient at <768px viewport

## Vibe → Effect Layer Recipes

### Minimal
- CSS-only conic gradient + grain → atmosphere
- Optional cursor-reactive accent on logo mark
- Forbidden: shader, particles (too much for vibe)

### Editorial
- CSS noise overlay
- GSAP scroll-linked transforms on display type
- Forbidden: WebGL (vibe is print, not screen-tech)

### Brutalist
- CSS hard-edged transforms
- IntersectionObserver-driven sharp reveals (no easing)
- Forbidden: smooth shader gradients (against vibe)

### Retro-futuristic
- Particle field (shader-driven points)
- Synthwave gradient backgrounds (shader animated)
- Scroll-linked grid distortion
- Approved use of WebGL

### Organic
- Displacement plane (shader wave)
- Slow CSS gradient animation
- Forbidden: hard-edged motion

### Luxury
- Subtle CSS gradient + grain
- Lenis smooth scroll for readerly pace
- Forbidden: particles, distortion (cheapens)

### Playful
- Lottie / SMIL animations for character
- CSS hover bounces (spring physics)
- Forbidden: shader complexity (vibe is hand-feel)

### Industrial
- IntersectionObserver hard reveals
- CSS-only structure animations
- Forbidden: organic motion, particles

### Art-deco
- Geometric SVG path animations (SMIL)
- Slow CSS rotation on heritage motifs
- Forbidden: WebGL (vibe predates digital)

### Glass-tech
- Refractive shader on hero plane (frosted glass effect)
- Subtle particle accent
- Approved use of WebGL

### Hand-crafted
- CSS-only with paper texture
- Lottie sketch-line animations
- Forbidden: WebGL, smooth gradients

## Implementation Checklist

Before shipping any visual effect:
- [ ] Bundle size measured (effect JS ≤ 100KB gzipped)
- [ ] CSS-only attempt was made first; chose WebGL only after CSS proved insufficient
- [ ] No GLB/GLTF imported (unless user-override logged)
- [ ] No OrbitControls in landing/portfolio
- [ ] `frameloop="demand"` if state is static
- [ ] `prefers-reduced-motion` respected
- [ ] Mobile fallback in place (degrade to CSS-only or static)
- [ ] `IntersectionObserver` pauses off-screen render
- [ ] Effect serves narrative, not decoration

## When to Skip This Phase Entirely

If Phase 2d returned "none" OR if CSS delivers what's needed → skip Phase 5. Move directly to Phase 6 (plan).

The best human-crafted landings often use NO visual effects at all (paperclip, owo, augen). Restraint is the premium choice.
