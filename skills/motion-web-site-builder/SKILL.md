---
name: motion-web-site-builder
description: Build a motion website implementation plan (and optional starter structure) from motion prompt briefs using accessible 3D/animated UI patterns.
license: MIT
---

# Motion Web Site Builder

**I am helping create a usable 3D motion website so the user gets a fast, readable, visually distinctive page instead of a static mockup.**

## Scope

This skill produces implementation guidance and starter structure for motion-first web pages.

It is intended for:
- React or plain HTML/CSS/JS stacks
- 3D-inspired depth, parallax, and camera-aware interactions
- Landing sections with strong CTA clarity

## Inputs

- Prompt direction from `decisions-motionsites-prompt-researcher`
- Target stack and file structure
- Performance/accessibility constraints

## Delivery format

1. **Decision board**: what motion patterns are enabled/disabled and why.
2. **Token set**: spacing, palette, font, duration, easing.
3. **Component map**:
   - hero
   - feature reveal section
   - CTA block
   - footer
4. **Motion plan**:
   - scroll response rules
   - hover response rules
   - reduced-motion behavior
5. **Starter implementation**:
   - if asked for HTML-only: provide `<index>.html` plus CSS + JS
   - if asked for React: provide component + stylesheet scaffold

## Must-follow constraints

- Keep one primary call to action visible in each major section.
- Avoid motion loops that repeat faster than one full cycle every 6s unless user requests it.
- Always include low-motion fallback states.
- Respect user accessibility: contrast and readable copy first, decorative movement second.

## Quality checks

- Can a first-time visitor identify the value proposition in 2 seconds?
- Do motion transitions clarify meaning instead of adding noise?
- Can keyboard users reach primary CTA without waiting on animation?
- Are transitions smooth on mid-tier hardware (no continuous heavy filters)?

## Output examples

- Provide a concise “implementation packet” with file names and snippets.
- Include clear next steps to move from concept to shipped page.
