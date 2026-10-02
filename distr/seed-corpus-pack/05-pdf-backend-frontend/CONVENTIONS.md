# PDF / BE / FE conventions — SYNTHESIZED

## Frontend
- Default greenfield: React + Vite + TypeScript + Tailwind (Merrypak-like) OR open-dashboard for admin-first apps.
- Structure: `src/pages`, `src/components/{ui,feature}`, `src/hooks`, `src/contexts`.
- Data access behind repository/API client seam.
- Theme tokens in one config file.

## Backend
- Prefer Clean Architecture / layered apps (ln-722 .NET *or* Django apps as in Merrypak *or* open-dashboard presets).
- Wire contract for list/detail CRUD (see open-dashboard `backends/CONTRACT.md`).
- Idempotency on place-order and payment webhooks (ecommerce-foundations).

## PDF
- Use DecisionsAI `pdf` skill (pypdf/pdfplumber/reportlab patterns in SKILL.md).
- Invoices/packing slips: snapshot Order + addresses; never live-mutate prices when rendering historical PDFs.

## Cross-cutting
- `start`/`stop`/`restart` scripts at repo root (Merrypak/auctionnow habit).
- `AGENTS.md` + `CLAUDE.md` entrypoints.
