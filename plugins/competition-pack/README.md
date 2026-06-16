# DecisionsAI Competition Pack

Vendored harness skills and rules from the sibling reference clones (same layout as `reference/ecc`, `reference/rtk`):

- **Ponytail** — lazy senior dev discipline (YAGNI, stdlib-first, minimal diffs)
- **Fallow** — deterministic JS/TS codebase intelligence (`fallow audit`, dead code, dupes, health)

DecisionsAI projects this pack into Codex, Cursor, Claude, and Pi the same way as the ECC harness pack. Refresh from upstream checkouts with:

```bash
cd ../reference/ponytail && git pull
cd ../reference/fallow && git pull
cd ../../DecisionsAI && python3 scripts/sync_competition_pack.py
```

Reference clones: `../reference/ponytail` and `../reference/fallow`
