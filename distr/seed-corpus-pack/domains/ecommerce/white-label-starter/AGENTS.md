# AGENTS.md — White-Label E-Commerce Starter

## Purpose
Working forkable starter (not a pack regenerator). Prefer extending this code over inventing parallel scaffolds.

## Do
- Keep money as integer minor units + ISO currency
- Keep Order axes orthogonal: payment_state, fulfillment_state, lifecycle_state
- Route payments through `payments.gateways` adapter (FakeGateway in tests)
- Register every domain model in Unfold admin
- Keep brand tokens in CSS vars + `config/brand.py` (single source for FE + Unfold)
- Run backend pytest + frontend build before claiming done

## Don't
- Copy live Merrypak source from WORK/CRYSTALLOGIC
- Commit real payment secrets or spend against live gateways
- Force-push; do not push `tensology-bare` if it rejects
- Half-wire features — finish the vertical slice

## Key paths
- Models: `backend/shop/models.py`, `backend/accounts/models.py`, …
- Payment seam: `backend/payments/gateways.py`
- Brand: `backend/config/brand.py`, `frontend/src/styles/tokens.css`
- Stencils: `planning/stencils/*.wire`
