# Roadmap

Each phase is shippable and used for real (by us) before the next starts. Scope is deliberately small: one instance, one Postgres, one Redis.

## Phase 0 — Ledger + structured API

- Users, API keys with scopes; `user_id` on every table (RLS switched on but simple).
- `units` table (measures: money, mass, count, area; seeded), accounts, tags, transactions, postings, FX rates. USD + VND with per-unit precision.
- `POST /transactions`, account/tag CRUD, `GET /balances`.
- Period rollups (incremental + nightly re-close) and daily balance/net-worth snapshots.
- Annotations table and endpoints.
- `interactions` and `identities` models with REST endpoints; system accounts (receivable, payable) on demand.
- Bootstrap admin from config (key generated and printed once if absent). **Minimal Telegram slice**: `/start`, `/link`, `/keys`, `/lang` — enough to register and mint an API key without a console.
- Usage metering middleware, `usage_events`/rollups, default free plan, `/metrics` (Prometheus/Grafana containers when needed; tech/06).

Exit: register via the bot, mint a key, post transactions via API (incl. both sides of a two-bank transfer, which leave spend/income unchanged); `/metrics` and `/health` answer.

## Phase 1 — Ingestion recipes

- `raw_events` store; input-agnostic `/raw` endpoint; fingerprinting.
- Recipe DSL, interpreter, schema test harness.
- `/recipes/propose` (LLM compiles a recipe from a sample), recipe CRUD, fixtures.
- Review queue for recipe misses.
- Recipes for 3–5 major VN bank email/SMS templates.
- Tagging cascade without LLM: rules, fingerprint cache, merchant memory, keyword dictionary; tag suggestions (trigram + tag-name embeddings); untagged triage in API.
- kNN over confirmed examples (pgvector + small multilingual embedding model in the worker).

- First connector as an n8n template: email body → `/raw`.
- **Telegram channel, full**: forward text → `/raw` → transaction card, tag confirmation via inline keyboard, `/balance` `/spend` `/untagged` `/review` `/inbox`, `/currency` `/tz`, pushes from the worker.

Exit: forward a bank SMS to the bot, transaction appears with a tag; fix the tag with one tap; never opened a browser.

## Phase 2 — Agent surface

- MCP server mirroring REST: balances, net worth, spend/income queries, transactions, annotations, record_*, `list_interactions` / `resolve_interaction`.
- `get_context_summary`, `project_cashflow`.
- Webhooks / SSE for `posting.committed`, `review.needed`, `alert.raised`.

Exit: Claude with the MCP attached answers "how much cash at Tết" correctly.

## Phase 3 — Earning & wealth depth

- IncomeStreams with projection/reconciliation.
- Mark-to-market accounts, Valuations with staleness; instruments with measure/unit, prices per unit (SJC gold per lượng, VN stocks per share), manual and feed sources.
- Holdings/lots per unit, realised & unrealised gains; user-defined units.
- Net-worth time series and breakdowns.

## Phase 4 — Assistant behaviours & operations

- Anomaly and staleness alerts.
- Recurring detection.
- Narrative summaries in personality voice.
- Community recipe library (opt-in sharing of bank templates).
- Admin API, grants, donation webhook → allowance top-up.
- Nightly LLM tag batch (allowance-gated) and global merchant memory.

## Phase 5 — Console (`console/`, separate app in this repo)

- Browser auth (OIDC + cookie session), `/me`, self-service endpoints, OpenAPI client generation in CI.
- Next.js console: overview, ledger, wealth, recipes, review queue, integrations, usage/allowance, settings; operator views. See [docs/ui](../ui/00-overview.md).

## Later

- Households / shared ledgers.
- Broker/exchange sync adapters.
- Receipt image OCR → recipe.
- Goals and what-if support data.
- Paid: unit conversion of quantity history and combined single-unit snapshots.
- Transfer pairing: link two independently received halves via a `transit` clearing account (ADR-027).
