# Console — architecture

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| Framework | Next.js (App Router), TypeScript | SSR for auth-gated pages, static export possible, mature |
| UI | Tailwind CSS + shadcn/ui | Fast, consistent, accessible primitives; no design-system project needed |
| Data | TanStack Query + generated OpenAPI client (`openapi-typescript` / `orval`) | Typed calls, caching, retries; contract enforced at build |
| Charts | Recharts (or ECharts if stacked-currency series get complex) | |
| Forms | react-hook-form + zod (schemas derived from OpenAPI where possible) | |
| i18n | next-intl, `vi` + `en` | VN-first |
| Realtime | native `EventSource` against `/events/stream` | review queue and overview live updates |
| Tests | Vitest + Testing Library; Playwright for the review-queue and recipe-propose flows | |

## Structure

```
console/
├── Dockerfile               multi-stage: deps → build → node:alpine runtime (or static export → nginx)
├── package.json
├── next.config.ts
├── src/
│   ├── app/                 routes: (user)/..., admin/..., login
│   ├── components/          ui primitives + domain components (MoneyCell, StalenessBadge, FxNote)
│   ├── api/                 generated client + thin hooks (useNetWorth, useReviewItems...)
│   ├── lib/                 formatting (currency by `currencies.decimals`), dates, auth helpers
│   └── i18n/
└── tests/
```

Domain components worth naming up front because they encode the product principles: `MoneyCell` (currency-aware formatting, shows native + converted with rate on hover), `QuantityCell` (number + localized unit label, per-unit rows never summed client-side), `StalenessBadge` (from `as_of` + `expected_refresh_interval`), `ProvenanceLink` (raw event + recipe version), `AllowanceMeter`.

## Topology: same origin, browser calls the API directly

```
browser ──▶ reverse proxy (Caddy/nginx) ──┬── /api/*  /auth/*  /events/*  /openapi.json ──▶ FastAPI
                                          └── /*                                          ──▶ console (static export; node only if SSR is ever needed)
```

Next.js is a **build tool and asset server**, not a backend-for-frontend. The browser calls the Python API on the same origin, so there is no CORS, cookies just work, and the console exercises exactly the public API that n8n and MCP agents use. We deliberately do not route API calls through Next.js server code (no BFF): it would add a hop, a Node runtime dependency, and a place for logic to leak into that agents can't reach. The console starts as a static export; SSR can be switched on later without changing this topology.

## Auth flow

1. `/login` → `GET /auth/oidc/start` on the API; the API handles the provider callback and sets an HttpOnly, SameSite=Lax session cookie for the origin.
2. `GET /me` on load resolves user, locale, scopes (drives admin routes), plan and allowance.
3. CSRF: double-submit token from `/me` echoed in `X-CSRF-Token` on mutating requests.
4. No API keys in the browser, ever. Keys are shown once on creation and never stored client-side.

## Internationalisation

Vietnamese is the first-class locale; English is the default and the source language. Three layers, handled differently:

| Layer | What | Where translated | Mechanism |
|-------|------|------------------|-----------|
| UI strings | buttons, labels, errors, empty states | console | `next-intl` over the shared repo-level `i18n/{en,vi}.json` catalogues (also read by the API for Telegram/narration), keyed by stable ids (`review.reason.no_recipe`), ICU MessageFormat |
| Domain terms | enums the API returns: account types, tag kinds, review reasons, alert kinds, usage kinds, plan names | console | same catalogue, namespaced to mirror the enum: `enum.account_type.credit_card` → "Thẻ tín dụng". Backend stores and returns English identifiers only |
| Unit names | `luong`, `chi`, `oz_troy`, `share` … | console | global units are enums: `unit.luong` → "lượng" / "tael"; covered by the `/meta/enums` check. User-defined units display their own `name` from the API |
| User data | descriptions, merchant names, account names, annotations, recipe names | never | stored and shown verbatim |

Rules:

- English catalogue is the source of truth; `vi` translates it. CI fails on missing keys, and on any enum value from `GET /meta/enums` lacking an `enum.*` translation.
- Never concatenate translated fragments; use full templates with placeholders.
- **Formatting is not translation.** Numbers, dates and money use `Intl.*` with the user's locale and the money object's `decimals`: `250.000 ₫` (vi) vs `₫250,000` (en) are the same `amount`. Never `toFixed(2)`.
- System tags are translated from `system_key` (`tag.food.coffee_drinks`) with fallback locale → `en` → humanised key; a user's `display_name_override` wins over the catalogue; custom tags show their own `name`, never translated.
- Vietnamese strings run 20–30% longer than English; layouts must tolerate it. Test both locales on every screen.
- Narration from the personality layer is generated directly in the user's locale by the backend; it is not translated client-side.

**Display chains for mixed-unit quantities.** The API always returns one value in one unit (`{value: "23", unit: "chi"}`). The console may render it split across a per-measure, per-locale chain when the user has that preference on: `mass/vi: [luong, chi, phan]` → "2 lượng 3 chỉ"; `mass/en: [oz_troy]` or `[kg, g]`; `area/vi: [ha, sao_*, m2]`. Chains live in the console catalogue next to unit names; they never change what is sent back to the API, and they never merge different holding rows. Quantity input forms accept mixed parts and send `{parts: [...]}`; the backend normalises.

## Data fetching rules

- All reads go through the generated client; no hand-written fetch URLs.
- Money and dates are formatted only via `lib/format` using the currency's `decimals` from the API — never `toFixed(2)`.
- Every report response's `as_of`, `fx`, `staleness` and `annotations` fields are rendered, not dropped.
- Optimistic updates only for review-queue resolutions and annotations; everything ledger-related waits for the server.
- `429 allowance_exhausted` is handled globally: toast with `kind`, `resets_at` and top-up link.

## Build & deploy

- `console/Dockerfile` is independent of `api/Dockerfile`; `docker-compose.yml` at repo root runs both plus Postgres, Redis, Grafana.
- CI: `api` job exports `/openapi.json` as an artifact → `console` job regenerates the client and fails on type errors. Contract drift is caught before merge.
- Production: console as a static export behind the same reverse proxy as the API (same origin, see Topology). Environment: `NEXT_PUBLIC_API_BASE_PATH` (default `/api`).

## Non-goals

- No business logic in the console (no client-side re-aggregation of postings, no FX math). If a screen needs a number, add or extend a report endpoint.
- No offline mode.
- No native app; responsive web only.
