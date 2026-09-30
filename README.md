# finassis
Finassis – A smart, AI-powered personal finance assistant that helps you track budgets, manage expenses, and optimize wealth with a touch of pop-culture personality

A ledger-first personal-finance **backend** built to be the financial memory an AI agent reads from. Expense, earning and wealth tracking over one append-only ledger; multi-user, multi-currency (USD + VND first) and single-instance-efficient from day one. Messy inputs (VN bank SMS/emails, CSVs) are handled by *recipes* that an LLM compiles once and plain code runs forever. Everything is exposed as REST + MCP tools with provenance, staleness and annotations so the user's own AI can reason with good numbers. Every costly action is metered against a per-user allowance from day one.

## Layout

- `api/` — Python backend (FastAPI, worker, MCP server)
- `console/` — Next.js console, separate app with its own Dockerfile, built on the public API
- `connectors/` — n8n templates and scripts that feed `POST /raw`
- `docs/` — [product](docs/product/00-vision.md) · [tech](docs/tech/00-architecture.md) · [ui](docs/ui/00-overview.md) · [index](docs/README.md)

## Stack

API: Python 3.12 · FastAPI · PostgreSQL 16 · Redis 7 · Prometheus/Grafana
Console: Next.js · TypeScript · Tailwind + shadcn/ui · generated OpenAPI client
