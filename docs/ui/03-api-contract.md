# Console — what it needs from the API

This is the checklist the backend must satisfy for the console to be buildable without console-private endpoints. Items already required by agents/MCP are marked ✱; the rest are console-driven additions to the public API.

## Auth & identity

- `GET /auth/oidc/start`, `GET /auth/oidc/callback` → HttpOnly session cookie; `POST /auth/logout`; `POST /auth/refresh`.
- `GET /me` → `{user, default_currency, timezone, locale, scopes[], plan, usage_to_date{kind→qty}, allowance{kind→remaining, resets_at}, grants[], csrf_token, personality_profile}`.
- CORS for console origin(s) with credentials; `X-CSRF-Token` enforcement on mutations from cookie sessions.

## Self-service

| Resource | Endpoints |
|----------|-----------|
| API keys | `GET /keys`, `POST /keys {name, scopes, expires_at?}` (returns plaintext once), `DELETE /keys/{id}`, `POST /keys/{id}/rotate` |
| Webhooks | `GET/POST /webhooks`, `PATCH/DELETE /webhooks/{id}`, `POST /webhooks/{id}/test`, `GET /webhooks/{id}/deliveries` |
| Recipes ✱ | `GET /recipes?scope=mine|community`, `GET /recipes/{id}`, `GET /recipes/{id}/versions`, `POST /recipes/{id}/versions` (edit → new version), `GET/POST /recipes/{id}/fixtures`, `POST /recipes/{id}/run {raw_event_id|text}` (dry run), `PATCH /recipes/{id} {is_active}`, `POST /recipes/{id}/publish`, `POST /recipes/import {community_recipe_id, version}` |
| Recipe compile ✱ | `POST /recipes/propose {raw_event_id|text, hints?}` → async job id; `GET /jobs/{id}` |
| Review queue ✱ | `GET /review-items`, `GET /review-items/{id}`, `POST /review-items/{id}/resolve {action, edits?}` |
| Raw events ✱ | `GET /raw-events?status=&source=`, `GET /raw-events/{id}` (text + processing trail), `POST /raw` |
| Annotations ✱ | `GET /annotations?target_type&target_id`, `POST /annotations`, `DELETE /annotations/{id}` |
| Preferences | `PATCH /me {default_currency, timezone, locale, personality_profile, alert_channels}` |
| Tags ✱ | `GET /tags` (combined list: system ∪ customs, with prefs applied), `POST /tags` (custom child; `root_id` required; allowance-gated), `PATCH /tags/{id}` (rename custom / `display_name_override` + `is_hidden` for system), `DELETE /tags/{id}` (custom only; postings re-tag to root), `GET /tags/suggest?q=milktea` → `[{tag, score, source}]` + `create_as_child_of` |
| Tagging ✱ | `POST /postings/{id}/tag {tag_id, pin?: true}` (confirm; `pin` = "always tag like this"), `GET /postings/untagged`, `GET /tag-proposals`, `POST /tag-proposals/{id}/resolve`; `409 unknown_tag {suggestions, create_as_child_of}` on writes with an unknown tag name; `create_if_missing=true` to auto-create a custom child |
| Aliases | `GET/POST/DELETE /account-aliases` |
| Interactions ✱ | `GET /interactions?status=open&kind=`, `POST /interactions/{id}/resolve {option_key \| text}`, `POST /interactions/{id}/dismiss` — the console's inbox is a renderer of these, same as Telegram |
| Identities | `GET /identities`, `POST /identities/link-codes` (mint code for `/link` in Telegram), `DELETE /identities/{provider}/{id}` (not the last one) |
| Data | `POST /exports {format}` → job; `GET /exports/{id}` → download URL; `DELETE /me` (schedules deletion, grace period) |
| Usage | `GET /usage?period=month&from&to` per kind; `GET /allowance` |

## Reports & stats (all ✱, shared with agents)

- `GET /reports/net-worth?at&currency&breakdown_by=class|liquidity|currency|account`
- `GET /reports/networth-series?from&to&currency&granularity=day|month`
- `GET /reports/spend?period&group_by&currency&filters…`, `GET /reports/spend-series?from&to&granularity&group_by`
- `GET /reports/income?…`, `GET /reports/income-series?…`
- `GET /balances?at&currency`
- `GET /assets?include_stale` (mark-to-market items with latest valuation, staleness, notes)
- `GET /holdings`, `GET /accounts/{id}/lots`
- `GET /projections/cashflow?to_date&currency&scenario?`
- `GET /budgets/status?period`
- `GET /alerts?since`
- `GET /transactions?…&cursor`, `GET /transactions/{id}` (with postings, raw_event_id, recipe_id/version, reconciliation)

Every monetary field follows the agent-consumption envelope: `{value, currency, as_of, staleness?, source, confidence, fx?, annotations?}` (see tech/04-ai-layers.md §B). The console renders these fields; it never recomputes them.

## Realtime ✱

- `GET /events/stream` (SSE): `posting.committed`, `review.needed`, `review.resolved`, `alert.raised`, `recipe.failed`, `job.completed`.

## Admin (scope `admin`)

- `GET /admin/users?…`, `GET /admin/users/{id}`, `POST /admin/users/{id}/grants`, `PUT /admin/users/{id}/plan`, `POST /admin/users/{id}/pause|unpause`, `POST /admin/users/{id}/export|delete`
- `GET /admin/pipeline` (stream lag, pending, failed), `POST /admin/raw-events/{id}/replay`, `POST /admin/pipeline/requeue-failed`
- `GET /admin/recipes?sort=failure_rate`, `POST /admin/recipes/{id}/versions/{v}/disable`
- `GET /admin/compiler-runs?from&to&group_by=day|user`
- `GET /admin/config`, `POST /admin/config/reload`, `GET /admin/jobs`, `POST /admin/jobs/{name}/run`
- `GET /admin/audit?…`

## Contract hygiene

- OpenAPI 3.1 at `/openapi.json`, with `operationId`s stable enough to generate a client; tagged by area.
- Cursor pagination `{items, next_cursor}` everywhere lists can grow.
- Error envelope `{code, message, details?}`; `429 allowance_exhausted` includes `{kind, limit, used, resets_at, top_up_url?}`.
- Async operations (`propose`, `exports`, admin replays) return a `job` and are polled via `GET /jobs/{id}` or observed via SSE `job.completed`.
- Money envelope (ADR-017): `{"amount": 25000000, "currency": "USD", "decimals": 2, "display": "250,000.00"}` — `amount` is an integer in minor units, `decimals` is included so no lookup is needed, `display` is a locale-neutral convenience. Clients do arithmetic on `amount`, never on `display`.
- Quantity envelope (ADR-021/022): `{"value": "23", "unit": "chi", "decimals": 3, "name": null}` — `name` is set only for user-defined units; global unit labels come from the console catalogue. Holdings return one row per unit. Quantity **input** accepts `{value, unit}` or `{parts: [{value, unit}, …]}` (same measure; normalised to the smallest unit). `GET/POST /units?measure=` for pickers and user-defined units (`measure`, `factor_to_base`, `decimals`, `name` required); `GET /meta/enums` includes `measure` values.
- Conversion endpoints (`POST /holdings/convert`, `GET /reports/holdings?combine_units=true`) are allowance-gated (paid feature candidate) and always return the factors used.
- Enums are English identifiers (`credit_card`, `schema_failed`); the console translates them. `GET /meta/enums` lists every enum the API can return so translation coverage can be tested in CI.
- `Accept-Language` overrides `users.locale` for a request; affects only `display` formatting and narration, never data.
