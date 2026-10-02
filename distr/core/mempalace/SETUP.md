# MemPalace setup map — complete local Decisions install

Updated: 2026-10-02 ~20:10 SAST  
Scope: local DecisionsAI only. Flag `mempalace_memory_backend` default **ON**.

## 1. Install (one-time, local venv)

```bash
# From DecisionsAI repo
~/.virtualenvs/decisions/bin/pip install -e ../reference/mempalace
# Pulls free chromadb (no paid API). First embed downloads ONNX MiniLM ~79MB to ~/.cache/chroma/

# Or one-shot: deps + mine bundled seed pack
~/.virtualenvs/decisions/bin/python python -m distr.core.mempalace
```

Optional path bootstrap without pip: `distr.core.mempalace.bootstrap.ensure_mempalace_on_path()` adds the reference clone to `sys.path` (still needs chromadb importable). Soft-fails with a clear setup message when deps are missing.

Palace directory (created on first write / first-run mine):

```
~/.decisions/mempalace/palace/     # default (DECISIONS_MEMPALACE_PALACE_PATH overrides)
```

Disable for a session:

```bash
export DECISIONS_MEMPALACE_MEMORY_BACKEND=0
# or settings key mempalace_memory_backend: false
```

## 2. Bundled seed corpus pack

```
distr/seed-corpus-pack/     # vendored ~9–12MB curated pack (ships via build_app.sh rsync of distr/)
  mempalace.yaml            # wing: seed_corpus_pack
  manifest.json
  …
```

**Packaging approach:** vendored copy under `distr/seed-corpus-pack` (not a download).  
`installer/build_app.sh` already rsyncs all of `distr/` into the app bundle, so the pack ships with Decisions.

**First-run (default ON):** Application schedules `_maybe_ensure_mempalace_seed` (~2.5s after start) → `ensure_seed_wing(background=True)` →

```bash
python -m mempalace init <bundled-pack> --yes --auto-mine --no-llm --palace ~/.decisions/mempalace/palace
```

Marker: `~/.decisions/mempalace/.seed_corpus_pack_mined.json`  
Log: `~/.decisions/mempalace/seed-corpus-mine.log`  

Chroma DB is **not** committed to git — mine on first run (offline once deps + ONNX model cache exist).

## 3. Package layout

```
distr/core/mempalace/
  flags.py      # FLAG_KEY, env override, DEFAULT_ENABLED=True
  paths.py      # ~/.decisions/mempalace/palace + bundled_seed_corpus_pack()
  bootstrap.py  # reference clone on sys.path
  mapping.py    # wing=decisions + wing=seed_corpus_pack + rooms
  adapter.py    # search / add_drawer / format_search_as_context
  wiring.py     # dual_write_* / prefer_read_* (seed wing preferred for RAG)
  seed.py       # first-run mine of bundled pack
distr/seed-corpus-pack/   # vendored corpus
python -m distr.core.mempalace
```

Wings: `decisions` (runtime dual-writes); `seed_corpus_pack` (bundled curated knowledge, default RAG preference).

## 4. End-to-end surface map

```
                    ┌─────────────────────────────────────────┐
                    │  Feature flag mempalace_memory_backend  │
                    │  ON  = dual-write + prefer-read (default)│
                    │  OFF = legacy only                      │
                    └──────────────────┬──────────────────────┘
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         ▼                             ▼                             ▼
   Agent chat                    Development                    CLI / IDE
   (RAG prefers seed wing)       (dual-write + FS pickup)       (FS authoritative)
                                       │
                                       ▼
                         ┌─────────────────────────┐
                         │ MemPalace + chromadb    │
                         │ ~/.decisions/mempalace/ │
                         │ wings: decisions +      │
                         │        seed_corpus_pack │
                         └─────────────────────────┘
```

## 5. Rollback

```bash
export DECISIONS_MEMPALACE_MEMORY_BACKEND=0   # or settings false
rm -rf ~/.decisions/mempalace/               # optional; legacy stores untouched
# pip uninstall mempalace chromadb           # optional
```

## 6. Related docs

- `.decisions-mempalace-flags.md` — locked decisions, live-test commands  
- `distr/seed-corpus-pack/MEMPALACE.md` — pack ingest notes  
- `python -m distr.core.mempalace` — local deps + mine helper  

**PROD / go-live:** default-ON flag changes local behavior. Do not promote to production release channels without an explicit go-live decision (unsigned local builds only today).
