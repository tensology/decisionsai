# TEST-RESULTS — White-Label E-Commerce Starter

Date: 2026-10-02 (Africa/Johannesburg)

## Backend pytest

```bash
cd backend && source .venv/bin/activate && pytest -q
```

**Result: 10 passed**

Coverage exercised:
- Model relations / money sale-price / order axis defaults
- Register + activate email token path
- Password forgot/reset token path
- Idempotent place-order checkout
- FakeGateway webhook → PaymentIntent + Order.payment_state
- Legal pages 200 (terms/privacy)
- Home meta 200
- Unfold admin login page 200
- Catalog list 200

## Frontend build

```bash
cd frontend && npm install && npm run build
```

**Result: PASS** (`tsc --noEmit && vite build` — 55 modules, dist emitted)

## Smoke (Django runserver :8000)

| Path | Status |
|------|--------|
| `/api/pages/home/` | 200 |
| `/api/pages/legal/terms/` | 200 |
| `/api/pages/legal/privacy/` | 200 |
| `/api/pages/legal/about/` | 200 |
| `/api/pages/legal/contact/` | 200 |
| `/api/shop/catalog/` | 200 |
| `/admin/login/` | 200 (Unfold skin) |
| `/api/auth/csrf/` | 200 |
| `POST /api/auth/register/` | 201 |

## Stencil / page verification

### HTML stubs (`planning/stencils/html/`)
All 6 checked (home, about, contact, terms, login, checkout): contain `<h1`, brand string, not error shells — **OK**

### Wireframe DSL (`planning/stencils/*.wire`)
Rendered via DecisionsAI `wireframe.js` `renderWireframe`:
**16 ok, 0 fail** — no Invalid wireframe diagnostics; HTML length > 200 each.

## Notes
- SQLite local; FakeGateway only; console email backend
- Do not commit `.venv/`, `node_modules/`, `db.sqlite3`, `frontend/dist/`
