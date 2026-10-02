# SOURCES — provenance (no edits to originals)

## Local references (READ ONLY)

| Source | Absolute path | In pack |
|--------|---------------|---------|
| ecc | `.../reference/ecc` | curated → `copied-skills/ecc/` + live index |
| claude-code-skills | `.../reference/claude-code-skills` | INDEX only (125 skills) |
| awesome-design-skills | `.../reference/awesome-design-skills` | curated styles |
| open-design | `.../reference/open-design` | live index |
| taste-skill | `.../reference/taste-skill` | curated |
| builderio-skills | `.../reference/builderio-skills` | curated |
| stitch-skills-assessment | `.../reference/stitch-skills-assessment` | curated |
| mattpocock-skills | `.../reference/mattpocock-skills` | curated engineering |
| ponytail | `.../reference/ponytail` | curated |
| superpowers | `.../reference/superpowers` | curated |
| gstack | `.../reference/gstack` | curated |
| fallow | `.../reference/fallow` | curated |
| mempalace | `.../reference/mempalace` | used for ingest CLI; not vendored wholesale |
| DecisionsAI skills | `.../DecisionsAI/skills` | curated subset + domain-modeling |
| ~/.decisions *pack-state.json | `~/.decisions` | copied → `pack-state/` |
| Merrypak | `.../WORK/CRYSTALLOGIC/www.merrypak.co.za` | curated → `domains/ecommerce/merrypak/` |
| currency-trader | `.../TRADING/currency-trader` | curated → `domains/trading-robot/` |

## Public vendor / external

| Source | How | Location |
|--------|-----|----------|
| oa2p-solutions/ecommerce-foundations | `git clone --depth 1` | `vendor/ecommerce-foundations/` |
| ahpxex/open-dashboard | `git clone --depth 1` | `vendor/open-dashboard/` |
| zai-org/Vision2Web | HF download subset (parquet); archives skipped | `external/Vision2Web/` |

## Synthesized (created in pack)

- `01-entity-domain/entity-graph.yaml`, `CONTEXT.md`
- `02-ui-conventions/SCREEN-CONVENTIONS.md`
- `03-done-states-frac/FRAC-TEMPLATE.md`, `DONE-STATES.md`
- `04-verify-feedback-loops/VERIFY-LOOP.md`
- `05-pdf-backend-frontend/CONVENTIONS.md`
- `profiles/*.json`
- `domains/ecommerce/merrypak/ARCHITECTURE.md` (+ JSON observation)

## Policy

- Do **not** modify source repos or Merrypak/currency-trader.
- Do **not** commit this pack unless asked.
- LOCAL ONLY — no prod deploy.

## Behavioral / quality layers (synthesized in pack)

- `07-agent-behavior/*` — SYNTHESIZED rules
- `08-premium-quality-gate/*` — SYNTHESIZED gate over curated taste/awesome-design/builderio/design-references copies
- `09-page-stencils/*` — SYNTHESIZED stencils from open-dashboard + Merrypak + FRAC
- `01-entity-domain/MODEL-INFERENCE.md` — SYNTHESIZED from ecommerce-foundations + Merrypak
