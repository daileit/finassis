# Operations, metering and plans

Everything that costs money is metered from day one, using the same pattern as the ledger: an append-only event table plus rollups. Plans read the rollups. Monetisation later is a configuration change, not a redesign.

## Configuration

- **Env vars** for secrets and deployment-specific values (`DATABASE_URL`, `REDIS_URL`, `AI_API_KEY`, `JWT_SIGNING_KEY`, `PUBLIC_BASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `BOOTSTRAP_ADMIN_TELEGRAM_ID`, `BOOTSTRAP_ADMIN_API_KEY` — optional; generated and logged once if absent).
- **`config.yaml`** for behaviour: plan definitions, default limits, recipe interpreter timeboxes, snapshot cadence, FX/price feed settings, compiler model + prompt version, retention. Hot-reloadable where safe (plans, limits), restart otherwise.
- Config is validated by a Pydantic settings model at boot; the effective config (secrets redacted) is exposed at `GET /admin/config`.

## Metrics (`/metrics`, Prometheus format)

| Area | Metrics |
|------|---------|
| API | request count / latency by route and status; per-tenant request count (bounded cardinality: top-N + "other") |
| Ingestion | raw events in; recipe hit rate; recipe run duration; recipe failure by recipe version; review queue depth and age |
| Compiler | calls, tokens in/out, latency, outcome; spend estimate |
| Ledger | postings/min; rollup lag (oldest dirty period age); snapshot job duration |
| Storage | per-tenant bytes (raw_events, postings, total) measured nightly; DB size; partition count |
| Queue | stream length, pending count, consumer lag per group |
| Webhooks | deliveries, failures, retry depth |

Grafana + Prometheus in the same Compose file is the **operator console for Phase 0**. No custom UI needed for operating the stack.

## Usage metering

```
usage_events                             -- append-only, partitioned monthly
  id, user_id, kind, quantity NUMERIC, unit, ref_id (nullable), occurred_at
  kind ∈ api.request | raw.item | recipe.run | compiler.call | compiler.tokens |
         storage.bytes | webhook.delivery | mcp.call | export.run

usage_rollups                            -- per user × kind × day (and month)
  user_id, kind, period_kind, period_start, quantity, closed_at
  PK (user_id, kind, period_kind, period_start)
```

- Emitted by middleware (API/MCP calls), the worker (raw items, recipe runs, compiler calls with token counts, webhook deliveries) and a nightly job (storage bytes per tenant from partition/row stats).
- Rolled up incrementally like `period_rollups`; hot counters cached in Redis (`usage:{user}:{kind}:{day}`) so quota checks are O(1).

## Plans and allowance

```
plans
  id, name, limits jsonb, price_hint, is_default
  -- limits example:
  -- { "api.request": {"month": 20000}, "raw.item": {"month": 2000}, "compiler.call": {"month": 10},
  --   "storage.bytes": {"cap": 104857600}, "raw.text_bytes": {"max": 32768}, "retention_days": 730 }

user_plans
  user_id, plan_id, started_at, ended_at (nullable)

grants                                   -- top-ups: donation, promo, manual
  id, user_id, kind (donation | promo | admin | referral), allowance jsonb, expires_at, note, created_at
```

**Allowance** is the internal, neutral term for "how much of each metered kind the user can still use in the period" = plan limit + active grants − usage. The personality layer may present it as *mana* ("you're out of mana for recipe compiles this month"); the API and console use `allowance`.

**Quota check**: middleware resolves the user's effective limits (cached), reads the Redis counter, and either proceeds (and increments) or returns `429 allowance_exhausted` with `{kind, limit, used, resets_at, how_to_top_up}`. Ledger reads are never blocked by quota; only costly kinds (raw items, compiler calls, storage, high API volume) are.

**Monetisation outlet**: a donation/payment webhook (any provider) creates a `grant`. Paid plans are just `plans` rows with higher limits. Nothing else changes.

## Admin API (`admin` scope, separate key type)

- Users: list, view usage & allowance, set plan, add grant, pause/unpause tenant, export/delete tenant data.
- Recipes: list community recipes, disable a recipe version globally, view failure rates.
- Pipeline: replay a raw_event, requeue failed items, inspect stream lag.
- Ledger: trigger re-close for a tenant/period, trigger snapshot.
- Config: view effective config, reload hot-reloadable sections.
- Audit: every admin action is written to `admin_audit (actor, action, target, payload, at)`.

## Retention & efficiency levers

- `raw_events.text` older than plan `retention_days` → compressed to a blob store or dropped (metadata kept). Ledger is never dropped.
- Old `postings` partitions → detached/archived; rollups and snapshots stay.
- `usage_events` older than 13 months → dropped; `usage_rollups` kept.
- Per-tenant storage is a metered kind, so a runaway user hits allowance rather than filling the disk.

## Jobs schedule (defaults)

| Job | Cadence |
|-----|---------|
| Rollup re-close (dirty periods) | hourly |
| Balance & net-worth snapshots | daily 02:00 local |
| FX / price refresh | daily (configurable per feed) |
| Income projection materialise | daily |
| Storage measurement → usage | nightly |
| Staleness & anomaly alerts | daily |
| Recipe fixture regression | on publish + weekly |
| Retention sweep | weekly |

## Alerts (operator)

Rollup lag > 24h · review queue age > 7d · recipe version failure rate > 20% · compiler spend > daily cap · disk > 80% · queue pending > threshold · webhook failure rate > 10%.
