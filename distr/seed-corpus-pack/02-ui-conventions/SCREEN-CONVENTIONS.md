# Screen / UI conventions — SYNTHESIZED

Provenance: Merrypak shop FE + open-dashboard PATTERNS + DecisionsAI frontend-design / design-references. Label: **SYNTHESIZED**.

## Surfaces

| Surface | Pattern | Primary refs |
|---------|---------|--------------|
| Admin list | CRUD table: server paginate/sort/search/filter + bulk | open-dashboard CRUD table |
| Admin detail | Show page `/$id` + edit/delete | open-dashboard Detail/Show |
| Admin master-detail | List + side panel selection in URL | open-dashboard Master-detail (orders) |
| Admin cards | Card/grid list | open-dashboard Card list |
| Admin forms | Shared dialog or full-page form | open-dashboard Form page/dialog |
| Admin charts | Dashboard datasets | open-dashboard Chart page |
| Customer catalog | Product list + detail (gallery, variants) | Merrypak `pages/shop` |
| Cart / checkout | Multi-step: address → shipping → payment → review | Merrypak `components/checkout` + ecommerce-foundations cart/order |
| Auth / account | Login, register, account history | Merrypak `pages/auth` |
| Customer portal | Order history, addresses, returns | Merrypak shop + entity graph |

## Shared invariants (admin)

From open-dashboard (copied ideas, see vendor for exact code):

1. List params one shape: `_page`, `_limit`, `_sort`, `_order`, `q`, filters.
2. Repository seam: `list/getOne/create/update/remove` — swap memory/drizzle/REST without rewriting pages.
3. Mutations toast success/error; confirm destructive actions.
4. URL-synced table state.

## Design direction

- Commit to one aesthetic (enterprise clean for B2B shop/admin; avoid generic purple-gradient AI slop).
- Tokens via CSS variables / Tailwind theme (Merrypak: Arimo + sky primary).
- Accessibility: keyboardable tables/dialogs; meaningful labels.

## Vision→web tasks

Vision2Web parquet splits = acceptance-style tasks (screenshot/spec → implement). Use for evals, not as production assets. Archives not in pack by default.
