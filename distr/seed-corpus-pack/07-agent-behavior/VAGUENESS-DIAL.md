# Vagueness dial / question budget — SYNTHESIZED (hard rule)

Calibrate clarifying questions to how sharp the request is. Do not interview by default; do not silently invent architecture when the ask is foggy.

## Score the request (0–5)

| Score | Signals | Question budget | Action |
|------:|---------|-----------------|--------|
| 0–1 **Vague** | “build me a shop”, no stack, no audience, no entities named | **4–7** targeted questions | Ask before scaffolding. Cover: audience (B2B/B2C), surfaces (admin/shop), must-have flows, brand/tone, stack constraints. |
| 2–3 **Mixed** | Some entities or screens named; gaps elsewhere | **2–4** questions | Ask only about gaps that change architecture (auth model, payment timing, multi-warehouse). Infer the rest from pack profiles. |
| 4–5 **Sharp** | Named entities, flows, stack, or “like Merrypack” | **0–1** questions | Infer defaults from `profiles/*.json` + entity graph; **just go**. One confirm only if a choice is hard to reverse. |

## What counts as a good question

- Binary or short multiple-choice when possible.
- Names the decision and the default you will take if unanswered.
- Never ask what the pack already answers (order↔line items, list+detail admin shapes).

## Anti-patterns

- 12-question intake for “add a products CRUD page.”
- Zero questions when user said “something like Shopify but for industrial parts” (vague + high stakes → ask).
- Asking about font/color before FRAC when they already said “enterprise dark admin.”
