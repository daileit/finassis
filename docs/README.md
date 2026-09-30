# Finassis documentation

Repo layout: `api/` (Python backend: REST, MCP, Telegram channel, worker), `console/` (Next.js UI), `connectors/` (n8n templates, scripts), `i18n/` (shared catalogues), `docs/` (this). `api` and `console` have their own Dockerfiles; `docker-compose.yml` at the root runs everything. See [tech/00-architecture.md](tech/00-architecture.md#repository-layout).

## Product — what and why

- [00-vision.md](product/00-vision.md) — the financial memory an AI agent reads from; Vietnam first; principles, non-goals
- [01-features.md](product/01-features.md) — expense, earning, wealth (units, per-unit aggregation), recipes, agent-ready data, Telegram, plans, multi-currency
- [02-roadmap.md](product/02-roadmap.md) — phased delivery plan (ledger → recipes + Telegram → MCP → wealth depth → ops → console)
- [03-tags.md](product/03-tags.md) — tag taxonomy, tagging cascade, confirmed memory, suggestions, translation

## Tech — how

- [00-architecture.md](tech/00-architecture.md) — stack, repo layout, components, request paths, tenancy, auth (user/admin/channel keys), events
- [01-data-model.md](tech/01-data-model.md) — units, ledger, tags & tagging memory, income, wealth (instruments, prices, lots), FX, ingestion & recipes, interactions, metering
- [02-ledger-and-snapshots.md](tech/02-ledger-and-snapshots.md) — append-only ledger, rollups, snapshots, re-close
- [03-ingestion-pipeline.md](tech/03-ingestion-pipeline.md) — `/raw` → fingerprint → recipe interpreter (DSL) → reconcile → tagging → structured write; connectors
- [04-ai-layers.md](tech/04-ai-layers.md) — AI compiles recipes and batches tags (offline); data designed for the user's AI to consume (MCP/REST/webhooks)
- [05-decisions.md](tech/05-decisions.md) — decision log (ADR-001…024) and open questions
- [06-operations.md](tech/06-operations.md) — config, metrics, usage metering, plans & allowance, admin API, jobs, retention
- [07-interactions-and-channels.md](tech/07-interactions-and-channels.md) — pending-question model; Telegram channel in-process now via a client port, separable later; identities

## UI — the console (`console/`, separate app in this repo)

- [00-overview.md](ui/00-overview.md) — why same repo but separate app; audiences; principles
- [01-features.md](ui/01-features.md) — screens and the endpoints each depends on
- [02-architecture.md](ui/02-architecture.md) — stack, structure, auth flow, data rules, build/deploy
- [03-api-contract.md](ui/03-api-contract.md) — checklist of what the console needs from the API

Conventions: numbered prefixes give reading order; add new decisions to the ADR log rather than editing old ones.
