-- =============================================================================
-- Finassis — PostgreSQL 16 schema
-- Hand-written init schema; the first Alembic migration is generated from this
-- file. Afterwards Alembic is the source of truth and this file is re-rendered
-- with `make db-render`. Design notes: docs/tech/08-database.md
-- Requires: pgvector >= 0.7, pg_trgm, unaccent, btree_gist, pgcrypto, pg_stat_statements
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 0. Extensions
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- -----------------------------------------------------------------------------
-- 1. Roles (idempotent; passwords set out-of-band)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'finassis_owner') THEN
    CREATE ROLE finassis_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'finassis_app') THEN
    CREATE ROLE finassis_app LOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'finassis_admin') THEN
    CREATE ROLE finassis_admin LOGIN BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'finassis_readonly') THEN
    CREATE ROLE finassis_readonly LOGIN BYPASSRLS;
  END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 2. Helper functions
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION current_user_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- unaccent is STABLE; wrap as IMMUTABLE for generated columns / indexes
CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
  SELECT public.unaccent('public.unaccent', $1)
$$;

CREATE OR REPLACE FUNCTION norm_text(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
  SELECT regexp_replace(lower(immutable_unaccent($1)), '\s+', ' ', 'g')
$$;

-- -----------------------------------------------------------------------------
-- 3. Domains (text enums; extend via ALTER DOMAIN in migrations)
-- -----------------------------------------------------------------------------
CREATE DOMAIN d_measure         AS text CHECK (VALUE IN ('money','mass','count','area'));
CREATE DOMAIN d_tag_kind        AS text CHECK (VALUE IN ('expense','income','off_report'));
CREATE DOMAIN d_tag_default     AS text CHECK (VALUE IN ('on','off','sys'));
CREATE DOMAIN d_account_type    AS text CHECK (VALUE IN (
  'cash','bank','term_deposit','credit_card','loan','brokerage','crypto','real_estate',
  'vehicle','pension','private_equity','receivable','payable','collectible','other'));
CREATE DOMAIN d_valuation_mode  AS text CHECK (VALUE IN ('ledger','mark_to_market'));
CREATE DOMAIN d_liquidity       AS text CHECK (VALUE IN ('liquid','semi','illiquid'));
CREATE DOMAIN d_purpose         AS text CHECK (VALUE IN (
  'emergency_fund','retirement','education','house','vehicle','travel','general','business'));
CREATE DOMAIN d_txn_source      AS text CHECK (VALUE IN ('api','raw','projection','system'));
CREATE DOMAIN d_txn_status      AS text CHECK (VALUE IN ('posted','projected','void'));
CREATE DOMAIN d_tag_source      AS text CHECK (VALUE IN ('caller','rule','cache','merchant','keyword','knn','llm'));
CREATE DOMAIN d_confidence      AS text CHECK (VALUE IN ('exact','high','medium','low'));
CREATE DOMAIN d_asset_class     AS text CHECK (VALUE IN (
  'equity','etf','bond','fund','crypto','precious_metal','real_estate','vehicle','livestock','collectible','other'));
CREATE DOMAIN d_price_source    AS text CHECK (VALUE ~ '^(manual|import|feed:[a-z0-9_]+)$');
CREATE DOMAIN d_raw_status      AS text CHECK (VALUE IN ('queued','committed','duplicate','review','failed'));
CREATE DOMAIN d_review_reason   AS text CHECK (VALUE IN ('no_recipe','schema_failed','ambiguous_match','recipe_timeout'));
CREATE DOMAIN d_review_status   AS text CHECK (VALUE IN ('open','accepted','edited','rejected'));
CREATE DOMAIN d_proj_status     AS text CHECK (VALUE IN ('pending','matched','missed','cancelled'));
CREATE DOMAIN d_interaction_kind AS text CHECK (VALUE IN (
  'tag_proposal','review_item','unknown_tag','recipe_confirm','stale_valuation','alert',
  'onboarding_step','allowance_warning','confirm_action'));
CREATE DOMAIN d_interaction_status AS text CHECK (VALUE IN ('open','resolved','expired','dismissed'));
CREATE DOMAIN d_priority        AS text CHECK (VALUE IN ('now','today','digest'));
CREATE DOMAIN d_channel         AS text CHECK (VALUE IN ('telegram','console','mcp','api'));
CREATE DOMAIN d_identity_provider AS text CHECK (VALUE IN ('telegram','google','email'));
CREATE DOMAIN d_key_kind        AS text CHECK (VALUE IN ('user','admin','channel'));
CREATE DOMAIN d_grant_kind      AS text CHECK (VALUE IN ('donation','promo','admin','referral'));
CREATE DOMAIN d_period_kind     AS text CHECK (VALUE IN ('day','month'));
CREATE DOMAIN d_annotation_target AS text CHECK (VALUE IN (
  'profile','account','valuation','instrument','tag','period','transaction'));
CREATE DOMAIN d_author_kind     AS text CHECK (VALUE IN ('user','agent'));
CREATE DOMAIN d_budget_period   AS text CHECK (VALUE IN ('week','month','year','custom'));
CREATE DOMAIN d_goal_status     AS text CHECK (VALUE IN ('active','reached','abandoned'));
CREATE DOMAIN d_locale          AS text CHECK (VALUE ~ '^[a-z]{2}(-[A-Z]{2})?$');
CREATE DOMAIN d_currency        AS text CHECK (VALUE ~ '^[A-Z][A-Z0-9_]{1,15}$');
CREATE DOMAIN d_money           AS numeric(24,8);
CREATE DOMAIN d_qty             AS numeric(24,8);

-- -----------------------------------------------------------------------------
-- 4. Reference data (seeded; ADR-025)
-- -----------------------------------------------------------------------------
CREATE TABLE seed_versions (
  file          text PRIMARY KEY,
  version       int  NOT NULL,
  content_hash  text NOT NULL,
  applied_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE units (
  code            text PRIMARY KEY,
  measure         d_measure NOT NULL,
  factor_to_base  numeric(30,15),
  decimals        smallint NOT NULL CHECK (decimals BETWEEN 0 AND 15),
  symbol          text,
  name            text,                       -- user-defined units only
  user_id         uuid,                       -- NULL = global (FK added after users)
  is_system       boolean NOT NULL DEFAULT false,
  is_deprecated   boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT units_money_factor_check CHECK ((measure = 'money') = (factor_to_base IS NULL)),
  CONSTRAINT units_user_name_check    CHECK (user_id IS NULL OR name IS NOT NULL)
);

CREATE TABLE fx_rates (
  base      d_currency NOT NULL REFERENCES units(code),
  quote     d_currency NOT NULL REFERENCES units(code),
  as_of     date NOT NULL,
  rate      numeric(24,10) NOT NULL CHECK (rate > 0),
  source    text NOT NULL,
  PRIMARY KEY (base, quote, as_of)
);

-- -----------------------------------------------------------------------------
-- 5. Tenancy, identity, plans
-- -----------------------------------------------------------------------------
CREATE TABLE users (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  default_currency   d_currency NOT NULL DEFAULT 'USD' REFERENCES units(code),
  timezone           text NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
  locale             d_locale NOT NULL DEFAULT 'en',
  display_name       text,
  personality_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_admin           boolean NOT NULL DEFAULT false,
  is_paused          boolean NOT NULL DEFAULT false,
  deletion_scheduled_at timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE units ADD CONSTRAINT units_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX units_user_id_idx ON units(user_id) WHERE user_id IS NOT NULL;

CREATE TABLE identities (
  provider     d_identity_provider NOT NULL,
  provider_id  text NOT NULL,
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  display_name text,
  is_primary   boolean NOT NULL DEFAULT false,
  linked_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, provider_id)
);
CREATE INDEX identities_user_id_idx ON identities(user_id);

CREATE TABLE link_codes (
  code        text PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider    d_identity_provider NOT NULL,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid REFERENCES users(id) ON DELETE CASCADE,   -- NULL for channel keys
  kind         d_key_kind NOT NULL DEFAULT 'user',
  name         text NOT NULL,
  scopes       text[] NOT NULL DEFAULT '{}',
  key_prefix   text NOT NULL,                                  -- first 8 chars, for display/lookup
  key_hash     text NOT NULL UNIQUE,                           -- sha256
  last_used_at timestamptz,
  expires_at   timestamptz,
  revoked_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT api_keys_kind_user_check CHECK ((kind = 'channel') = (user_id IS NULL))
);
CREATE INDEX api_keys_user_id_idx ON api_keys(user_id) WHERE revoked_at IS NULL;

CREATE TABLE plans (
  id          text PRIMARY KEY,                -- 'free', 'supporter', ...
  name        text NOT NULL,
  limits      jsonb NOT NULL,                  -- {"raw.item": {"month": 2000}, ...}
  price_hint  text,
  is_default  boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX plans_single_default_idx ON plans((is_default)) WHERE is_default;

CREATE TABLE user_plans (
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_id     text NOT NULL REFERENCES plans(id),
  started_at  timestamptz NOT NULL DEFAULT now(),
  ended_at    timestamptz,
  PRIMARY KEY (user_id, started_at),
  EXCLUDE USING gist (user_id WITH =, tstzrange(started_at, ended_at, '[)') WITH &&)
);

CREATE TABLE grants (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        d_grant_kind NOT NULL,
  allowance   jsonb NOT NULL,                  -- {"compiler.call": 20}
  note        text,
  expires_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX grants_user_id_idx ON grants(user_id) WHERE expires_at IS NULL OR expires_at > now();

-- -----------------------------------------------------------------------------
-- 6. Ledger: accounts, tags, merchants, transactions, postings
-- -----------------------------------------------------------------------------
CREATE TABLE accounts (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name             text NOT NULL,
  type             d_account_type NOT NULL,
  currency         d_currency NOT NULL REFERENCES units(code),
  valuation_mode   d_valuation_mode NOT NULL DEFAULT 'ledger',
  is_liability     boolean NOT NULL DEFAULT false,
  is_system        boolean NOT NULL DEFAULT false,      -- receivable / payable auto-created
  liquidity        d_liquidity NOT NULL DEFAULT 'liquid',
  purpose          d_purpose,
  labels           text[] NOT NULL DEFAULT '{}',
  terms            jsonb,                                -- yield-bearing accounts: rate, term, maturity, payout
  expected_refresh_interval interval,
  parent_id        uuid REFERENCES accounts(id) ON DELETE SET NULL,
  institution      text,
  external_ref     text,
  is_archived      boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX accounts_user_id_idx ON accounts(user_id) WHERE NOT is_archived;
CREATE UNIQUE INDEX accounts_user_system_type_idx ON accounts(user_id, type) WHERE is_system;
CREATE TRIGGER accounts_updated_at BEFORE UPDATE ON accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE account_aliases (
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  alias       text NOT NULL,                              -- card tail '1234', 'tcb-main'
  account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, alias)
);

CREATE TABLE tags (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid REFERENCES users(id) ON DELETE CASCADE,  -- NULL = system
  system_key   text,                                          -- system tags only, globally unique
  name         text,                                          -- custom tags only
  root_id      uuid REFERENCES tags(id) ON DELETE RESTRICT,   -- NULL for roots
  kind         d_tag_kind,                                    -- set on roots; NULL on children (inherit)
  is_custom    boolean NOT NULL DEFAULT false,
  default_state d_tag_default,                                -- system children only
  region       text,
  is_deprecated boolean NOT NULL DEFAULT false,
  icon         text,
  sort_order   int NOT NULL DEFAULT 0,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tags_custom_shape_check CHECK (
    (is_custom AND user_id IS NOT NULL AND root_id IS NOT NULL AND name IS NOT NULL AND system_key IS NULL)
    OR (NOT is_custom AND user_id IS NULL AND system_key IS NOT NULL)),
  CONSTRAINT tags_root_kind_check CHECK ((root_id IS NULL) = (kind IS NOT NULL))
);
CREATE UNIQUE INDEX tags_system_key_idx ON tags(system_key) WHERE system_key IS NOT NULL;
CREATE UNIQUE INDEX tags_user_root_name_idx ON tags(user_id, root_id, lower(name)) WHERE is_custom;
CREATE INDEX tags_root_id_idx ON tags(root_id);
CREATE INDEX tags_name_trgm_idx ON tags USING gin (name gin_trgm_ops) WHERE name IS NOT NULL;

CREATE TABLE tag_keywords (                      -- flattened from seeds for the keyword dictionary
  tag_id   uuid NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  keyword  text NOT NULL,
  locale   d_locale,                              -- NULL = language-neutral
  PRIMARY KEY (tag_id, keyword)
);
CREATE INDEX tag_keywords_keyword_idx ON tag_keywords(keyword);

CREATE TABLE tag_prefs (
  user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tag_id                uuid NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  is_hidden             boolean NOT NULL DEFAULT false,
  display_name_override text,
  sort_order            int,
  PRIMARY KEY (user_id, tag_id)
);

CREATE TABLE merchants (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid REFERENCES users(id) ON DELETE CASCADE,   -- NULL = global
  canonical_name  text NOT NULL,
  normalized_name text GENERATED ALWAYS AS (norm_text(canonical_name)) STORED,
  aliases         text[] NOT NULL DEFAULT '{}',
  website         text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX merchants_norm_trgm_idx ON merchants USING gin (normalized_name gin_trgm_ops);
CREATE INDEX merchants_user_id_idx ON merchants(user_id);

CREATE TABLE income_streams (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name               text NOT NULL,
  account_id         uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  tag_id             uuid NOT NULL REFERENCES tags(id),
  currency           d_currency NOT NULL REFERENCES units(code),
  schedule           text NOT NULL,                 -- RRULE
  amount_rule        jsonb NOT NULL,                -- {"type":"fixed",...}|{"type":"yield",...}|{"type":"per_unit",...}
  derived_from_account_id    uuid REFERENCES accounts(id) ON DELETE CASCADE,
  derived_from_instrument_id uuid,                  -- FK added after instruments
  expected_variance_pct numeric(5,2) NOT NULL DEFAULT 10,
  is_active          boolean NOT NULL DEFAULT true,
  next_expected_at   timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX income_streams_user_id_idx ON income_streams(user_id) WHERE is_active;
CREATE TRIGGER income_streams_updated_at BEFORE UPDATE ON income_streams FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE transactions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  occurred_at       timestamptz NOT NULL,
  booked_at         timestamptz NOT NULL DEFAULT now(),
  description       text,
  description_norm  text GENERATED ALWAYS AS (norm_text(description)) STORED,
  merchant_id       uuid REFERENCES merchants(id) ON DELETE SET NULL,
  source            d_txn_source NOT NULL DEFAULT 'api',
  status            d_txn_status NOT NULL DEFAULT 'posted',
  reverses_id       uuid REFERENCES transactions(id),
  income_stream_id  uuid REFERENCES income_streams(id) ON DELETE SET NULL,
  pair_id           uuid,                                   -- links two halves of a detected self-transfer
  labels            text[] NOT NULL DEFAULT '{}',
  idempotency_key   text,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX transactions_user_occurred_idx ON transactions(user_id, occurred_at DESC);
CREATE INDEX transactions_pair_idx ON transactions(pair_id) WHERE pair_id IS NOT NULL;
CREATE INDEX transactions_desc_trgm_idx ON transactions USING gin (description_norm gin_trgm_ops);
CREATE UNIQUE INDEX transactions_idempotency_idx ON transactions(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- postings: append-only, partitioned by month on occurred_at
CREATE TABLE postings (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  transaction_id  uuid NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  account_id      uuid REFERENCES accounts(id),           -- NULL on a pure tag leg (the "equity" side of an expense/income)
  tag_id          uuid REFERENCES tags(id),               -- NULL = untagged; an account leg may also carry a tag (transfers tag both legs)
  amount          d_money NOT NULL,
  currency        d_currency NOT NULL REFERENCES units(code),
  quantity        d_qty,
  unit            text REFERENCES units(code),
  instrument_id   uuid,                                    -- FK added after instruments
  tag_source      d_tag_source,
  tag_confidence  d_confidence,
  occurred_at     timestamptz NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, occurred_at),
  CONSTRAINT postings_side_check CHECK (account_id IS NOT NULL OR tag_id IS NOT NULL),
  CONSTRAINT postings_qty_unit_check CHECK ((quantity IS NULL) = (unit IS NULL))
) PARTITION BY RANGE (occurred_at);
CREATE TABLE postings_default PARTITION OF postings DEFAULT;
CREATE INDEX postings_user_account_occurred_idx ON postings(user_id, account_id, occurred_at) INCLUDE (amount, currency);
CREATE INDEX postings_user_tag_occurred_idx     ON postings(user_id, tag_id, occurred_at);
CREATE INDEX postings_user_amount_occurred_idx  ON postings(user_id, amount, occurred_at);
CREATE INDEX postings_transaction_idx           ON postings(transaction_id);

-- -----------------------------------------------------------------------------
-- 7. Tagging memory
-- -----------------------------------------------------------------------------
CREATE TABLE tag_rules (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  priority     int NOT NULL DEFAULT 100,
  matcher      jsonb NOT NULL,                 -- {"contains": "...", "regex": "...", "account_id": ..., "amount_min": ...}
  tag_id       uuid NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  hit_count    int NOT NULL DEFAULT 0,
  last_hit_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tag_rules_user_priority_idx ON tag_rules(user_id, priority);

CREATE TABLE merchant_memory_user (
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  normalized_merchant text NOT NULL,
  tag_id              uuid NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  confirmations       int NOT NULL DEFAULT 1,
  last_confirmed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, normalized_merchant)
);

CREATE TABLE merchant_memory_global (
  normalized_merchant text NOT NULL,
  system_key          text NOT NULL,
  votes               int NOT NULL DEFAULT 0,
  distinct_users      int NOT NULL DEFAULT 0,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (normalized_merchant, system_key)
);

CREATE TABLE tag_examples (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                 uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  normalized_description  text NOT NULL,
  embedding               vector(384) NOT NULL,
  tag_id                  uuid NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  weight                  real NOT NULL DEFAULT 1.0,       -- 3.0 = pinned "always tag like this"
  source                  text NOT NULL CHECK (source IN ('user','agent','llm_confirmed')),
  created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tag_examples_user_idx ON tag_examples(user_id);
CREATE INDEX tag_examples_embedding_idx ON tag_examples USING hnsw (embedding vector_cosine_ops);

CREATE TABLE tag_index (
  tag_id     uuid PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
  user_id    uuid REFERENCES users(id) ON DELETE CASCADE,   -- NULL for system tags
  text       text NOT NULL,
  embedding  vector(384) NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tag_index_embedding_idx ON tag_index USING hnsw (embedding vector_cosine_ops);

CREATE TABLE tag_proposals (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  transaction_id  uuid NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  candidates      jsonb NOT NULL,                -- [{"tag_id":..., "score":0.81, "source":"knn"}]
  created_at      timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz,
  resolution      jsonb
);
CREATE INDEX tag_proposals_open_idx ON tag_proposals(user_id, created_at) WHERE resolved_at IS NULL;

-- -----------------------------------------------------------------------------
-- 8. Income projections, budgets, goals
-- -----------------------------------------------------------------------------
CREATE TABLE projected_income (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  income_stream_id    uuid NOT NULL REFERENCES income_streams(id) ON DELETE CASCADE,
  expected_at         timestamptz NOT NULL,
  expected_amount     d_money NOT NULL,
  currency            d_currency NOT NULL REFERENCES units(code),
  status              d_proj_status NOT NULL DEFAULT 'pending',
  matched_transaction_id uuid REFERENCES transactions(id) ON DELETE SET NULL,
  UNIQUE (income_stream_id, expected_at)
);
CREATE INDEX projected_income_user_pending_idx ON projected_income(user_id, expected_at) WHERE status = 'pending';

CREATE TABLE budgets (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tag_id      uuid NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  period      d_budget_period NOT NULL DEFAULT 'month',
  rrule       text,                                   -- when period = custom
  amount      d_money NOT NULL CHECK (amount > 0),
  currency    d_currency NOT NULL REFERENCES units(code),
  rollover    boolean NOT NULL DEFAULT false,
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, tag_id, period)
);

CREATE TABLE goals (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name                text NOT NULL,
  target_amount       d_money NOT NULL,
  currency            d_currency NOT NULL REFERENCES units(code),
  target_date         date,
  linked_account_ids  uuid[] NOT NULL DEFAULT '{}',
  status              d_goal_status NOT NULL DEFAULT 'active',
  created_at          timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 9. Wealth: instruments, prices, holdings, lots, valuations
-- -----------------------------------------------------------------------------
CREATE TABLE instruments (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid REFERENCES users(id) ON DELETE CASCADE,   -- NULL = global reference (listed stocks)
  symbol            text,
  name              text NOT NULL,
  asset_class       d_asset_class NOT NULL,
  measure           d_measure NOT NULL,
  default_unit      text NOT NULL REFERENCES units(code),
  grade             text,                                            -- 'SJC 9999', '18K', breed...
  exchange          text,
  pricing_currency  d_currency REFERENCES units(code),
  expected_refresh_interval interval,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX instruments_global_symbol_idx ON instruments(exchange, symbol) WHERE user_id IS NULL AND symbol IS NOT NULL;
CREATE INDEX instruments_user_id_idx ON instruments(user_id);

ALTER TABLE postings       ADD CONSTRAINT postings_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES instruments(id);
ALTER TABLE income_streams ADD CONSTRAINT income_streams_instrument_fkey FOREIGN KEY (derived_from_instrument_id) REFERENCES instruments(id) ON DELETE CASCADE;

CREATE TABLE prices (
  instrument_id  uuid NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  as_of          timestamptz NOT NULL,
  price          d_money NOT NULL CHECK (price >= 0),
  currency       d_currency NOT NULL REFERENCES units(code),
  per_unit       text NOT NULL REFERENCES units(code),
  source         d_price_source NOT NULL,
  PRIMARY KEY (instrument_id, as_of, per_unit, source)
);

CREATE TABLE holdings (                              -- derived cache; per unit, never auto-converted
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id     uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  instrument_id  uuid NOT NULL REFERENCES instruments(id),
  unit           text NOT NULL REFERENCES units(code),
  quantity       d_qty NOT NULL,
  cost_basis     d_money NOT NULL DEFAULT 0,
  currency       d_currency NOT NULL REFERENCES units(code),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, account_id, instrument_id, unit)
);

CREATE TABLE lots (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  instrument_id       uuid NOT NULL REFERENCES instruments(id),
  acquired_at         timestamptz NOT NULL,
  quantity_original   d_qty NOT NULL CHECK (quantity_original > 0),
  quantity_open       d_qty NOT NULL CHECK (quantity_open >= 0),
  unit                text NOT NULL REFERENCES units(code),
  unit_cost           d_money NOT NULL,
  currency            d_currency NOT NULL REFERENCES units(code),
  opening_posting_id  bigint,                          -- no FK: postings is partitioned
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX lots_open_idx ON lots(user_id, account_id, instrument_id, acquired_at) WHERE quantity_open > 0;

CREATE TABLE valuations (                            -- whole-account mark-to-market (house, car)
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  as_of       timestamptz NOT NULL,
  value       d_money NOT NULL,
  currency    d_currency NOT NULL REFERENCES units(code),
  source      d_price_source NOT NULL,
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (account_id, as_of, source)
);
CREATE INDEX valuations_account_latest_idx ON valuations(account_id, as_of DESC);

-- -----------------------------------------------------------------------------
-- 10. Annotations
-- -----------------------------------------------------------------------------
CREATE TABLE annotations (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_type  d_annotation_target NOT NULL,
  target_id    text,                                 -- uuid as text, or 'YYYY-MM' for period, NULL for profile
  body         text NOT NULL,
  author_kind  d_author_kind NOT NULL,
  author_name  text,
  tags         text[] NOT NULL DEFAULT '{}',
  valid_until  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX annotations_target_idx ON annotations(user_id, target_type, target_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- 11. Ingestion: raw events, recipes, review, compiler
-- -----------------------------------------------------------------------------
CREATE TABLE recipes (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid REFERENCES users(id) ON DELETE CASCADE,   -- NULL = community
  name          text NOT NULL,
  fingerprint   jsonb NOT NULL,                                 -- {"source_prefix": "...", "anchors": [...]}
  emit_schema   text NOT NULL DEFAULT 'transaction.v1',
  is_active     boolean NOT NULL DEFAULT true,
  created_by    text NOT NULL,                                  -- 'user' | 'compiler:<version>' | 'import:<recipe_id>'
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recipes_user_active_idx ON recipes(user_id) WHERE is_active;

CREATE TABLE recipe_versions (
  recipe_id                uuid NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  version                  int NOT NULL,
  body                     jsonb NOT NULL,             -- DSL program
  compiler_prompt_version  text,
  notes                    text,
  published_at             timestamptz NOT NULL DEFAULT now(),
  is_disabled              boolean NOT NULL DEFAULT false,
  PRIMARY KEY (recipe_id, version)
);

-- raw_events: partitioned by month on received_at
CREATE TABLE raw_events (
  id                 bigint GENERATED ALWAYS AS IDENTITY,
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  received_at        timestamptz NOT NULL DEFAULT now(),
  source             text,                             -- free-form hint: 'email:tcb.com.vn', 'sms:TCB', 'telegram', 'api'
  text               text,                             -- length-capped by the API; may be nulled by retention
  payload            jsonb,                            -- structured/api events
  blob_ref           text,
  content_hash       text NOT NULL,
  fingerprint        text,
  processing_status  d_raw_status NOT NULL DEFAULT 'queued',
  recipe_id          uuid REFERENCES recipes(id) ON DELETE SET NULL,
  recipe_version     int,
  transaction_id     uuid REFERENCES transactions(id) ON DELETE SET NULL,
  error              text,
  PRIMARY KEY (id, received_at)
) PARTITION BY RANGE (received_at);
CREATE TABLE raw_events_default PARTITION OF raw_events DEFAULT;
-- idempotency: (user_id, content_hash) cannot be UNIQUE across partitions; the app checks this index before insert
CREATE INDEX raw_events_user_hash_idx ON raw_events(user_id, content_hash);
CREATE INDEX raw_events_user_fingerprint_idx ON raw_events(user_id, fingerprint);
CREATE INDEX raw_events_user_status_idx      ON raw_events(user_id, processing_status, received_at DESC);
CREATE INDEX raw_events_transaction_idx      ON raw_events(transaction_id) WHERE transaction_id IS NOT NULL;

CREATE TABLE recipe_fixtures (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recipe_id     uuid NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  raw_event_id  bigint NOT NULL,                       -- no FK: raw_events is partitioned
  expected      jsonb NOT NULL,
  last_run_at   timestamptz,
  last_result   text CHECK (last_result IN ('pass','fail')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE review_items (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  raw_event_id    bigint NOT NULL,
  recipe_id       uuid REFERENCES recipes(id) ON DELETE SET NULL,
  recipe_version  int,
  partial         jsonb NOT NULL DEFAULT '{}'::jsonb,
  reason          d_review_reason NOT NULL,
  reason_detail   text,
  status          d_review_status NOT NULL DEFAULT 'open',
  created_at      timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz
);
CREATE INDEX review_items_open_idx ON review_items(user_id, created_at) WHERE status = 'open';

CREATE TABLE compiler_runs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind            text NOT NULL CHECK (kind IN ('recipe','tag_batch')),
  raw_event_id    bigint,
  prompt_version  text NOT NULL,
  model           text NOT NULL,
  tokens_in       int NOT NULL DEFAULT 0,
  tokens_out      int NOT NULL DEFAULT 0,
  latency_ms      int,
  outcome         text NOT NULL,
  recipe_id       uuid REFERENCES recipes(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX compiler_runs_user_created_idx ON compiler_runs(user_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- 12. Interactions (pending questions) and alerts
-- -----------------------------------------------------------------------------
CREATE TABLE interactions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind             d_interaction_kind NOT NULL,
  subject_type     text,
  subject_id       text,
  prompt           jsonb NOT NULL,                       -- {"key": "...", "params": {...}}
  options          jsonb NOT NULL DEFAULT '[]'::jsonb,   -- <= 6 items
  allow_free_text  boolean NOT NULL DEFAULT false,
  free_text_hint_key text,
  priority         d_priority NOT NULL DEFAULT 'today',
  status           d_interaction_status NOT NULL DEFAULT 'open',
  resolution       jsonb,
  resolved_via     d_channel,
  awaiting_free_text_until timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL DEFAULT now() + interval '14 days',
  resolved_at      timestamptz,
  CONSTRAINT interactions_options_len_check CHECK (jsonb_array_length(options) <= 6)
);
CREATE INDEX interactions_open_idx ON interactions(user_id, priority, created_at) WHERE status = 'open';
CREATE INDEX interactions_subject_idx ON interactions(user_id, subject_type, subject_id) WHERE status = 'open';

CREATE TABLE alerts (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         text NOT NULL,                             -- spend_spike, duplicate_charge, income_missed, ...
  params       jsonb NOT NULL DEFAULT '{}'::jsonb,
  severity     text NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warn','critical')),
  interaction_id uuid REFERENCES interactions(id) ON DELETE SET NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  acknowledged_at timestamptz
);
CREATE INDEX alerts_user_open_idx ON alerts(user_id, created_at DESC) WHERE acknowledged_at IS NULL;

-- -----------------------------------------------------------------------------
-- 13. Aggregates (docs/tech/02)
-- -----------------------------------------------------------------------------
CREATE TABLE period_rollups (
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period_kind    d_period_kind NOT NULL,
  period_start   date NOT NULL,
  account_id     uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',   -- sentinel = tag-only leg
  tag_id         uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',   -- sentinel = untagged leg
  currency       d_currency NOT NULL,
  debit          d_money NOT NULL DEFAULT 0,
  credit         d_money NOT NULL DEFAULT 0,
  net            d_money NOT NULL DEFAULT 0,
  posting_count  int NOT NULL DEFAULT 0,
  closed_at      timestamptz,
  PRIMARY KEY (user_id, period_kind, period_start, account_id, tag_id, currency)
);
-- PK columns cannot be NULL; the all-zero uuid is the application's sentinel for "no account" / "no tag".

CREATE TABLE balance_snapshots (
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id       uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  currency         d_currency NOT NULL,
  as_of            date NOT NULL,
  balance          d_money NOT NULL,
  quantity         d_qty,
  last_posting_id  bigint NOT NULL,
  PRIMARY KEY (user_id, account_id, currency, as_of)
);

CREATE TABLE networth_snapshots (
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  as_of              date NOT NULL,
  currency           d_currency NOT NULL,
  total_assets       d_money NOT NULL,
  total_liabilities  d_money NOT NULL,
  net_worth          d_money NOT NULL,
  breakdown          jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (user_id, as_of)
);

CREATE TABLE dirty_periods (
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period_kind   d_period_kind NOT NULL,
  period_start  date NOT NULL,
  marked_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, period_kind, period_start)
);

-- -----------------------------------------------------------------------------
-- 14. Metering, webhooks, admin audit (docs/tech/06)
-- -----------------------------------------------------------------------------
CREATE TABLE usage_events (
  id           bigint GENERATED ALWAYS AS IDENTITY,
  user_id      uuid NOT NULL,
  kind         text NOT NULL,
  quantity     numeric(20,4) NOT NULL DEFAULT 1,
  unit         text NOT NULL DEFAULT 'count',
  ref_id       text,
  occurred_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE TABLE usage_events_default PARTITION OF usage_events DEFAULT;
CREATE INDEX usage_events_user_kind_idx ON usage_events(user_id, kind, occurred_at);

CREATE TABLE usage_rollups (
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind          text NOT NULL,
  period_kind   d_period_kind NOT NULL,
  period_start  date NOT NULL,
  quantity      numeric(20,4) NOT NULL DEFAULT 0,
  closed_at     timestamptz,
  PRIMARY KEY (user_id, kind, period_kind, period_start)
);

CREATE TABLE webhooks (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  url            text NOT NULL,
  events         text[] NOT NULL,
  secret         text NOT NULL,
  is_active      boolean NOT NULL DEFAULT true,
  failure_count  int NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX webhooks_user_active_idx ON webhooks(user_id) WHERE is_active;

CREATE TABLE webhook_deliveries (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  webhook_id    uuid NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
  event         text NOT NULL,
  payload       jsonb NOT NULL,
  attempt       int NOT NULL DEFAULT 1,
  status_code   int,
  error         text,
  delivered_at  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX webhook_deliveries_webhook_idx ON webhook_deliveries(webhook_id, created_at DESC);

CREATE TABLE admin_audit (
  id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor     text NOT NULL,                       -- api key id or 'system'
  action    text NOT NULL,
  target    text,
  payload   jsonb,
  at        timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 15. Integrity triggers
-- -----------------------------------------------------------------------------
-- postings are immutable for the app role
CREATE OR REPLACE FUNCTION postings_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF current_user IN ('finassis_admin', 'finassis_owner')
     OR pg_has_role(current_user, 'finassis_owner', 'member')
     OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
  END IF;
  RAISE EXCEPTION 'postings are append-only; record a reversal instead' USING ERRCODE = 'integrity_constraint_violation';
END $$;
CREATE TRIGGER postings_immutable_trg BEFORE UPDATE OR DELETE ON postings
  FOR EACH ROW EXECUTE FUNCTION postings_immutable();

-- mark dirty periods on insert (also on admin delete, for re-close)
CREATE OR REPLACE FUNCTION mark_dirty_period() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE r RECORD; d date;
BEGIN
  IF TG_OP = 'DELETE' THEN r := OLD; ELSE r := NEW; END IF;
  d := (r.occurred_at AT TIME ZONE 'UTC')::date;   -- bucket in UTC; job re-buckets per user tz
  INSERT INTO dirty_periods(user_id, period_kind, period_start) VALUES
    (r.user_id, 'day',   d),
    (r.user_id, 'month', date_trunc('month', d)::date)
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;
CREATE TRIGGER postings_dirty_trg AFTER INSERT OR DELETE ON postings
  FOR EACH ROW EXECUTE FUNCTION mark_dirty_period();

-- currency/unit columns must reference money units
CREATE OR REPLACE FUNCTION check_money_unit() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE m text;
BEGIN
  SELECT measure INTO m FROM units WHERE code = NEW.currency;
  IF m IS DISTINCT FROM 'money' THEN
    RAISE EXCEPTION 'currency % is not a money unit', NEW.currency USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER accounts_money_unit_trg  BEFORE INSERT OR UPDATE OF currency ON accounts  FOR EACH ROW EXECUTE FUNCTION check_money_unit();
CREATE TRIGGER postings_money_unit_trg  BEFORE INSERT ON postings                       FOR EACH ROW EXECUTE FUNCTION check_money_unit();
CREATE OR REPLACE FUNCTION check_user_default_currency() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE m text;
BEGIN
  SELECT measure INTO m FROM units WHERE code = NEW.default_currency;
  IF m IS DISTINCT FROM 'money' THEN
    RAISE EXCEPTION 'default_currency % is not a money unit', NEW.default_currency USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER users_default_currency_trg BEFORE INSERT OR UPDATE OF default_currency ON users
  FOR EACH ROW EXECUTE FUNCTION check_user_default_currency();

-- a posting may reference a root tag except the off_report root; custom tags must belong to the user
CREATE OR REPLACE FUNCTION check_posting_tag() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE t RECORD;
BEGIN
  IF NEW.tag_id IS NULL THEN RETURN NEW; END IF;
  SELECT id, user_id, root_id, kind, is_deprecated INTO t FROM tags WHERE id = NEW.tag_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown tag %', NEW.tag_id; END IF;
  IF t.user_id IS NOT NULL AND t.user_id <> NEW.user_id THEN
    RAISE EXCEPTION 'tag % belongs to another user', NEW.tag_id USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF t.root_id IS NULL AND t.kind = 'off_report' THEN
    RAISE EXCEPTION 'off_report root cannot be used directly; pick a child' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER postings_tag_trg BEFORE INSERT ON postings FOR EACH ROW EXECUTE FUNCTION check_posting_tag();

-- deferred: postings of a transaction balance per currency
CREATE OR REPLACE FUNCTION check_transaction_balanced() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE bad RECORD;
BEGIN
  SELECT currency, sum(amount) AS total INTO bad
  FROM postings WHERE transaction_id = NEW.transaction_id
  GROUP BY currency HAVING sum(amount) <> 0 LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'transaction % unbalanced in %: %', NEW.transaction_id, bad.currency, bad.total
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER postings_balanced_trg AFTER INSERT ON postings
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_transaction_balanced();

-- -----------------------------------------------------------------------------
-- 16. Partition management
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ensure_partitions(months_ahead int DEFAULT 3) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  t text; m date; nm date; part text;
BEGIN
  FOREACH t IN ARRAY ARRAY['postings','raw_events','usage_events'] LOOP
    m := date_trunc('month', now())::date - interval '1 month';
    FOR i IN 0..(months_ahead + 1) LOOP
      nm := (m + interval '1 month')::date;
      part := format('%s_%s', t, to_char(m, 'YYYY_MM'));
      IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
        EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', part, t, m, nm);
      END IF;
      m := nm;
    END LOOP;
  END LOOP;
END $$;
SELECT ensure_partitions(3);

-- -----------------------------------------------------------------------------
-- 17. Row-level security
-- -----------------------------------------------------------------------------
-- strict tenant tables: user_id NOT NULL
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'identities','link_codes','user_plans','grants','accounts','account_aliases','tag_prefs',
    'income_streams','transactions','postings','tag_rules','merchant_memory_user','tag_examples',
    'tag_proposals','projected_income','budgets','goals','holdings','lots','valuations','annotations',
    'raw_events','review_items','compiler_runs','interactions','alerts','period_rollups',
    'balance_snapshots','networth_snapshots','dirty_periods','usage_events','usage_rollups','webhooks'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
      USING (user_id = current_user_id()) WITH CHECK (user_id = current_user_id())$p$, t);
  END LOOP;
END $$;

-- users: a session sees only its own row. Creating a user (registration, bootstrap) is a privileged
-- operation performed on a finassis_admin connection, since no app.user_id exists yet.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
CREATE POLICY self ON users USING (id = current_user_id()) WITH CHECK (id = current_user_id());

-- mixed global/per-user reference tables: read global + own, write own
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['units','tags','merchants','instruments','recipes','tag_index'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY read_global_or_own ON %I FOR SELECT
      USING (user_id IS NULL OR user_id = current_user_id())$p$, t);
    EXECUTE format($p$CREATE POLICY write_own ON %I FOR ALL
      USING (user_id = current_user_id()) WITH CHECK (user_id = current_user_id())$p$, t);
  END LOOP;
END $$;

-- child tables without user_id: scope through parent
ALTER TABLE recipe_versions ENABLE ROW LEVEL SECURITY; ALTER TABLE recipe_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY via_recipe ON recipe_versions
  USING (EXISTS (SELECT 1 FROM recipes r WHERE r.id = recipe_id AND (r.user_id IS NULL OR r.user_id = current_user_id())))
  WITH CHECK (EXISTS (SELECT 1 FROM recipes r WHERE r.id = recipe_id AND r.user_id = current_user_id()));
ALTER TABLE recipe_fixtures ENABLE ROW LEVEL SECURITY; ALTER TABLE recipe_fixtures FORCE ROW LEVEL SECURITY;
CREATE POLICY via_recipe ON recipe_fixtures
  USING (EXISTS (SELECT 1 FROM recipes r WHERE r.id = recipe_id AND (r.user_id IS NULL OR r.user_id = current_user_id())))
  WITH CHECK (EXISTS (SELECT 1 FROM recipes r WHERE r.id = recipe_id AND r.user_id = current_user_id()));
ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY; ALTER TABLE webhook_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY via_webhook ON webhook_deliveries
  USING (EXISTS (SELECT 1 FROM webhooks w WHERE w.id = webhook_id AND w.user_id = current_user_id()));
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY; ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY own_keys ON api_keys USING (user_id = current_user_id()) WITH CHECK (user_id = current_user_id());
ALTER TABLE prices ENABLE ROW LEVEL SECURITY; ALTER TABLE prices FORCE ROW LEVEL SECURITY;
CREATE POLICY via_instrument ON prices
  USING (EXISTS (SELECT 1 FROM instruments i WHERE i.id = instrument_id AND (i.user_id IS NULL OR i.user_id = current_user_id())))
  WITH CHECK (EXISTS (SELECT 1 FROM instruments i WHERE i.id = instrument_id AND i.user_id = current_user_id()));

-- global-only tables: no RLS; app role read-only
-- fx_rates, merchant_memory_global, plans, seed_versions, tag_keywords, admin_audit

-- -----------------------------------------------------------------------------
-- 18. Grants
-- -----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO finassis_app, finassis_admin, finassis_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO finassis_app, finassis_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO finassis_readonly;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO finassis_app, finassis_admin;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO finassis_app, finassis_admin;
REVOKE EXECUTE ON FUNCTION ensure_partitions(int) FROM finassis_app, PUBLIC;
-- app role may not touch global-only tables except read
REVOKE INSERT, UPDATE, DELETE ON fx_rates, merchant_memory_global, plans, seed_versions, tag_keywords, admin_audit FROM finassis_app;
-- admin_audit is append-only even for admin
REVOKE UPDATE, DELETE ON admin_audit FROM finassis_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO finassis_app, finassis_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO finassis_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO finassis_app, finassis_admin;

ALTER ROLE finassis_readonly SET statement_timeout = '10s';
ALTER ROLE finassis_app      SET statement_timeout = '30s';
ALTER ROLE finassis_app      SET idle_in_transaction_session_timeout = '60s';

COMMIT;
