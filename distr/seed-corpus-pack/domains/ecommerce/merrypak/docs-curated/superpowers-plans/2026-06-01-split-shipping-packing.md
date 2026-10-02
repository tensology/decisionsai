# Split Shipping Packing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make split shipping quote and packing calculations use only the stock that can ship now, while wait-all orders continue to calculate the full basket.

**Architecture:** Keep the packing calculation as the source of truth. Checkout sends a customer choice (`wait` vs `split`) and backend maps split to `exclude_out_of_stock=True` for packing/courier only; the order lines and D3 order submission remain the full customer order. Tests prove the same quantity split is used in live courier quote, order carton report, checkout validation, and customer-facing summary.

**Tech Stack:** Django/DRF, Celery task wrappers, existing `generate_order_carton_payload` packing code, React checkout UI, Vite build, Django tests.

## Status Update — 2026-06-01

Core implementation is complete and verified. Remaining work is manual QA against real Merrypak products, real stock, and the live admin/customer journeys.

Verified commands:

```bash
cd backend/webapp
~/.virtualenvs/merrypak/bin/python manage.py test \
  shop.tests.test_checkout_shipping_rules \
  shop.tests.test_shipping_accuracy \
  shop.tests.test_production_invariants \
  --keepdb

~/.virtualenvs/merrypak/bin/python manage.py check

cd ../../frontend
npm run build
```

Known verification noise: WeasyPrint native-library warnings, noisy D3 404 logs in checkout tests, outdated Browserslist/chunk-size build warnings. Commands exited successfully.

---

## Files

- Modify: `backend/webapp/apps/shop/tasks.py`
  - Own the split packing calculation through `generate_order_carton_payload`.
  - Add/verify testable metadata showing ordered, shipped-now, and balance quantities.
- Modify: `backend/webapp/apps/shop/views.py`
  - Parse `split_shipping` in `CalculateShippingView`.
  - Pass split mode into async live-rate task.
  - Generate order carton report with split mode.
- Modify: `backend/webapp/apps/shop/utils.py`
  - Generate email/backorder packing PDFs with split mode when the order has `split_shipping=True`.
- Modify: `frontend/src/pages/shop/checkout.jsx`
  - Keep the wait/split choice explicit.
  - Block courier calculation until choice is made when stock is short.
  - Send `split_shipping` to shipping calculation and submit payload.
- Modify: `frontend/src/pages/shop/checkout/SplitDeliverySection.jsx`
  - Show first shipment and balance quantities from the same stock data checkout uses.
- Modify: `frontend/src/pages/shop/checkout/checkoutSubmitUtils.js`
  - Confirm submitted order payload carries `split_shipping` and choice fields consistently.
- Test: `backend/webapp/apps/shop/tests/test_checkout_shipping_rules.py`
  - Checkout shipping endpoint and async live-rate handoff.
- Test: `backend/webapp/apps/shop/tests/test_production_invariants.py`
  - Collection and normal packing invariants.
- Test: `backend/webapp/apps/shop/tests/test_shipping_accuracy.py`
  - Live courier packing behavior when split mode is on.

---

### Task 1: Lock The Packing Quantity Contract

- [x] Write a failing test in `backend/webapp/apps/shop/tests/test_checkout_shipping_rules.py` for split packing:
  - Product variant ordered `10`, DB `quantity_on_hand=3`.
  - Product insert ordered `5`, DB `quantity_on_hand=0`.
  - Call `generate_order_carton_payload(order, exclude_out_of_stock=True, include_ai_report=False)`.
  - Assert the payload packs the variant as quantity `3` and excludes the insert.
  - Assert `stock_adjustments` includes both the reduction and exclusion.

- [x] Run:

```bash
cd backend/webapp
python manage.py test shop.tests.test_checkout_shipping_rules --keepdb
```

Expected: the new split packing assertions fail if metadata is missing or wrong.

- [x] Update `backend/webapp/apps/shop/tasks.py` only if the current `generate_order_carton_payload` does not expose enough structured detail for the assertions. Keep the existing behavior that reads `ProductVariant.quantity_on_hand` and `ProductInsert.quantity_on_hand` from the DB.

- [x] Re-run the focused test until it passes.

### Task 2: Prove Wait-All Still Packs Full Quantities

- [x] Add a companion test with the same order and stock values, but call:

```python
generate_order_carton_payload(order, exclude_out_of_stock=False, include_ai_report=False)
```

- [x] Assert the full ordered quantities are packed and `stock_adjustments` is empty.

- [x] Run the same focused test file and confirm both split and wait-all cases pass.

### Task 3: Wire Checkout Courier Quote To Split Packing

- [x] Add a test around `CalculateShippingView` in `backend/webapp/apps/shop/tests/test_checkout_shipping_rules.py`:
  - POST `/api/checkout/calculate-shipping/` with `shipping_method='courier'`, a valid address, and `split_shipping=True`.
  - Mock `run_checkout_live_rate_calculation.delay`.
  - Assert it is called with `exclude_out_of_stock=True`.

- [x] Add the inverse test with `split_shipping=False`.

- [x] Run:

```bash
cd backend/webapp
python manage.py test shop.tests.test_checkout_shipping_rules --keepdb
```

Expected: both handoff tests pass.

### Task 4: Wire Async Live Rate To Packing

- [x] Add a test in `backend/webapp/apps/shop/tests/test_shipping_accuracy.py`:
  - Mock `generate_order_carton_payload`.
  - Call `get_live_courier_rate_for_basket(..., exclude_out_of_stock=True, include_ai_report=False)`.
  - Assert `generate_order_carton_payload` receives `exclude_out_of_stock=True`.

- [x] Add a task-level test for `run_checkout_live_rate_calculation(..., exclude_out_of_stock=True)` if the current Celery test harness supports direct task invocation.

- [x] Run:

```bash
cd backend/webapp
python manage.py test shop.tests.test_shipping_accuracy --keepdb
```

Expected: live courier quote uses the split packing path.

### Task 5: Wire Order Carton Reports To Split Packing

- [x] Add tests in `backend/webapp/apps/shop/tests/test_production_invariants.py`:
  - `Order(split_shipping=False)` calls `generate_order_carton_payload(..., exclude_out_of_stock=False)`.
  - `Order(split_shipping=True)` calls `generate_order_carton_payload(..., exclude_out_of_stock=True)`.
  - Collection orders still do not call live courier pricing.

- [x] Run:

```bash
cd backend/webapp
python manage.py test shop.tests.test_production_invariants --keepdb
```

Expected: normal orders, split orders, and collection orders each preserve their intended packing behavior.

### Task 6: Make Checkout UI Match The Backend Contract

- [x] Confirm `frontend/src/pages/shop/checkout.jsx` keeps three states:
  - `splitDeliveryChoice === null`: no courier quote allowed while stock is short.
  - `splitDeliveryChoice === 'wait'`: courier quote uses `split_shipping=false`.
  - `splitDeliveryChoice === 'split'`: courier quote uses `split_shipping=true`.

- [x] Confirm `SplitDeliverySection.jsx` shows, per short-stock line:
  - ordered quantity
  - available now quantity
  - balance quantity

- [x] Confirm `checkoutSubmitUtils.js` sends `split_shipping` consistently with the user choice.

- [x] Run:

```bash
cd frontend
npm run build
```

Expected: Vite build passes.

### Task 7: Add Regression Checks For Customer Messaging

- [x] Add or update frontend tests if the project has a working React test harness. If it does not, add a focused non-test verification note to `tasks.md` and rely on build plus manual checkout QA.

- [ ] Manual QA checklist:
  - [ ] Basket has no shortage: split delivery section hidden; courier works normally.
  - [ ] Basket has one short line: split delivery section appears; no courier calculation before choice.
  - [ ] Choose wait: courier calculation uses full basket.
  - [ ] Choose split: courier calculation uses available stock only.
  - [ ] Order confirmation / packing report explains this shipment versus balance.

### Task 8: Final Verification

- [x] Run all focused backend tests:

```bash
cd backend/webapp
python manage.py test \
  shop.tests.test_checkout_shipping_rules \
  shop.tests.test_shipping_accuracy \
  shop.tests.test_production_invariants \
  --keepdb
```

- [x] Run frontend build:

```bash
cd frontend
npm run build
```

- [x] Update `tasks.md` with exact tests run and any remaining manual QA gaps.

---

## Explicit Non-Goals For This Pass

- Do not change D3 order submission to send only in-stock lines. The customer order remains the full order.
- Do not auto-charge or auto-calculate courier for the later balance shipment.
- Do not implement dual quote comparison until the single selected-path split calculation is fully tested.
