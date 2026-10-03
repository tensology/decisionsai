# LOADING — how an agent boots this pack

## 1. Choose a profile

- `profiles/ecommerce.json` — default for e-commerce (~90% scaffold target)
- `profiles/trading-robot.json` — trading/FX (~70%)

## 2. Read `load_order` in order

Always includes early:

1. Vagueness dial + get-shit-done + Paul planning workflow (`07-agent-behavior/`)
2. For ecommerce: `MODEL-INFERENCE.md` then entity graph
3. Premium quality gate (`08-`) — **hard fail if UI skips it**
4. Relevant `09-page-stencils/*`
5. FRAC / verify / domain extracts / vendor skills

## 3. Runtime behavior

```
score vagueness → ask within budget → expand entities →
screens/sitemap → FRAC deltas → ERD binds → page V/M/logic links →
pick page stencil(s) → scaffold → premium gate → verify → done
```

## 4. Optional MemPalace

```bash
cd /Users/paul/development/TENSOLOGY/DECISIONS/reference/mempalace
uv run python -m mempalace wake-up --wing seed-corpus-pack
uv run python -m mempalace search "page stencil customer portal"
```

See `MEMPALACE.md` for ingest status.

## 5. Do not

- Edit `reference/`, Merrypak, or DecisionsAI sources from this workflow
- Claim UI done without `08-premium-quality-gate` checklist
- Skip model inference when the user names an entity
