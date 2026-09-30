# Interactions and channels

## The problem

Several features need to *ask the user something and wait*: confirm a tag proposal, resolve a review item, pick or create a tag for an unknown name, approve a compiled recipe, refresh a stale valuation, acknowledge an alert, finish onboarding. An LLM tool surface is poor at multi-turn option choosing, and the console does not exist yet. We need one model of "a pending question with options" and one or more channels that render and resolve it.

## Interactions (core model)

```
interactions
  id, user_id, kind (tag_proposal | review_item | unknown_tag | recipe_confirm | stale_valuation | alert |
                     onboarding_step | allowance_warning | confirm_action),
  subject_type, subject_id,                       -- posting, raw_event, recipe, account, ...
  prompt jsonb,                                   -- language-neutral: {key: "tag_proposal.prompt", params: {...}}
  options jsonb,                                  -- [{key, label: {key | text}, payload}], ≤ 6
  allow_free_text bool, free_text_hint_key (nullable),
  priority (now | today | digest),
  status (open | resolved | expired | dismissed), resolution jsonb, resolved_via (telegram | console | mcp | api),
  created_at, expires_at, resolved_at
  INDEX (user_id, status, priority, created_at)
```

Rules:

- Created by domain services, never by channels. Tagging creates `tag_proposal`; the pipeline creates `review_item` and `recipe_confirm`; jobs create `stale_valuation`, `alert`, `allowance_warning`; the API creates `unknown_tag` when a caller sends a tag name it cannot resolve; `confirm_action` guards sensitive operations (reveal key, delete account, change plan).
- Resolution runs the domain action (set tag, accept review item, publish recipe…) and marks the row; the channel only reports the choice. Resolving via one channel closes it in all.
- `prompt` and option labels are **keys with params**, rendered per user locale by the channel. Free text from the user (a custom tag name, a valuation note) is stored verbatim.
- `priority` drives delivery: `now` pushes immediately, `today` batches into a daily message, `digest` into the weekly one.
- Expiry closes stale questions (default 14 days) so queues don't rot; expired tag proposals leave the posting `untagged`.

Domain events: `interaction.created`, `interaction.resolved`, `interaction.expired` on the event stream; webhooks may subscribe.

## Channels

A channel is a renderer + resolver of interactions and a thin adapter over the domain services for commands. Channels hold **no state**: everything is in `interactions`, `identities` and the user profile.

| Channel | Renders as | Resolves via | Status |
|---------|-----------|--------------|--------|
| Telegram | message + inline keyboard | callback `interaction_id:option_key`; one free-text reply when `allow_free_text` | Phase 1 |
| MCP | `list_interactions()` JSON | `resolve_interaction(id, option_key | text)` | Phase 2 |
| REST | `GET /interactions`, `POST /interactions/{id}/resolve` | same | Phase 1 (used by the others) |
| Console | cards / inbox | same REST | Phase 5 |
| Zalo OA | message + buttons | same pattern | later |
| Email digest | weekly list with deep links | links → REST/console | later |

## Telegram channel — inside the API service today, separable tomorrow

The bot ships **as a module of `api/`** (`api/src/finassis/channels/telegram/`) and runs in the same process. It is designed so that moving it to its own service is a configuration change, not a rewrite. Rationale in ADR-024.

### The rule: the bot never imports the domain layer

The bot depends on two **ports** (interfaces), not on domain services:

```python
class FinassisClient(Protocol):          # everything the bot asks the core to do
    async def raw_ingest(self, user: UserRef, text: str, source: str) -> RawResult: ...
    async def get_balances(self, user: UserRef, at: date | None, currency: str | None) -> Balances: ...
    async def list_interactions(self, user: UserRef, status: str) -> list[Interaction]: ...
    async def resolve_interaction(self, user: UserRef, id: UUID, choice: Choice) -> Interaction: ...
    async def create_key(self, user: UserRef, name: str, scopes: list[str]) -> KeyOnce: ...
    # ... one method per public endpoint the bot uses, mirroring the OpenAPI operationIds

class EventSource(Protocol):             # everything the core tells the bot
    def subscribe(self, kinds: set[str]) -> AsyncIterator[Event]: ...   # interaction.created, alert.raised, digest.due
```

Two implementations of each, chosen by config (`CHANNEL_TELEGRAM_MODE = inprocess | remote`):

| Port | `inprocess` (now) | `remote` (later) |
|------|-------------------|------------------|
| `FinassisClient` | `InProcessClient`: calls the **same request handlers** REST uses (through the service layer, with auth context set to the mapped user) — no HTTP, no serialization, but the exact same code path and quota checks as an API call | `HttpClient`: generated from `/openapi.json`, authenticated with a **service key** (`kind = channel`, scope `channel:telegram`) that may act **on behalf of** a user resolved via `identities` |
| `EventSource` | Redis Stream consumer group `channel-telegram` | Webhook receiver or SSE `/events/stream` subscribed with the service key |

The bot's Telegram-specific code (update parsing, keyboards, rendering) sits on top and is identical in both modes. A contract test runs the bot's handler suite against **both** clients so drift is caught early.

### Why in-process now

One deployment, one image, one thing to operate; if the API is down the bot is useless anyway. `InProcessClient` avoids loopback HTTP and double serialization while still going through the API's own handlers, so quotas, RLS context, metering and audit behave exactly as for an external caller.

### What the split looks like when it comes

1. Add the `channel` key kind and `act_as` support in the API (a service key + `X-Act-As-User` header, allowed only for `channel:*` scopes, audited).
2. Set `CHANNEL_TELEGRAM_MODE=remote`, give the bot a service key, point Telegram's webhook at the new host.
3. Move `channels/telegram/` to `bot/` with its own Dockerfile; its only dependency on the repo becomes the generated client.

No domain code changes. The `EventSource` swap is the same shape: the bot subscribes to webhooks instead of the Redis stream.

### Flow

```
Telegram ──webhook──▶ POST /channels/telegram/webhook (FastAPI, secret-token verified)
                        → parse update → identities(telegram, chat_id) → UserRef
                        → command | callback | free-text handler
                        → FinassisClient.<method>(user, ...)      # in-process or HTTP, same call
                        → render reply (i18n, user locale) → Telegram sendMessage

EventSource ──▶ interaction.created / alert.raised / digest.due
                        → render → Telegram sendMessage (push)
```

### Identity & registration

```
identities
  user_id, provider (telegram | google | email), provider_id, display_name, linked_at, is_primary
  PK (provider, provider_id); INDEX (user_id)
```

- `/start` in a new chat → create user (locale from Telegram language code, `vi`/`en`), `identities(telegram, chat_id)`, seed default tags, open `onboarding_step` interactions (default currency? timezone? first bank?).
- `/link <code>` → attach this Telegram to an existing user; codes are minted in the console or via API (`POST /identities/link-codes`), single-use, 10-minute TTL.
- The console later adds a Google identity to the same user. One user, many identities.
- Trust: a chat id maps to exactly one user; the bot never acts on a chat it can't map. Group chats are ignored in v1.

### Commands (thin over the same endpoints)

| Area | Commands |
|------|----------|
| Input | any text or forwarded message → `POST /raw` → transaction card with buttons `Change tag · Split · Undo`; if untagged/low confidence the card *is* the `tag_proposal` interaction (top 3 suggestions + `Other…`) |
| Queries | `/balance` · `/networth` · `/spend [week\|month] [tag]` · `/income` · `/assets` (with staleness) · `/budget` · `/untagged` · `/review` |
| Interactions | `/inbox` lists open interactions with buttons; digest messages daily/weekly |
| Tags | `/tags` · `/tag add <name> under <root>` · `/tag hide <name>` |
| Ops | `/keys` (list/create/revoke; new key shown once with a self-delete button) · `/webhooks` · `/recipes` · `/usage` · `/plan` |
| Settings | `/lang vi\|en` · `/currency` · `/tz` · `/voice` (personality) · `/export` · `/delete` |
| Help | `/help`, `/start` |

### Rendering

- All text through the same i18n catalogue mechanism as the console (keys + ICU), rendered server-side per user locale. Money via the envelope's `decimals`; quantities via unit labels.
- Cards are short. Telegram is not a dashboard; anything long links to the console when it exists, or offers `/export`.
- Inline keyboards ≤ 6 buttons; `Other…` sets `awaiting_free_text` on the interaction with a 10-minute TTL; the next text message from that chat resolves it (and is *not* sent to `/raw`).
- Personality voice applies to narration lines only (summaries, nudges), never to numbers or option labels.

### Safety

- Webhook secret token + Telegram IP allowlist (or signature) verified before parsing.
- Sensitive commands create a `confirm_action` interaction (second tap) and are allowance/scope checked like any API call.
- Per-chat rate limit; raw-text inputs count against `raw.item` allowance like any other source (`source: "telegram"`).
- Keys revealed in chat carry a "delete this message" button; we also offer `/keys rotate`.

### Act-as-user for service keys (defined now, used at split time)

```
api_keys.kind ∈ user | admin | channel
scopes for channel keys: channel:telegram (later channel:zalo …)
header: X-Act-As-User: <user_id>   -- accepted only for kind=channel; the user must have an identity
                                       for that channel's provider; every act-as call is audited
```

Defining this in the auth model now costs nothing and means the split requires no auth redesign.

## What this changes elsewhere

- Tagging (`product/03-tags.md`): proposals and unknown-tag suggestions are interactions.
- Ingestion review queue: `review_items` remain the detailed record; each open one has a companion `review_item` interaction for delivery.
- Alerts: delivered as interactions with `priority`.
- Roadmap: Telegram channel lands in Phase 1, making the console optional for a long time.
