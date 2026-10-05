# Cost ledger: decisions needing operator review

Local implementation shipped with the defaults below. Call these out before treating
the ledger as invoice-ready.

## Open calls

1. **Client identity** — Projects have **no** `client_id` column. Today `client_key` is
   derived as a slug of the project name (or empty). Do you want a real Client ORM /
   FK, a free-text field on Project, or keep slug-only?

2. **Pricing source of truth** — Static table in `distr/core/cost_ledger/pricing.py`
   (USD / 1M tokens). Switch later to provider usage APIs / Cursor invoices? Keep
   local/ollama at `$0` provider + resource estimate?

3. **Local resource assumptions** — Constants:
   - `LOCAL_RAM_GB_HOUR_USD = 0.01`
   - `LOCAL_KWH_USD = 0.15` (SA-ish residential)
   - `LOCAL_ASSUMED_RAM_GB = 24`
   - Watts: 27B≈150W, 30B≈180W, default≈140W  
   Confirm or replace with measured draw for your Mac / GPU.

4. **Settings persistence** — `cost_ledger_enabled` (default **True**) and
   `cost_invoice_display` (`blended`|`explicit`, default blended) live in
   `DEFAULT_SETTINGS` + env overrides (`DECISIONS_COST_LEDGER_ENABLED`,
   `DECISIONS_COST_INVOICE_DISPLAY`). They are **not** Settings ORM columns yet, so
   DB save will not persist them until a migration adds columns. OK for now?

5. **Budget breaker integration** — Power budget still uses
   `blueprint_adherence._estimate_token_cost` (in-memory). Should the breaker read
   ledger rollups as the durable source? (ACCEPTANCE §7 checkbox still open.)

6. **Deliverable linking** — `link_deliverable(run_id, ref)` +
   `cost_ledger_deliverables` table exist. Which artifact IDs / paths should auto-link
   (StudioArtifact.uri, step output paths, git commit SHAs)?

7. **Currency / tax / markup** — Ledger is raw USD estimates. Invoice markup,
   ZAR conversion, or VAT later?

8. **Harness vs workflow source tags** — StepExecutor hook records `source="workflow"`.
   Confirm when harness workers should tag `harness` vs `execution` / `chat`.
