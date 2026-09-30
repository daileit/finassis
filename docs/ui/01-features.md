# Console — features & screens

Ordered roughly by build priority. Each screen names the API endpoints it depends on (see [03-api-contract.md](03-api-contract.md)).

## User

### Overview (`/`)
Net worth (default currency, with breakdown popover and staleness flags), liquid cash, month-to-date spend vs budget pace, income due in the next 30 days, open review items, recent alerts, recent annotations.
API: `/reports/net-worth`, `/balances`, `/reports/spend`, `/budgets/status`, `/projections/cashflow`, `/review-items`, `/alerts`, `/annotations`.

### Ledger (`/ledger`)
Filterable, paginated transaction list (account, tag, date range, amount, text, source). Row → detail drawer: postings, tag edit with suggestions, split, link refund, annotations, and **provenance**: raw event text, recipe + version, reconciliation outcome. Manual add via structured form.
API: `/transactions`, `/postings`, `/raw-events/{id}`, `/recipes/{id}/versions/{v}`, `/annotations`.

### Wealth (`/wealth`)
Accounts grouped by class and liquidity; mark-to-market items with latest valuation, `as_of`, staleness badge, source, note; add valuation; holdings with lots, unrealised/realised gains; net-worth time series chart with per-currency stacking.
API: `/accounts`, `/assets`, `/valuations`, `/holdings`, `/reports/networth-series`.

### Cash flow & budgets (`/cashflow`)
Income vs expense by month, tag breakdown, budget pace bars, income streams with expected vs actual and missed markers, cash-flow projection to a chosen date with assumptions listed.
API: `/reports/spend-series`, `/reports/income-series`, `/budgets`, `/income-streams`, `/projected-income`, `/projections/cashflow`.

### Recipes (`/recipes`)
My recipes and community library. Recipe page: fingerprint, DSL steps (read-only YAML view + guarded editor), versions with diff, fixtures with pass/fail, run-against-sample, enable/disable, publish to community (redaction confirmation). "Propose from sample": paste text or pick a raw event → shows compile progress → result with test outcome.
API: `/recipes`, `/recipes/{id}/versions`, `/recipes/{id}/fixtures`, `/recipes/propose`, `/raw-events`.

### Inbox (`/inbox`)
All open interactions (tag proposals, review items, stale valuations, alerts, confirmations) as cards with their options; resolving here closes them in Telegram too. Keyboard-driven.
API: `/interactions`, `/interactions/{id}/resolve`.

### Review queue (`/review`)
List with reason chips (`no_recipe`, `schema_failed:<field>`, `ambiguous_match`, `recipe_timeout`). Item view: raw text side-by-side with partial extraction; accept / edit-and-accept / reject / propose recipe. Keyboard-driven for speed.
API: `/review-items`, `/review-items/{id}/resolve`, `/recipes/propose`.

### Integrations (`/integrations`)
- API keys: create with scopes, show once, last used, revoke/rotate.
- Webhooks: URL, events, secret, test delivery, delivery log.
- Connectors: guides and downloadable n8n templates (email → `/raw`, SMS relay, Telegram), each pre-filled with the user's endpoint.
- MCP: copy-paste config snippet for Claude Desktop / other clients, with a key selector.
API: `/keys`, `/webhooks`, `/webhooks/{id}/test`, `/webhooks/{id}/deliveries`.

### Usage & allowance (`/usage`)
Per-kind usage for the period, remaining allowance, reset date, active grants, plan details; donate/top-up entry point (provider TBD); history chart.
API: `/usage`, `/allowance`, `/me`.

### Settings (`/settings`)
Profile, default currency, timezone, locale, personality profile (voice presets + custom notes), alert channels, tag editor (hide/rename system tags, custom child tags), account aliases (for recipe lookups), data export, account deletion (grace period).
API: `/me`, `/tags`, `/account-aliases`, `/exports`, `DELETE /me`.

## Operator (`admin` scope)

### Tenants (`/admin/tenants`)
List with plan, usage, storage, last activity; tenant page: allowance, grants (add), plan (change), pause/unpause, export/delete, recent errors.
API: `/admin/users`, `/admin/users/{id}/grants`, `/admin/users/{id}/plan`, `/admin/users/{id}/pause`.

### Pipeline (`/admin/pipeline`)
Stream lag, pending, failed items with requeue/replay; recipe failure leaderboard by version with disable action; compiler spend by day and tenant.
API: `/admin/pipeline`, `/admin/raw-events/{id}/replay`, `/admin/recipes`, `/admin/compiler-runs`.

### System (`/admin/system`)
Effective config (redacted), hot-reload, job status and last runs, audit log.
API: `/admin/config`, `/admin/jobs`, `/admin/audit`.

Grafana remains the place for time-series operational metrics; the operator console links out to it rather than re-implementing dashboards.
