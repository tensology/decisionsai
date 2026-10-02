# seed-corpus-pack

Bootable seed corpus for Jupiter/Decisions agents.

**Location:** `distr/seed-corpus-pack/` inside DecisionsAI (vendored; ships via `installer/build_app.sh` rsync of `distr/`).  
Mined on first run into `~/.decisions/mempalace/palace` (wing `seed_corpus_pack`) when MemPalace is enabled.

## Quick start

1. Read `LOADING.md`
2. Load `profiles/ecommerce.json` (or `trading-robot.json`)
3. Follow `load_order` — includes behavioral layers and quality gate

## Layout

```
seed-corpus-pack/
  README.md  LOADING.md  manifest.json  SOURCES.md
  profiles/                 # per-domain boot profiles
  01-entity-domain/         # graph + MODEL-INFERENCE
  02-ui-conventions/
  03-done-states-frac/
  04-verify-feedback-loops/
  05-pdf-backend-frontend/
  06-harness-ops/
  07-agent-behavior/        # vagueness dial + get-shit-done
  08-premium-quality-gate/  # NON-NEGOTIABLE styling gate
  09-page-stencils/         # funnel/dashboard/CRUD/portal/admin
  domains/                  # Merrypak + currency-trader extracts
  vendor/                   # ecommerce-foundations, open-dashboard
  external/Vision2Web/
  copied-skills/
  pack-state/
```

## Behavioral layers (discoverable via manifest.behavioral_layers)

| Layer | Path |
|-------|------|
| Vagueness dial | `07-agent-behavior/VAGUENESS-DIAL.md` |
| Get-shit-done | `07-agent-behavior/GET-SHIT-DONE.md` |
| Premium quality gate | `08-premium-quality-gate/QUALITY-GATE.md` |
| Page stencils | `09-page-stencils/` |
| Model inference | `01-entity-domain/MODEL-INFERENCE.md` |

## Coverage honesty

Seed, not full product brain: curated Merrypak docs (not whole repo), Vision2Web parquet only (no media archives), LN-* pipeline indexed live not dumped.
