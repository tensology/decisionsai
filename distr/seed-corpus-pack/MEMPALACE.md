# MemPalace ingest (Decisions-bundled seed pack)

This pack ships inside DecisionsAI at `distr/seed-corpus-pack/`.

| Field | Value |
|-------|-------|
| Wing | `seed_corpus_pack` |
| Expected drawers (reference mine) | ~7916 |
| Decisions palace | `~/.decisions/mempalace/palace` |
| First-run | `distr.core.mempalace.seed.ensure_seed_wing()` runs `mempalace init <pack> --yes --auto-mine --no-llm --palace <decisions-palace>` |

Offline once chromadb + mempalace are importable. No LLM required (`--no-llm`).

Do not dump the Chroma DB into git — first-run mines into the Decisions-owned palace path.
