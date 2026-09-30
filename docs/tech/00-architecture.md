# Architecture

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| Language / API | Python 3.12+, FastAPI, Pydantic v2 | Fast to iterate, first-class async, strong typing for financial payloads, good LLM SDK ecosystem |
| Database | PostgreSQL 16 | `NUMERIC` for money, JSONB for raw events, partitioning for the ledger, mature and boring |
| Cache / queue / events | Redis 7 | Cache for hot aggregates, Streams for async pipeline, pub/sub for webhooks. One dependency, three jobs |
| Workers | arq (or Celery w/ Redis broker) | Async-native; runs ingestion, rollups, projections, alerts |
| Scheduler | Postgres-backed cron table + worker tick (or APScheduler) | Avoid a separate scheduler service early |
| Migrations | Alembic | |
| Vectors / fuzzy | `pgvector`, `pg_trgm`; small multilingual sentence-embedding model (384-dim, CPU) in the worker | Tag kNN and tag-name suggestions without an LLM |
| Regex engine | `google-re2` | Linear-time matching for recipe steps; no catastrophic backtracking from LLM-written patterns |
| AI | Provider-agnostic client (Anthropic first) behind an internal `ai/` package | Used only by the recipe compiler; swap models without touching domain code |

**Deployment target is one instance**: one Postgres, one Redis, one API process, one worker. The design leaves room to scale (stateless API, stream consumer groups, partitioned ledger) but is optimised to be cheap and efficient at N=1. Data efficiency is a day-one concern, not a later one.

## Repository layout

```
finassis/
├── api/                  Python backend: FastAPI app, worker, MCP server, migrations
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic/
│   └── src/finassis/
│       ├── api/          REST routers, auth, quota middleware
│       ├── mcp/          MCP server (thin adapter over domain)
│       ├── channels/     telegram/ (webhook + push renderer), later zalo/, email_digest/
│       ├── domain/       ledger, accounts, tags, reporting, recipes, interactions, metering …
│       ├── ingest/       fingerprint, DSL interpreter, reconcile, tagging cascade
│       ├── compiler/     LLM recipe compiler, nightly tag batch
│       ├── jobs/         rollups, snapshots, projections, retention, pushes
│       ├── i18n/         catalogue loader for server-rendered text (channels, narration)
│       └── db/           models, RLS helpers
├── console/              Next.js UI — separate app, own Dockerfile (see docs/ui/)
├── connectors/           n8n templates and small scripts that end in POST /raw
├── docs/                 product/ · tech/ · ui/
├── docker-compose.yml    api, worker, console, postgres, redis, grafana, prometheus
├── Makefile              dev shortcuts: up, api, console, contract (export openapi → regen client), test
└── README.md
```

REST, MCP and the Telegram channel are three thin adapters in one process over one domain layer (ADR-008, ADR-024). `api` and `console` are built and deployed independently; the OpenAPI spec exported from `api` is the contract, and `console`'s generated client is regenerated in CI so drift fails the build.

## Component view

```
┌──────────────────────────────────────────────────────────────────┐
│ Clients: Telegram users · console (console/) · n8n · MCP agents  │
│ Connectors (outside core): email · SMS relay · CSV               │
│   → all end in POST /raw {text, source?}                         │
└──────┬───────────────────┬───────────────────┬───────────────────┘
       │ Telegram webhook  │ REST (+ webhooks) │ MCP
┌──────▼───────────────────▼───────────────────▼───────────────────┐
│ Adapters (one FastAPI process): channels/telegram · api · mcp    │
│  auth/identity · rate-limit · tenant scoping · validation · i18n │
├──────────────────────────────────────────────────────────────────┤
│ Domain services                                                  │
│  ledger · accounts · tags · budgets · income streams ·           │
│  interactions · identities ·                                     │
│  valuations · holdings · reporting · alerts · nl-query           │
├──────────────────────────────────────────────────────────────────┤
│ Ingestion pre-processor (async, Redis Streams)                   │
│  raw_event → fingerprint → recipe interpreter (DSL, no LLM) →    │
│  schema check → reconcile → structured write | review queue      │
│  recipe compiler (LLM, user-triggered, offline)                  │
├──────────────────────────────────────────────────────────────────┤
│ Aggregation jobs                                                 │
│  period rollups · balance snapshots · net-worth snapshots ·      │
│  income projection · FX/price refresh · anomaly scan             │
├────────────────────────┬─────────────────────────────────────────┤
│ PostgreSQL             │ Redis                                   │
│  ledger (partitioned)  │  streams: ingest, events                │
│  rollups, snapshots    │  cache: balances, rollups               │
│  raw_events (JSONB)    │  pub/sub: webhook fan-out               │
└────────────────────────┴─────────────────────────────────────────┘
```

## Request paths

**Structured write** — `POST /transactions` → validate → domain service writes transaction + postings in one DB transaction → emits `posting.committed` to Redis Stream → returns 201. Rollup job consumes the event and updates the affected period buckets incrementally.

**Raw write** — `POST /raw {text, source?}` → store `raw_event` → enqueue → worker fingerprints, finds a recipe, runs it (pure Python, ms) → output validated against `transaction.v1` → reconcile → commit via the same domain service as the structured path. No recipe or validation failure → `review_item` + `review.needed`. The LLM is never called here.

**Recipe compile** — `POST /recipes/propose {raw_event_id}` → LLM produces a DSL recipe → validated, run against the sample, fixture stored → recipe saved. User-triggered, rate-limited, async.

**Read** — `GET /reports/net-worth?at=2026-09-30&currency=USD` → reporting service reads latest snapshot ≤ date + rollups since → per-currency totals converted with the FX rate for that date → response includes `as_of`, staleness per mark-to-market item, rates used, and relevant annotations → cached in Redis (short TTL, invalidated by `posting.committed` for that tenant).

**Meta** — `GET /meta/enums` lists every enum value the API can return (for console translation coverage); `GET /units?measure=` lists global + the user's units with decimals and factors (`/units?measure=money` is the currency list); global unit names are translated by the console. Money is always `{amount, currency, decimals, display}` (ADR-017).

**Agent context** — `GET /context-summary` (MCP `get_context_summary`) → assembled from snapshots, streams, alerts and recent annotations; budgeted to ~2k tokens.

## Multi-tenancy

- Every table carries `user_id` (or `tenant_id` if households are introduced later).
- Postgres **row-level security** enabled on all tenant tables; the API sets `SET LOCAL app.user_id` per request so a missed `WHERE` cannot leak data.
- Composite primary keys / indexes lead with `user_id` so partitioning and index locality follow tenants.

## Auth

- Users authenticate via OAuth2/OIDC (external IdP) or email+password.
- Machine access via **API keys** with scopes (`transactions:write`, `raw:write`, `recipes:propose`, `reports:read`, ...). This is what n8n, connectors and MCP clients use.
- MCP server authenticates the same way; each MCP session is bound to one user's key.
- Browser sessions (for the separate console) via OIDC → HttpOnly cookie; same `user_id` and scope model.
- `admin`-kind keys for the operator API; every admin action audited.
- Quota middleware runs after auth: resolves plan + grants, checks Redis usage counters, emits `usage_events` (see [06-operations.md](06-operations.md)).

## Events & integrations out

- Internal event bus: Redis Streams (`finassis:events`) with consumer groups per worker type.
- External: **webhooks** (per user, per event type, HMAC-signed) and an optional **SSE** endpoint. n8n subscribes to `posting.committed`, `alert.raised`, `review.needed`.

## Observability

- Structured JSON logs with `user_id`, `request_id`, `event_id`.
- OpenTelemetry traces across API → worker via event metadata.
- Metrics: ingest latency, recipe hit rate, recipe failure rate per version, review-queue depth, rollup lag, compiler calls and tokens per user.

## Deployment

Docker Compose everywhere to start: api, worker, console, postgres, redis, prometheus, grafana on one VM; `api` and `console` are separate images from separate Dockerfiles. Postgres tuned for a small box (shared_buffers, work_mem sized to the instance; `pg_stat_statements` on from day one). Backups: Postgres PITR or nightly dump to object storage; Redis is disposable (streams are replayable from `raw_events`). When growth demands it: move Postgres to a managed service, add workers, add API replicas — no code change required.
