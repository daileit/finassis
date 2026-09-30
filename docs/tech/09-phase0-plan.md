# Phase 0 implementation plan

Goal (from the roadmap): register via the bot, mint an API key, post transactions via API (including both sides of a two-bank transfer, which leave spend/income unchanged), read balances and simple reports; metering and metrics on; runnable with one `docker compose up`.

## Scope

**In**

- `api/` Python 3.12 package `finassis` with three entrypoints: `finassis api`, `finassis worker`, `finassis migrate` (also `seed`, `bootstrap`).
- Startup sequence (tech/00): config → Alembic `upgrade head` (migration 0001 applies `api/db/schema.sql`) → seed loader (`seeds/*.json`) → bootstrap admin → `ensure_partitions()` → serve.
- Auth: Bearer API keys (`user` / `admin` kinds) with scopes; per-request DB transaction with `SET LOCAL app.user_id`; admin connection for privileged operations (user creation, bootstrap).
- REST `/api/v1`: `health`, `me`, `units`, `tags` (list combined, create custom, patch prefs, suggest), `accounts` (+ aliases, system accounts on demand), `transactions` (simple + explicit forms, list, get, reverse), `balances`, `reports/spend`, `reports/income`, `annotations`, `interactions` (list/resolve/dismiss), `keys`, `identities/link-codes`, `admin/users`, `admin/grants`, `metrics`.
- Ledger write path: validate → expand simple form to two legs → resolve tag (key / custom name / id; `409 unknown_tag` with trigram suggestions) → insert transaction + postings → incremental `period_rollups` upsert → usage event, all in one DB transaction. Light self-transfer pair detection after commit.
- Money envelope `{amount, currency, decimals, display}` on every monetary field.
- Metering middleware (`api.request`, `raw.item` reserved) → `usage_events`; Redis counters for allowance checks; default `free` plan seeded; `429 allowance_exhausted`.
- Telegram channel inside the API: webhook endpoint, `/start`, `/link`, `/keys`, `/lang`, `/help`, through the `FinassisClient` port (in-process implementation).
- Worker: rollup re-close from `dirty_periods`, daily balance + net-worth snapshots, partition maintenance, interaction expiry. Simple asyncio scheduler; Redis Streams consumer scaffolding.
- Prometheus `/metrics`; structured JSON logs.
- Dockerfile (multi-stage, `uv`), compose with postgres/redis/api/worker (Prometheus/Grafana deferred; snippet in tech/06).

**Out (Phase 1+)**: `/raw`, recipes, tagging cascade beyond caller-supplied, embeddings, income streams/projections, holdings/prices/valuations, budgets, webhooks delivery, console, MCP server (the port exists; the MCP adapter comes with Phase 2).

## Module map

```
api/
├── Dockerfile · pyproject.toml · alembic.ini · README.md
├── alembic/ env.py · versions/0001_init.py (executes db/schema.sql)
├── db/ schema.sql · smoke.sql
├── src/finassis/
│   ├── __main__.py            CLI (typer-free argparse): api | worker | migrate | seed | bootstrap
│   ├── config.py              pydantic-settings; env + optional config.yaml
│   ├── logging.py             structlog JSON
│   ├── errors.py              AppError hierarchy → error envelope
│   ├── db.py                  asyncpg pools (app/admin), tenant_tx(), admin_tx()
│   ├── i18n.py                catalogue loader (generated + hand-written), t(locale, key, **params) with fallback
│   ├── money.py               Money envelope, units cache, formatting
│   ├── domain/
│   │   ├── units.py · tags.py · accounts.py · ledger.py · balances.py · reports.py
│   │   ├── annotations.py · interactions.py · identities.py · keys.py · users.py
│   │   ├── metering.py · plans.py · seeds.py · bootstrap.py
│   ├── api/
│   │   ├── app.py             FastAPI factory, lifespan (startup sequence), middleware
│   │   ├── deps.py            auth → Principal; tenant session dependency
│   │   ├── schemas.py         Pydantic request/response models
│   │   └── routers/           health · me · units · tags · accounts · transactions · balances · reports
│   │                          · annotations · interactions · keys · identities · admin · metrics
│   ├── client/
│   │   ├── port.py            FinassisClient Protocol
│   │   └── inprocess.py       InProcessClient (calls domain services with a tenant tx)
│   ├── channels/telegram/
│   │   ├── router.py          POST /channels/telegram/webhook (secret verified)
│   │   ├── handlers.py        /start /link /keys /lang /help; callback + free-text stubs
│   │   └── tg.py              minimal Bot API HTTP client (sendMessage, inline keyboards)
│   └── jobs/
│       ├── scheduler.py       asyncio cron-ish loop
│       ├── rollups.py         re-close dirty periods
│       ├── snapshots.py       balance + net-worth snapshots
│       └── maintenance.py     ensure_partitions, interaction expiry, retention stub
└── tests/
    ├── unit/                  money, tx expansion, tag resolution, i18n, config
    └── integration/           requires DATABASE_URL; RLS, ledger write, balances, rollups
```

## Data access approach

Raw SQL via **asyncpg** with small typed helpers; no ORM. The schema is hand-written and RLS depends on `SET LOCAL app.user_id` inside a transaction, which is simplest to guarantee with explicit `async with tenant_tx(user_id) as conn:`. Two pools: `app` (role `finassis_app`, RLS applies) and `admin` (role `finassis_admin`, for user creation, bootstrap, jobs iterating tenants). In dev the compose Postgres superuser is used for both with roles created by `schema.sql`; `DATABASE_URL_APP` / `DATABASE_URL_ADMIN` override in prod.

## Order of work

1. Scaffold: pyproject, config, logging, db, Alembic 0001, CLI, health endpoint. *Runnable, empty.*
2. Seeds loader + bootstrap admin (prints key once) + units/money envelope + i18n loader.
3. Auth (keys, scopes, principal), tenant tx dependency, metering middleware, `/me`, `/units`, `/metrics`.
4. Tags (combined list, custom create, prefs, resolve + suggest), accounts (+ aliases, system accounts).
5. Ledger: transactions write (simple/explicit), postings, incremental rollups, pair detection; list/get/reverse; balances; spend/income reports.
6. Annotations, interactions, identities/link codes, keys endpoints, admin endpoints.
7. Telegram channel + FinassisClient port.
8. Worker jobs; Dockerfile; compose.
9. Tests; `make api-test`; docs sync.

## Status (2026-10-01)

Implemented: everything under *In* above. Verified in this environment: Python compile of all modules, 13 unit tests (money envelope, i18n rendering and fallbacks, occurred_at parsing, request schemas, key/scope logic, app construction, OpenAPI with 37 paths, `/metrics`, Telegram-disabled 404), `schema.sql` parse with the Postgres parser, seeds and i18n checks. **Not yet executed** (no Postgres/Docker in the authoring sandbox): the Alembic migration, seed loader, bootstrap, the integration tests in `api/tests/integration/`, `api/db/smoke.sql`, and the Docker build. Run `make up` then `make api-itest` and `make db-check`; expect small fixes (SQL typing, RLS edge cases), not design changes.

## Definition of done

- `docker compose up` brings up postgres, redis, api, worker; `GET /api/v1/health` returns ok; logs show the bootstrap admin key once.
- With that key: create a user via admin API (or via Telegram `/start` with a configured bot), mint a user key, create two bank accounts, post an expense, post both sides of a transfer tagged `off_report.self_transfer`, read `/balances` and `/reports/spend` — spend equals the expense only.
- `make check` passes; `make api-test` unit tests pass without a database; integration tests pass against `make db-up`.
