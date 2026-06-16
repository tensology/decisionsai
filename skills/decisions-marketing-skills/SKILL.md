---
name: decisions-marketing-skills
description: Index for Corey Haines marketing skills — use product-marketing first, then pull other skills from the reference clone without duplicating all 44 into the harness.
---

# Decisions Marketing Skills

## Already in your harness (projected)

- **product-marketing** — read this first for any marketing task
- **cro**, **copywriting**, **seo-audit**, **ai-seo**, **competitors** — when not already present from your global install

## ECC skills (prefer for Decisions workflows)

- **content-engine**, **article-writing**, **marketing-campaign**, **brand-voice**, **seo** — native ECC; use when the workflow lists them

## On demand (no bloat)

Full library lives at `../reference/marketingskills/skills/<id>/` (44 skills).

Install one skill:

```bash
npx skills add coreyhaines31/marketingskills@<skill-id>
```

Examples: `emails`, `launch`, `analytics`, `churn-prevention`, `paywalls`

## Pairing

| Task | Skills |
|------|--------|
| Ship landing copy | product-marketing → copywriting → humanizer |
| SEO + AI citations | seo-audit → ai-seo → ECC `seo` |
| Competitor page | competitors → agent-reach (live fetch) |
| UI + conversion | cro → decisions-ui-ideation |

Do not load every marketing skill into context — pick 1–3 for the ticket.
