# Done-states per flow — SYNTHESIZED

| Flow | Done when |
|------|-----------|
| Catalog browse | List+detail render; variant selection; ATP shown; empty/error states |
| Cart | Add/update/remove; price revalidate; reserve policy respected; persist across refresh |
| Checkout | Address+shipping+tax+payment; idempotent place-order; Order snapshot frozen |
| Pay (auth/capture) | Webhook-safe; payment_state correct; receipt emitted |
| Fulfill / ship | Fulfillment lines; tracking; on_hand decremented once; emails |
| Cancel | Compensating transition; inventory released; void/refund path |
| Return/refund | RMA; restock decision; refund partial/full; AC checklist green |
| Admin CRUD | open-dashboard invariants; CONTRACT tests pass |
| FE scaffold | Vite build green; routes stubbed; theme tokens applied |
| BE scaffold | API contract for resource; migrations; healthcheck |
| Trading strategy change | UI_STANDARDS respected; evals/tests green; **no prod execution change unless explicit** |

Iron law: no completion claims without fresh verification evidence.
