# Merrypak — primary e-commerce structural reference

**Source (READ ONLY):** `/Users/paul/development/WORK/CRYSTALLOGIC/www.merrypak.co.za`  
**Also:** `/Users/paul/Downloads/Work/Merrypak` (client docs/PDFs — not mined into code patterns)  
**Kind:** COPIED observation + curated planning docs. **No edits** to Merrypak.

Paul referred to this as “Meripack”; the project folder is **Merrypak** / `www.merrypak.co.za`.

## Why this is primary

Full production-shaped B2B/B2C packaging e-commerce: customer shop + auth + checkout, Django admin, ERP/D3 integration, Celery jobs, search, compliance docs. Prefer this layout when scaffolding “build an e-commerce website.”

## Stack (observed)

| Layer | Choices |
|-------|---------|
| FE | React 18 + Vite, Tailwind (+ Material Tailwind), MUI, react-router, axios, i18next, Playwright |
| BE | Django `backend/webapp` apps: `shop`, `accounts`, `pages`, `mailer`, `d3_api`, `woocommerce`, `translations`, `commands` |
| Style | Font **Arimo**; Tailwind `primary` sky/blue scale; max content width ~1800px |
| Ops | `start`/`stop`/`restart`, nginx+systemd under `conf/`, Celery workers, pubsub |

## Folder layout (scaffold target)

```
project/
  AGENTS.md, CLAUDE.md
  frontend/          # Vite React app
    src/pages/{shop,auth,core,blog}
    src/components/{ui,auth,checkout,...}
    src/hooks, contexts, utils, assets
    tailwind.config.js, playwright.config.js
  backend/webapp/    # Django project
    apps/{shop,accounts,pages,mailer,d3_api,...}
    core/, templates/, static/, styleguide/
    manage.py
  conf/              # nginx, systemd, cron
  bin/               # start/stop helpers
  docs/              # compliance, integrations, plans
  planning/          # FRAC-ish numbered docs (23–30)
  db/, pubsub/, setup/
```

## UI conventions to copy

- **Customer portal:** `pages/shop` + `components/checkout` (list/detail product, cart, account).
- **Admin:** Django Unfold admin (not a separate React admin) — for greenfield React admin prefer `vendor/open-dashboard` patterns alongside Merrypak shop FE.
- **i18n:** `public/locales` + i18next.
- **Design tokens:** copy `layout/tailwind.config.js` as starting theme; rebrand primary as needed.

## Domain hooks

Map Merrypak shop concepts onto pack `01-entity-domain/entity-graph.yaml` (Customer, Product/Variant, Cart, Order, Shipment/split-delivery). See `docs-curated/superpowers-plans/` for split-shipping packing plan.

## Live reference

Do not duplicate the full repo into the pack. Use `SOURCE.txt` / this path when an agent needs deeper code.
