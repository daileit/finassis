# finassis
Finassis – A smart, AI-powered personal finance assistant that helps you track budgets, manage expenses, and optimize wealth with a touch of pop-culture personality

A ledger-first personal-finance **backend** built to be the financial memory an AI agent reads from. Expense, earning and wealth tracking over one append-only ledger; multi-user, multi-currency (USD + VND first), unit-aware (gold in lượng, land in m², stocks in shares) and single-instance-efficient from day one.

- Messy input (VN bank SMS/emails, CSVs) → `POST /raw` → **recipes** an LLM compiles once and plain code runs forever.
- Controlled **tags** with a cheapest-first tagging cascade (rules → memory → keywords → kNN → nightly LLM batch), confirmed by the user.
- **Telegram bot** as the first UI: register, forward a bank SMS, fix a tag with one tap, manage keys and settings. Runs inside the API today, designed to split out later.
- Everything exposed as **REST + MCP** with provenance, staleness and annotations so the user's own AI reasons with good numbers.
- Every costly action **metered** against a per-user allowance; console comes later as a separate app on the same public API.

## Layout

- `api/` — Python backend: FastAPI (REST, MCP server, Telegram channel), worker, migrations
- `console/` — Next.js console, separate app with its own Dockerfile, built on the public API
- `connectors/` — n8n templates and scripts that feed `POST /raw`
- `seeds/` — reference data as JSON (system tags, units) with schemas; loaded idempotently at startup
- `i18n/` — shared message catalogues (`en` source, `vi` first-class) used by both `api/` and `console/`; `generated/` built from seeds
- `scripts/` — repo tooling (`seeds_check`, `i18n_gen`, `i18n_check`); run via `make check`
- `docs/` — [product](docs/product/00-vision.md) · [tech](docs/tech/00-architecture.md) · [ui](docs/ui/00-overview.md) · [index](docs/README.md)

## Stack

API: Python 3.12 · FastAPI · PostgreSQL 16 (+ pgvector, pg_trgm) · Redis 7 · Prometheus/Grafana
Console: Next.js · TypeScript · Tailwind + shadcn/ui · generated OpenAPI client
