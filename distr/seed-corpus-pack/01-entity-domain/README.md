# 01 — Entity domain

## Purpose
Shared commerce entity graph + glossary so agents use one vocabulary.

## Contents
| File | Kind | Notes |
|------|------|-------|
| `entity-graph.yaml` | **SYNTHESIZED** | Graph + state axes from ecommerce-foundations |
| `CONTEXT.md` | **SYNTHESIZED** | Compact glossary (CONTEXT.md format) |
| `INDEX.md` | pointers | Live skill paths + vendor copies |

## Load order
1. This CONTEXT.md / entity-graph.yaml
2. `vendor/ecommerce-foundations/skills/domain-vocabulary/SKILL.md`
3. order-lifecycle → cart-lifecycle → payment-flows → inventory-management
4. `copied-skills/decisionsai/domain-modeling` (how to maintain glossary/ADRs)

## Model inference
See `MODEL-INFERENCE.md` — mandatory expansion of named entities into related models (orders → shipping, billing, customer, line items, …).
