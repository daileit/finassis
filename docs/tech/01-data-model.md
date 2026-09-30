# Data model

All monetary amounts are `NUMERIC(24, 8)` in the **native currency of the posting**. Display/rounding precision comes from `units.decimals`, never from a hard-coded "2 decimals". All tables carry `user_id` and are under RLS.

**Serialisation** (ADR-017): the API never emits floats for money. Every money value is `{amount: <integer minor units>, currency, decimals, display}`.

**Language**: all enums and system identifiers are stored in English (`credit_card`, `schema_failed`, `coffee_drinks`). The database holds no translations; the console translates identifiers with an `en` fallback. User-entered text (including custom tag names) is stored verbatim in whatever language the user used.

## Measures and units

Every quantity in the system is a number plus a **unit**; every unit belongs to exactly one **measure** (dimension). Units inside a measure convert by a fixed factor. **Money is a measure whose units are currencies**, and it is the one measure where conversion is time-varying (via `fx_rates`) instead of a constant. See ADR-021.

```
units                                    -- ONE table. measure is an enum column, not a table.
  code (PK), measure (enum: money | mass | count | area), 
  factor_to_base NUMERIC(30,15) (NULL for money → use fx_rates),
  decimals int, symbol (nullable), name (nullable; user-defined units only),
  user_id (NULL = global), is_system bool, created_at
  CHECK ((measure = 'money') = (factor_to_base IS NULL))

  base unit per measure (by convention, factor 1): money → n/a · mass → g · count → unit · area → m2
  seed (global):
    money: VND (0) · USD (2) · EUR (2) · BTC (8) · ETH (8)
    mass:  g (1, 3) · kg (1000) · oz_troy (31.1034768) · luong (37.5) · chi (3.75) · phan (0.375)
    count: unit (1, 0) · share (1, 0)
    area:  m2 (1, 2) · ha (10000) · sao_bac (360) · sao_trung (500) · sao_nam (1000)
  user-defined examples (user_id set, name required, same measure rules):
    count: "Tiger 24-pack" (factor 24) · mass: "my gold ring" (factor 1.2 → g)
```

Global unit names are translated in the console catalogue like any enum (`unit.luong` → "lượng" / "tael"), covered by the `/meta/enums` CI check; user-defined units display their own `name`. The backend stores no unit translations.

Rules that keep it small: no compound units (VND per m² is a *price*, not a unit); no cross-measure conversion; users add units only inside existing measures; adding a measure is a migration; purity, grade, breed, location are **instrument** attributes, not units. Base units are chosen so that all seeded factors are finite decimals, which `NUMERIC(30,15)` holds exactly.

### Mixed-unit input → one value, one unit

A quantity is always stored as **one number in one unit**. When input names several units of the same measure — `2 lượng 3 chỉ`, `1 kg 250 g` — it is normalised to the **smallest unit mentioned**: `23 chi`, `1250 g`. This is always exact and keeps the user's precision. Single-unit input is stored as typed. Applies to the structured API (`{value, unit}` or `{parts: [{value, unit}, …]}`), the recipe DSL (`parse_quantity` step), and MCP.

Mixed-unit **display** ("2 lượng 3 chỉ") is a formatting concern handled by the console via per-measure display chains; see [ui/02-architecture.md](../ui/02-architecture.md#internationalisation). The stored value is untouched.

### Extension paths (designed for, not built)

| Future need | Change | Effort |
|-------------|--------|--------|
| New measure (volume, length, time) | add enum value + seed units | migration, small |
| Instrument-scoped units ("this beer's 24-pack") | `instrument_units (instrument_id, code, name, factor_to_default_unit)`; resolver checks instrument units before user/global | additive table + resolver branch, small |
| Unit conversion of quantity history / combined snapshot (paid) | read-time conversion through `factor_to_base`; optional materialised `holdings_converted` | service + endpoint, medium; no schema change to ledger |
| Historic factor changes (a bank redefines a lot size) | `units.valid_from` + versioned rows | additive columns, small |
| Per-user override of a global unit's decimals or symbol | `unit_prefs (user_id, unit, decimals, symbol)` | additive table, small |

**Money envelope** (ADR-017) takes `decimals` from `units` where `measure = money`. There is no separate `currencies` table.

**VND** has zero decimals and large magnitudes; `NUMERIC(24,8)` holds it comfortably (up to 10^16 whole units).

### Aggregation is per unit, by default

Quantities are summed **only within the same unit**. If a user holds 3 oz_troy of gold and later records 3 chỉ, the holding shows two lines — `3 oz_troy` and `3 chỉ` — not a converted total. The money value of each line is computed via its own price-per-unit, and money values *are* summed (after FX). Converting quantity history to one unit, or a combined quantity snapshot, is an explicit, user-triggered operation and a candidate paid feature (see product/01-features §3). This keeps the default path exact and cheap and avoids silently rewriting what the user entered.

### Changing the unit of a stream or account

`accounts.currency`, `income_streams.currency` and `instruments.default_unit` are editable at any time and affect **new** entries only. Existing postings, lots and valuations keep the unit they were recorded in. Reports group by unit; the user's default currency is applied at the money layer only.

## Core ledger

```
users
  id, default_currency (default 'USD'), timezone (default 'Asia/Ho_Chi_Minh'), locale (default 'en'; 'vi' first-class),
  personality_profile jsonb, created_at

accounts
  id, user_id, name, type, currency, valuation_mode, is_liability, liquidity (liquid | semi | illiquid),
  expected_refresh_interval interval (nullable; staleness threshold for mark_to_market),
  parent_id (nullable, for grouping), institution, external_ref, is_archived, created_at

  type            ∈ cash | bank | credit_card | loan | brokerage | crypto | real_estate |
                    vehicle | pension | private_equity | collectible | other
  valuation_mode  ∈ ledger | mark_to_market

tags                                        -- controlled taxonomy; see product/03-tags.md
  id, user_id (NULL = system), system_key (nullable; system tags only), name (custom tags only),
  root_id (NULL for roots), kind (expense | income | transfer; set on roots, inherited), 
  is_custom bool, icon, sort_order, created_at
  CHECK (is_custom → user_id IS NOT NULL AND root_id IS NOT NULL)      -- customs are children only
  CHECK (NOT is_custom → user_id IS NULL AND system_key IS NOT NULL)

tag_prefs                                   -- per-user view of system tags
  user_id, tag_id, is_hidden bool, display_name_override (nullable), sort_order
  PK (user_id, tag_id)

transactions
  id, user_id, occurred_at, booked_at, description, merchant_id (nullable),
  source (api | import | inbox | projection | system), raw_event_id (nullable),
  status (posted | projected | void), reverses_id (nullable), metadata jsonb

postings                                    -- append-only, partitioned by (user_id hash, occurred_at month)
  id, user_id, transaction_id, account_id,
  tag_id (nullable → 'untagged'), tag_confidence (exact | high | medium | low), tag_source (caller | rule | cache | merchant | keyword | knn | llm),
  labels text[] (free-form, uncontrolled),
  amount NUMERIC(24,8), currency,                       -- money moved: ALWAYS a money unit
  quantity NUMERIC(24,8) (nullable), unit (nullable),   -- stuff moved, if any: 2.5 luong, 100 share, 1 unit
  instrument_id (nullable),
  occurred_at, created_at

  Invariant: within a transaction, postings in the same currency sum to zero
             OR the transaction is explicitly single-sided against a tag
             (simple expense/income). We enforce the balanced form internally:
             a simple expense is  [asset −X] + [expense-tag +X].
```

**Why postings, not just transactions?** Splits, transfers, FX conversions, and asset purchases (cash → holding) all become trivially representable, and every balance is `SUM(amount)` over one table.

**Corrections.** A posting is never updated. To fix one: create a transaction with `reverses_id` pointing at the original (postings negated), then a new correct transaction. Rollups re-close the affected periods (see [02-ledger-and-snapshots.md](02-ledger-and-snapshots.md)).

## Merchants, rules and tagging memory

```
merchants
  id, user_id (nullable → global), canonical_name, aliases text[], website

tag_rules                                -- user-authored: exact / regex on merchant or description
  id, user_id, priority, matcher jsonb (contains / regex / amount range / account), tag_id,
  hit_count, last_hit_at

merchant_memory_user                     -- normalised merchant → tag, per user, from confirmations only
  user_id, normalised_merchant, tag_id, confirmations int, last_confirmed_at
  PK (user_id, normalised_merchant)

merchant_memory_global                   -- anonymised votes; counted after N distinct users agree
  normalised_merchant, system_key, votes int, distinct_users int, updated_at
  PK (normalised_merchant, system_key)

tag_examples                             -- confirmed (description → tag) pairs with embeddings, for kNN
  id, user_id, normalised_description, embedding vector(384), tag_id,
  weight real (1.0 confirmed · 3.0 pinned "always tag like this"), source (user | agent | llm_confirmed),
  created_at
  INDEX ivfflat/hnsw on (embedding) per user via partial or partitioned index

tag_index                                -- embeddings of the tag list itself (system + user's customs) for suggestions
  tag_id, user_id (NULL for system), text (key + names + synonyms + description), embedding vector(384)

tag_proposals                            -- LLM / low-confidence suggestions awaiting confirmation
  posting_id, user_id, candidates jsonb ([{tag_id, score, source}]), created_at, resolved_at, resolution
```

Exact fingerprint cache lives in Redis: `tagfp:{user}:{sha1(normalised_description)}` → `tag_id`, written on confirmation, TTL long. Postgres `pg_trgm` on `tags.name` / `system_key` for typo-tolerant tag lookup.

Embedding model: a small multilingual sentence model (~120 MB, 384-dim) loaded in the worker, CPU inference ~20–50 ms. `pgvector` extension.

account_aliases                          -- used by recipe `lookup` steps
  user_id, account_id, alias text (e.g. card tail '1234', 'TCB main')
  PK (user_id, alias)
```

## Annotations

```
annotations
  id, user_id, target_type (profile | account | valuation | instrument | tag | period | transaction),
  target_id (nullable for profile; period encoded as 'YYYY-MM'), body text,
  author_kind (user | agent), author_name, created_at, valid_until (nullable), tags text[]
  INDEX (user_id, target_type, target_id, created_at desc)
```

Returned alongside the object they describe in every read API; agents may create them.

## Income

```
income_streams
  id, user_id, name, account_id (where it lands), tag_id, currency,
  schedule (rrule string), amount_rule jsonb
      -- {"type":"fixed","amount":5000} | {"type":"yield","holding_id":…,"annual_rate":0.04}
  expected_variance_pct, is_active, next_expected_at

projected_income                         -- materialised expectations
  id, user_id, income_stream_id, expected_at, expected_amount, currency,
  status (pending | matched | missed | cancelled), matched_transaction_id
```

## Wealth

```
instruments                              -- anything held in quantity: a stock, SJC gold, a plot of land, a dog
  id, user_id (NULL = global reference data, e.g. listed stocks), symbol (nullable), name,
  asset_class (equity | etf | bond | crypto | fund | precious_metal | real_estate | vehicle | livestock | collectible | other),
  measure, default_unit, grade (nullable text: 'SJC 9999', '18K', 'breed: Phú Quốc'), exchange (nullable),
  pricing_currency (nullable)

holdings                                 -- derived, cached: position per account × instrument × unit
  user_id, account_id, instrument_id, unit, quantity, cost_basis, currency, updated_at
  PK (user_id, account_id, instrument_id, unit)      -- per-unit aggregation: 3 oz_troy and 3 chi are two rows

lots                                     -- for cost-basis / realised gains
  id, user_id, account_id, instrument_id, acquired_at, quantity_open, quantity_original, unit,
  unit_cost, currency, opening_posting_id

prices                                   -- money per unit of an instrument
  instrument_id, as_of, price NUMERIC(24,8), currency, per_unit, source (manual | feed:<name>)
  PK (instrument_id, as_of, per_unit, source)
  e.g. SJC gold: 89,500,000 VND per luong · VNM: 65,200 VND per share

valuations                               -- point-in-time value for whole mark-to-market accounts (house, car)
  id, user_id, account_id, as_of, value NUMERIC(24,8), currency,
  source (manual | feed:<name> | import), note text, created_at
```

Valuing a holding: `quantity` in `unit` → convert to the price's `per_unit` via `units.factor_to_base` (same measure) → × `price` → money in `pricing currency` → FX to the user's default currency if requested. Every step is echoed in the response (`quantity`, `price_used`, `fx`).

`staleness` is not stored; it is computed at read time as `now() − as_of` of the latest price or valuation and returned with the value. Accounts and instruments may declare `expected_refresh_interval` so the staleness alert threshold is per asset (a house: 12 months; gold: 1 day).

## FX

```
fx_rates
  base, quote, as_of date, rate NUMERIC(24,10), source
  PK (base, quote, as_of)
```

Always convert `native → user.default_currency` at read time using the rate for the posting's `occurred_at::date` (historical) or `max(as_of)` (current).

## Ingestion

```
raw_events                               -- immutable, everything that ever arrived
  id, user_id, received_at, source text (nullable free-form hint: 'email:tcb.com.vn', 'sms:TCB', 'api', 'manual'),
  text (length-capped), payload jsonb (nullable, for structured/api events), blob_ref (nullable), content_hash, fingerprint,
  processing_status (queued | committed | duplicate | review | failed),
  recipe_id (nullable), recipe_version (nullable), transaction_id (nullable)
  UNIQUE (user_id, content_hash)

recipes
  id, user_id (nullable → community), name, fingerprint jsonb (source_prefix?, anchors[]), emit_schema ('transaction.v1'),
  is_active, created_by (user | compiler:<version>), created_at
recipe_versions
  recipe_id, version int, body jsonb (DSL program), compiler_prompt_version (nullable),
  published_at, notes
  PK (recipe_id, version)
recipe_fixtures
  id, recipe_id, raw_event_id, expected jsonb, last_run_at, last_result (pass | fail)

review_items
  id, user_id, raw_event_id, recipe_id (nullable), recipe_version (nullable),
  partial jsonb (fields extracted so far), reason (no_recipe | schema_failed | ambiguous_match | recipe_timeout),
  reason_detail text, status (open | accepted | edited | rejected), resolved_at

compiler_runs                            -- audit of every LLM call
  id, user_id, raw_event_id, prompt_version, model, tokens_in, tokens_out, latency_ms,
  outcome (recipe_saved | failed_validation | failed_fixture | error), recipe_id (nullable), created_at
```

## Budgets & goals

```
budgets
  id, user_id, tag_id, period (month | week | year | custom rrule), amount, currency, rollover bool

goals
  id, user_id, name, target_amount, currency, target_date, linked_account_ids, status
```

## Aggregates (see 02)

```
period_rollups, balance_snapshots, networth_snapshots
```

## Interactions and identities (see 07)

```
interactions   pending questions for the user with ≤ 6 options; rendered/resolved by any channel
identities     (provider, provider_id) → user_id; telegram first, google via console later
link_codes     single-use codes to attach a new identity to an existing user
```

## Metering, plans, auth (see 06 and ui/)

```
usage_events, usage_rollups, plans, user_plans, grants, admin_audit
api_keys      id, user_id, kind (user | admin), scopes text[], hashed_key, last_used_at, expires_at, revoked_at
webhooks      id, user_id, url, events text[], secret, is_active, failure_count
sessions      browser sessions for the console (or stateless JWT + refresh tokens)
```

## Indexing notes

- `postings (user_id, account_id, occurred_at)` and `(user_id, tag_id, occurred_at)` — the two hot access paths.
- `postings` partitioned by month (range on `occurred_at`) so old partitions can be compressed/detached; sub-partition by `user_id` hash only if a tenant becomes huge.
- `raw_events (user_id, content_hash)` unique — cheap idempotency for re-sent imports; `(user_id, fingerprint)` for recipe lookup.
- `raw_events.payload` is stored compressed (TOAST handles it); no GIN unless we query inside it.
- Single-instance efficiency: keep row counts honest — rollups and snapshots are the only tables reports touch; `postings` is read for drill-down and re-close only.
