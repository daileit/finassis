# finassis api

Python 3.12 backend: REST API (`/api/v1`), MCP server (Phase 2), Telegram channel, worker jobs. Design docs live in `../docs/tech/`; the Phase 0 plan is `../docs/tech/09-phase0-plan.md`.

## Run everything

```bash
cp .env.example .env           # set FINASSIS_TELEGRAM_BOT_TOKEN if you have a bot
docker compose up --build      # postgres, redis, api, worker
```

First start prints the bootstrap admin key once:

```
BOOTSTRAP: admin API key = fa_...
```

Then, with `A=fa_...`:

```bash
# create a user and mint their key (or let them /start the Telegram bot)
curl -s -X POST localhost:8000/api/v1/admin/users -H "Authorization: Bearer $A" -H 'content-type: application/json' \
  -d '{"locale":"vi","default_currency":"VND","display_name":"Dai"}'
curl -s -X POST localhost:8000/api/v1/admin/users/<user_id>/keys -H "Authorization: Bearer $A" -H 'content-type: application/json' \
  -d '{"name":"cli","scopes":["ledger:read","ledger:write","accounts:write","tags:write","reports:read","annotations:write","interactions:write","keys:manage"]}'

# as the user, with K=fk_...
curl -s -X POST localhost:8000/api/v1/accounts -H "Authorization: Bearer $K" -H 'content-type: application/json' \
  -d '{"name":"TCB main","type":"bank","currency":"VND","aliases":["tcb","1234"]}'
curl -s -X POST localhost:8000/api/v1/accounts -H "Authorization: Bearer $K" -H 'content-type: application/json' \
  -d '{"name":"Cash","type":"cash","currency":"VND"}'
# an expense (VND has 0 decimals: amount is in đồng)
curl -s -X POST localhost:8000/api/v1/transactions -H "Authorization: Bearer $K" -H 'content-type: application/json' \
  -d '{"account":"tcb","amount":-45000,"occurred_at":"2026-10-01","description":"Highlands","tag":"coffee_drinks"}'
# both sides of a self-transfer (one call, two legs) — excluded from spend/income
curl -s -X POST localhost:8000/api/v1/transactions -H "Authorization: Bearer $K" -H 'content-type: application/json' \
  -d '{"account":"tcb","amount":-2000000,"occurred_at":"2026-10-01","counter_account":"Cash"}'
curl -s localhost:8000/api/v1/balances -H "Authorization: Bearer $K"
curl -s "localhost:8000/api/v1/reports/spend?period=month" -H "Authorization: Bearer $K"
```

OpenAPI: `http://localhost:8000/api/v1/docs`. Metrics: `http://localhost:8000/metrics` (Prometheus/Grafana: see `../docs/tech/06-operations.md`).

## Develop

```bash
cd api && uv venv && . .venv/bin/activate && uv pip install -e '.[dev]'
make -C .. db-up                       # postgres + redis
FINASSIS_ENV=dev finassis api          # migrate → seed → bootstrap → serve on :8000
pytest tests/unit                      # no database needed
FINASSIS_DATABASE_URL=postgresql://finassis:finassis@localhost:5432/finassis pytest -m integration
```

CLI: `finassis api | worker | migrate | seed | bootstrap | partitions`.

## Layout

See `../docs/tech/09-phase0-plan.md` → *Module map*. Rules of the codebase:

- Domain functions take an `asyncpg` connection that is already tenant-scoped (`db.tenant_tx(user_id)`) or admin (`db.admin_tx()`); they never open connections themselves.
- REST, MCP and Telegram are thin adapters over the same domain functions. The Telegram channel goes through `client/port.py` so it can be split out later.
- Money is `Decimal` internally and `{amount, currency, decimals, display}` on the wire (`money.py`).
- Postings are append-only; the only mutation allowed is re-tagging (classification), enforced by a trigger.
