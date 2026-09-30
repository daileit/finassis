# Console — overview

The console is the web UI for Finassis: stats dashboards for users, and control over integrations, keys, recipes, usage and settings. It lives in this repository under `console/` as a **separate application** with its own Dockerfile, build and deploy, and talks to the backend only through the public API (plus the admin API for operator views). See ADR-016.

## Why in the same repo, but separate

- **Consistency**: one issue tracker, one PR can change an endpoint and the screen that uses it, one version history.
- **Contract enforcement**: the console generates its TypeScript client from `api`'s `/openapi.json` in CI. A backend change that breaks the UI fails the build.
- **Decoupled runtime**: separate Dockerfiles and images; the Python image never contains Node, and the console can deploy to a static host or edge while the API stays on the VM.
- **Dogfooding**: the console uses exactly the same read/write APIs as n8n and MCP agents. If the console needs a number, an agent needs it too — no console-private endpoints.

## Two audiences, one app

| Audience | Auth | Sees |
|----------|------|------|
| User | OIDC login → cookie session | Overview, ledger, wealth, recipes, review queue, integrations, usage & allowance, settings |
| Operator | same login + `admin` scope | Tenants, pipeline health, recipe failure rates, compiler spend, config, audit log |

Operator views are routes gated by scope, not a second app.

## Principles

- **Read what agents read.** Every chart is fed by a public report endpoint; every figure shows `as_of`, staleness and the FX rate used, exactly as the API returns them.
- **Show provenance.** A transaction detail page links to its raw event and the recipe version that produced it; a valuation shows its source and note.
- **Allowance is visible, not surprising.** Usage per kind, remaining allowance and reset date are one click away; a `429 allowance_exhausted` is rendered as a friendly top-up prompt (the personality layer may call it *mana*).
- **Vietnamese first, English default.** i18n from the start (`vi`, `en`). UI strings and domain terms are translated in the console from English identifiers; user data is never translated. VND formatting (`250.000 ₫`, no decimals), `dd/mm/yyyy`, `Asia/Ho_Chi_Minh` default. See [02-architecture.md](02-architecture.md#internationalisation).
- **Mobile-usable.** Not an app, but the review queue and overview must work on a phone.

## Documents

- [01-features.md](01-features.md) — screens and what each does
- [02-architecture.md](02-architecture.md) — stack, structure, auth flow, data fetching, build/deploy
- [03-api-contract.md](03-api-contract.md) — what the console needs from the backend
