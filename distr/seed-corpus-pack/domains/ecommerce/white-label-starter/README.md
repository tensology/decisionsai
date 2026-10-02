# White-Label E-Commerce Starter

Forkable working codebase (~90% production-ready vertical slice) for white-label shops.

**Not** a regenerate-from-spec pack — this is runnable Django + Vite React you can fork and brand.

## Stack

| Layer | Choice |
|-------|--------|
| Backend | Django 5 + django-unfold admin + DRF |
| Frontend | Vite + React 18 + TypeScript + Tailwind (CSS-var brand tokens) |
| DB | SQLite by default (out of the box) |
| Payments | Adapter seam + FakeGateway (PayFast/Stripe stubs) |

## What "90%" means

- Core commerce models, orthogonal order axes, money as integer minor units
- Auth flows (register/login/activate/password reset/profile/email/password change)
- Catalog, cart, idempotent checkout, orders
- Payment adapter + webhook path
- Legal pages, transactional email templates
- Unfold admin with brand color bridge documented
- Storefront pages that look like real pages
- Planning ERD + Decisions wireframe DSL stencils
- Automated tests green before you ship

Intentionally out of scope: production deploy, real payment credentials, multi-warehouse inventory, returns/RMA, promotions engine.

## Quick start

```bash
# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # optional
python manage.py migrate
python manage.py seed_demo    # products, legal pages, admin
python manage.py runserver 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # http://localhost:5173 → proxies /api to :8000
```

Or from repo root: `./bin/start` / `./bin/stop`.

## Admin (Unfold)

- URL: http://127.0.0.1:8000/admin/
- Default after `seed_demo`: `admin@example.com` / `adminpass123`
- `UNFOLD["COLORS"]` bridged to brand primary tokens — see `backend/config/brand.py` and README section below.

### BrandPack → Unfold COLORS

Later, a BrandPack JSON can map:

```json
{ "primary": { "50": "#f0f9ff", "500": "#0ea5e9", "600": "#0284c7", "900": "#0c4a6e" } }
```

into `UNFOLD["COLORS"]["primary"]` via `config.brand.apply_brand_to_unfold(brand_dict)`. Frontend reads the same tokens as CSS variables (`--color-primary-*`) and Tailwind theme extension.

## Tests

```bash
cd backend && source .venv/bin/activate && pytest -q
cd frontend && npm run build
# See TEST-RESULTS.md
```

## Layout

```
backend/     Django project (accounts, shop, pages, mailer, payments)
frontend/    Vite React storefront
planning/    ERD mermaid + wireframe DSL stencils
bin/         start/stop helpers
```

## License

Internal Tensology / Decisions seed — fork freely for client white-labels.
