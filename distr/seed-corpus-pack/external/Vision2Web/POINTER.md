# Vision2Web — pointer & local subset

- **Dataset:** https://huggingface.co/datasets/zai-org/Vision2Web
- **License:** Apache-2.0
- **Size category:** n&lt;1K tasks (parquet splits are small; media archives may be large)

## Locally fetched (this pack)

| Path | Status |
|------|--------|
| `webpage/test.parquet` | downloaded |
| `website/test.parquet` | downloaded |
| `frontend/test.parquet` | retry if missing |
| `README.md` | fetch from HF if missing |
| `archives/*.tar.gz` | **NOT downloaded** (avoid large media) |

## Fetch more (local only)

```bash
cd /Users/paul/development/TENSOLOGY/DECISIONS/seed-corpus-pack/external/Vision2Web
huggingface-cli download zai-org/Vision2Web --repo-type dataset \
  --include "README.md" --include "frontend/test.parquet" \
  --local-dir .
# Only if you need full screenshots / HTML archives:
# huggingface-cli download zai-org/Vision2Web --repo-type dataset \
#   --include "archives/*" --local-dir .
```

## Agent use

Treat parquet rows as **task schemas / PRD-like vision-to-web acceptance samples**, not as production UI assets.
Use alongside `02-ui-conventions/` and Vision2Web SCHEMA-SAMPLE when available.
