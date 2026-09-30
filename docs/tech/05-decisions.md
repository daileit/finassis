# Decision log

Lightweight ADRs. Add a new entry rather than editing an old one; mark superseded entries.

## ADR-001 — One ledger, three views

**Decision:** Expenses, income and wealth are derived from a single append-only `postings` table (double-entry style), not separate feature schemas.
**Why:** Guarantees consistency between cash flow and net worth; splits, transfers and asset purchases are representable without special cases.
**Consequence:** Slightly more ceremony for a "simple expense" (two postings). Hidden behind the API.

## ADR-002 — Multi-user from day one, enforced by RLS

**Decision:** Every table has `user_id`; Postgres row-level security is on; API sets `app.user_id` per request.
**Why:** Retrofitting tenancy is expensive; RLS is defence in depth against a missed `WHERE`.

## ADR-003 — Native currency at write, convert at read

**Decision:** Amounts stored in the currency they occurred in. `fx_rates` table; conversion happens in the reporting layer using the posting-date rate (historical) or latest (current), and every converted figure states the rate used. User default currency is USD, changeable; account/stream currency is per-object.
**Why:** No information loss; changing a default currency doesn't rewrite history.
**Consequence:** Rollups are keyed by currency; totals are computed by converting each currency bucket.

## ADR-004 — Python + FastAPI + Postgres + Redis

**Decision:** Single Postgres for all durable state; Redis for cache, Streams-based queue and pub/sub. No Kafka, no separate scheduler. See ADR-012 for the single-instance target.
**Why:** Smallest operational footprint that still gives async processing and replayability (raw_events are the durable log; Redis is disposable).

## ADR-005 — Append-only ledger with side-by-side aggregates

**Decision:** `period_rollups` (incremental + nightly re-close) and daily `balance_snapshots` / `networth_snapshots` with posting-id watermarks. Backdated corrections mark periods dirty and trigger re-close.
**Why:** Query cost independent of ledger size. Corrections are appends (reversals), never in-place edits.
**Rejected:** Loki-style index-only approach (still requires scanning detail); event-sourcing framework (overkill; the ledger *is* the event log).

## ADR-006 — Raw events are immutable and replayable

**Decision:** Every input is stored verbatim in `raw_events` before any interpretation. Processing can be re-run with any recipe version.
**Why:** Recipes will improve and templates will change; we must be able to reprocess history. Also gives idempotency via `content_hash`.

## ADR-007 — The ledger has one write path; AI is never on it

**Decision:** The structured transactions API is the only way to write postings. Ingestion of raw input is a pre-processor that produces structured calls. No LLM call happens in `/raw` processing.
**Why:** Provider outages, cost spikes or wrong extractions must not corrupt, slow or block the ledger. The ledger is truth precisely because nothing probabilistic writes to it directly.
**Supersedes:** the earlier confidence-gated auto-commit design.

## ADR-008 — MCP and REST share one domain service layer

**Decision:** MCP tools and REST endpoints are thin adapters over the same services; no logic in either adapter.
**Why:** n8n, Claude and any UI get identical semantics; one place to test.

## ADR-009 — AI compiles recipes; machine runs them

**Decision:** Unstructured input is handled by *recipes*: deterministic programs in a closed DSL (strip_html, regex via RE2, parse_amount, parse_date, lookup, const, …). An LLM is used once, user-triggered, to compile a recipe from a sample; the recipe is validated against the DSL schema, run against the sample, stored with a fixture, and versioned. All subsequent matching input runs through the interpreter in pure Python, no LLM.
**Why:** VN bank notifications are a small number of templates × a large number of messages. Paying LLM cost per template instead of per message is orders of magnitude cheaper and faster, and the behaviour is deterministic, auditable and testable. Schema failure is a visible signal to re-compile.
**Rejected:** LLM extraction per message with confidence gates (cost, latency, silent errors); a learned-rules loop promoted from corrections (subsumed — the recipe *is* the learned rule).
**Guardrails:** DSL is data not code; RE2 only; step and recipe timeboxes; required numeric fields must derive from captures; recipes are PII-free and shareable.

## ADR-010 — Data is designed for agent consumption

**Decision:** Finassis provides data; the user's own AI does analysis. Every numeric response carries `as_of`, staleness, source, confidence band, FX rate used, and relevant annotations. A `get_context_summary` tool returns a token-budgeted whole-picture snapshot. `annotations` let users and agents leave notes on any object for future sessions.
**Why:** The quality bar is "an external agent answers correctly from our tool output alone." Self-describing data prevents an agent from treating a 14-month-old house valuation as current or mixing currencies silently.
**Consequence:** Response schemas are heavier than a plain number; that is the point. Narration/personality is an optional layer and never touches tool data.

## ADR-011 — Vietnam first: USD + VND, per-currency precision

**Decision:** Day-one currencies are USD and VND; per-currency `decimals` (VND 0, USD 2, BTC 8) drive parsing, rounding and display. Default timezone `Asia/Ho_Chi_Minh`. Ingestion targets VN bank SMS/email templates first.
**Why:** No Plaid-equivalent in VN; heterogeneous wealth (gold, land, USD cash) is the norm; two-decimal assumptions break on VND.
**Amended by ADR-021:** gold is no longer a "commodity currency"; it is an instrument measured in mass units. The `currencies` table is subsumed by `units` (`measure = money`).

## ADR-012 — Single instance, efficient by design

**Decision:** Target deployment is one VM with one Postgres and one Redis. Efficiency is a day-one requirement: reports read only rollups/snapshots, ledger partitioned by month, raw payloads length-capped, recipes sub-10 ms, LLM usage rate-limited and user-triggered. Scaling paths (managed Postgres, more workers, API replicas) exist but are not built until needed.
**Why:** Solo/small effort; hosting cost matters; a design that is efficient at N=1 stays efficient at N=1000, the reverse is not true.

## ADR-013 — `/raw` is input-type agnostic; connectors live outside the core

**Decision:** One endpoint, `POST /raw {text, source?, received_at?, idempotency_key?}`, accepts any text. `source` is an optional free-form hint, not an enum. Anything that unwraps a transport (email, SMS relay, Telegram, CSV) is a connector outside the core that ends by calling `/raw`; connectors that are just "HTTP in, `/raw` out" should be n8n templates rather than code we maintain.
**Why:** Keeps the core small and stable while the set of sources grows with real demand. Fingerprinting works on text content; the hint only speeds it up.
**Consequence:** No `channel` enum anywhere. Length cap and idempotency live on `/raw`.

## ADR-014 — Meter every costly action from day one

**Decision:** Append-only `usage_events` + `usage_rollups` for API/MCP calls, raw items, recipe runs, compiler calls and tokens, storage bytes, webhook deliveries. Redis counters for O(1) quota checks. Prometheus `/metrics` and an `admin`-scoped API for operating the stack; Grafana is the operator console until a real one exists.
**Why:** API, storage, recipe runs and LLM compiles all cost money on a single instance. Metering built later is always incomplete; built first it is just middleware.

## ADR-015 — Plans, grants and "allowance"; monetisation is configuration

**Decision:** `plans` define per-kind limits; `grants` add top-ups (donation, promo, admin). Effective **allowance** = limit + grants − usage. `429 allowance_exhausted` carries top-up guidance. Ledger *reads* are never quota-blocked. Internal term is `allowance`; the personality layer may call it *mana*.
**Why:** Leaves the monetisation outlet wired without committing to a pricing model. Adding a paid plan or a donation webhook touches no domain code.

## ADR-016 — Console is a separate app in the same repo (monorepo)

**Decision:** No UI inside the Python service. The console is a Next.js/TypeScript app under `console/` with its own Dockerfile, build and deploy, in this repository alongside `api/`. It talks to the backend only via the public API (+ admin API), using a client generated from `/openapi.json` in CI. The backend provides browser auth (OIDC + cookie session), `/me`, self-service endpoints, stats via the same read APIs agents use, SSE and the OpenAPI spec. See [../ui/](../ui/00-overview.md).
**Why:** Same repo keeps product, API and UI changes in one history and one PR; separate app keeps runtimes decoupled (no Node in the Python image, independent deploys) and forces the console to dogfood the public API. Python-templated UI would be rewritten anyway.
**Amends:** an earlier version of this ADR said "separate repo"; consolidated into a monorepo for consistency and relatability.

## ADR-017 — Money on the wire is integer minor units + decimals + display

**Decision:** The API never emits floats or bare strings for money. Every money value is an object: `{"amount": 25000000, "currency": "USD", "decimals": 2, "display": "250,000.00"}`. `amount` is an integer in the currency's minor unit (VND: 0 decimals → `250000` is 250,000 ₫; BTC: 8; gold: 4). `decimals` is always included so clients need no lookup. `display` is a formatted convenience for humans and agents and is never parsed. Postgres stores `NUMERIC`; this is a serialisation rule only.
**Why:** Exact and arithmetic-safe in JavaScript (integers within 2^53), the convention used by payment APIs, unambiguous for LLM agents (bare strings like `"250000"` risk being treated as codes; floats risk rounding).
**Rejected:** floats (precision loss on fractional and 8-decimal values); decimal strings (poor ergonomics, silent concatenation in JS).

## ADR-018 — Backend language is Python

**Decision:** The `api/` service (API, worker, MCP server, recipe interpreter and compiler) is written in Python 3.12+. The console is TypeScript. Node is not used in the backend.

## ADR-019 — Same-origin topology; the browser calls the API directly

**Decision:** One reverse proxy routes `/api/*`, `/auth/*`, `/events/*` to FastAPI and everything else to the console's static export. Next.js is a build tool, not a BFF; no API calls pass through Next.js server code.
**Why:** No CORS, cookies work unchanged, one fewer runtime, and the console exercises the identical public API that agents and n8n use. A BFF would let logic accumulate where agents cannot reach it.

## ADR-020 — i18n: English identifiers in the backend, translation in the console, user data untouched

**Decision:** Vietnamese is the first-class locale, English the default and source language. The backend stores and returns enums/system keys in English and holds no translations; system tags are identified by `system_key` and translated in the console with `en` fallback, custom tags carry only the user's name. The console translates UI strings and enum identifiers via namespaced message catalogues (`next-intl`, ICU); CI verifies coverage against `GET /meta/enums`. User-entered data is never translated. Formatting (numbers, dates, money) follows locale and `decimals`, and is separate from translation. Narration is generated in the user's locale.
**Why:** Keeps the data model language-free, makes adding a locale a console-only change, and avoids ever "translating" a user's own transaction text.

## ADR-021 — Measures and units; money is one measure; aggregation is per unit

**Decision:** One `units` table: `code, measure (enum: money | mass | count | area), factor_to_base, decimals, symbol, name (user-defined only), user_id (NULL = global)`. Global seed + user-defined units inside existing measures; adding a measure is a migration. Currencies are the units of the `money` measure and are the one case where conversion is time-varying (`fx_rates`) rather than a constant factor. Postings carry money (`amount`, `currency`) and optionally stuff (`quantity`, `unit`). Instruments declare a `measure` and `default_unit`; prices are money per unit. Global unit names are translated in the console catalogue like any enum; user-defined units carry their own `name`; the backend stores no unit translations.

Quantities are **aggregated per unit** and never auto-converted: 3 oz_troy and 3 chỉ of gold are two holding rows, each valued through its own price-per-unit; their *money* values are summed. Changing a stream's or account's unit affects new entries only. Converting quantity history or producing a combined single-unit snapshot is an explicit, user-triggered operation and a candidate paid feature.

**Why:** Gold, land, livestock, shares and packs are all "a number of some unit of a thing," and money is just the special case with a rate table. One model covers all of them without schema changes. Per-unit aggregation keeps the default path exact, cheap, and faithful to what the user typed; conversion is a value-add worth charging for.
**Rejected:** gold as a pseudo-currency (ADR-011 original); a full physics unit system with compound units; automatic conversion to a canonical unit on write; a separate `measures` table and a `unit_labels` table (first draft — collapsed into the enum column and the console catalogue, see ADR-022).

## ADR-022 — No sub-unit hierarchy; mixed-unit input normalises to the smallest unit; mixed display is formatting

**Decision:** Units are flat; every relationship, decimal or not (10 chỉ = 1 lượng, 12 in = 1 ft, 16 oz = 1 lb), is expressed by `factor_to_base`. Base units are chosen (g, m², unit) so seeded factors are finite decimals. A quantity is stored as **one value in one unit**. Input that names several units of the same measure is normalised to the **smallest unit mentioned** (`2 lượng 3 chỉ` → `23 chi`; `1 kg 250 g` → `1250 g`); this is exact and preserves the user's precision. Structured API and MCP accept `{value, unit}` or `{parts: [...]}`; the recipe DSL gains `parse_quantity`. Mixed-unit **display** ("2 lượng 3 chỉ") is a console formatting preference using per-measure, per-locale display chains; it never changes stored data and never merges holding rows. "Pack"-style units are user-defined named units (e.g. "Tiger 24-pack", factor 24); instrument-scoped units are a documented additive extension if needed.
**Why:** A tree adds nothing a factor doesn't and breaks on units that aren't nested (troy ounce vs lượng vs gram). Smallest-unit normalisation avoids repeating fractions and second-guessing the user. Keeping display separate mirrors the money rule ("formatting is not translation").
**Extension paths** are listed in [01-data-model.md](01-data-model.md#extension-paths-designed-for-not-built): new measures, instrument-scoped units, paid conversion, versioned factors, per-user unit prefs — all additive.

## ADR-023 — Tags: fixed roots, extensible children, cheapest-first tagging with confirmed memory

**Decision:** "Tag" is the controlled label; each posting has exactly one primary `tag_id` plus optional free-form `labels[]`. Roots are fixed by Finassis and carry `kind`; children are system (shipped, extensible by seed) or custom (user-created, child level only, allowance-gated). Tagging runs a cheapest-first cascade: caller-supplied → user rules → exact fingerprint cache → merchant memory (user, then global anonymised) → keyword dictionary → vector kNN over the user's confirmed examples → untagged → nightly, allowance-gated LLM batch producing *proposals*. Only confirmed tags (user, agent, caller) write to memory; "always tag like this" stores a high-weight pinned example. Suggestions for unknown tag names and low-confidence results come from trigram matching plus a tiny embedding index of the tag list itself — no LLM. System tags translate in the console by `system_key` with `en` fallback; custom tags are never translated.
**Why:** Per-transaction LLM calls don't fit a free tier or a single instance. Every layer above the LLM is a string lookup or a small-vector search that gets cheaper as the user confirms more. Confirmation-gated memory prevents wrong guesses from compounding. Fixed roots keep budgets, reports and community content stable while children absorb personalisation.
**Rejected:** free-form categories (breaks aggregation and community rules); storing LLM guesses as truth; deep category trees.

## ADR-024 — Interactions as a core model; Telegram is the first channel and lives inside the API service

**Decision:** A pending question for the user (tag proposal, review item, unknown tag, recipe confirmation, stale valuation, alert, onboarding step, sensitive-action confirmation) is a first-class `interactions` row with ≤ 6 options, language-neutral prompt keys, priority and expiry. Channels render and resolve interactions and hold no state. Telegram is the first channel — registration (`/start`), raw input, queries, tag confirmation with inline keyboards, and account ops via commands — implemented as a module (`channels/telegram`) **inside the `api/` process**, calling domain services directly, with pushes sent by the existing worker. `identities` maps provider ids (telegram, later google) to one user. REST exposes the same interactions; MCP and the console are further renderers.
**Why:** Multi-turn option choosing is a poor fit for an LLM tool surface and the console doesn't exist yet; Telegram is where VN users already forward bank messages and its inline keyboards are exactly the UI for "pick one of three." One model for all pending questions means every channel added later gets every feature. A separate bot process would be HTTP between two halves of the same deployment; if the API is down the bot is useless anyway. The boundary that matters (no logic the other adapters lack) is enforced by package structure, like MCP.
**Separation is designed in:** the bot never imports domain services. It depends on a `FinassisClient` port (in-process implementation now, generated HTTP client later) and an `EventSource` port (Redis stream now, webhooks/SSE later), selected by config. A `channel` API-key kind with `X-Act-As-User` is defined in the auth model now so the split needs no auth redesign. Contract tests run the bot against both client implementations.
**Rejected:** bot as a separate service *today*; bot importing the domain layer directly (blocks the split); conversational state held in the bot; Telegram-specific features that the console/MCP could not also resolve. Zalo first (OA API restrictions) — later channel.

## Open questions

- Household / shared ledgers: separate `tenant_id` above `user_id`, or a sharing table? Decide before Phase 3.
- Lot-matching method for realised gains (FIFO default; per-account override?).
- Initial price/FX sources for VND, USD, SJC gold, VN stocks (free-tier limits, scraping legality).
- Pricing of a *combined* gold position when purities differ (SJC 9999 vs 18K) is per instrument, but should the UI offer a "total fine gold" line? Only as part of the paid conversion feature.
- Recipe DSL: YAML or JSON as the canonical stored form? (JSON in DB, YAML for humans, likely.)
- Community recipe library: moderation and trust model for shared bank templates.
- Payment/donation provider for grants (Stripe not available for VN merchants; consider Lemon Squeezy/Paddle, MoMo/ZaloPay, or Buy Me a Coffee-style links + manual grants at first).
- Browser auth provider for the console: Google OIDC only, or add Zalo/Facebook? (Telegram identity exists first; console login can also be "open Telegram, tap the login link".)
- Group chats in Telegram (household ledger) — tied to the shared-ledger question.
- Email intake: forwarding address per user vs. IMAP pull vs. user-side n8n → `/raw`. Start with an n8n template; build a hosted connector only on demand.
