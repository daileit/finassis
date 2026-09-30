# Database design

PostgreSQL 16 is the only durable store. This page fixes the choices the DDL in [`api/db/schema.sql`](../../api/db/schema.sql) relies on. Table-by-table semantics are in [01-data-model.md](01-data-model.md); aggregates in [02-ledger-and-snapshots.md](02-ledger-and-snapshots.md).

**Source of truth**: Alembic migrations under `api/alembic/`. `api/db/schema.sql` is the *rendered* full schema, kept in sync by `make db-render` (`pg_dump --schema-only` of a freshly migrated database) and diffed in CI. The first migration is generated *from* the hand-written `schema.sql` in this repo; from then on the arrow points the other way.

## Version, extensions, settings

| Item | Choice | Why |
|---|---|---|
| Version | PostgreSQL **16** | Stable; `MERGE`, better partition pruning, logical replication improvements. Not 17 yet: pgvector/managed-host coverage lagged when this was written |
| `pgvector` | ≥ 0.7 | `tag_examples.embedding`, `tag_index.embedding`; HNSW index |
| `pg_trgm` | core contrib | typo-tolerant tag/merchant lookup (`similarity`, GIN trigram indexes) |
| `unaccent` | core contrib | Vietnamese search and keyword normalisation without tone marks |
| `btree_gist` | core contrib | exclusion constraints on ranges (`user_plans` non-overlap) |
| `pg_stat_statements` | core contrib | on from day one; feeds Grafana |
| `pgcrypto` | core contrib | `digest()` for content hashes and key hashes in SQL where convenient |
| *Not used* | `uuid-ossp` (core `gen_random_uuid()` suffices), TimescaleDB (rollups are plain tables), `pg_partman` (partitions are app-managed; see below), PostGIS |

Cluster/database settings (in `postgresql.conf` or the managed-provider equivalent): `timezone = 'UTC'`, `default_text_search_config = 'pg_catalog.simple'`, `shared_preload_libraries = 'pg_stat_statements'`, `idle_in_transaction_session_timeout = '60s'`, `statement_timeout = '30s'` (overridden to `0` by jobs), `log_min_duration_statement = '500ms'`, `jit = off` (small OLTP queries; JIT hurts). Memory: size `shared_buffers` to ~25% of the VM, `work_mem` 16–32 MB, `maintenance_work_mem` 256 MB, `random_page_cost = 1.1` on SSD.

Every session from the app sets `SET LOCAL app.user_id = '<uuid>'` (or nothing, for admin/job roles) — see RLS.

## Conventions

- **Schema**: everything in `public`; one database per environment. No cross-schema indirection until needed.
- **Identifiers**: `snake_case`, singular column names, plural table names, `_id` suffix for FKs, `_at` for timestamps, `is_` for booleans.
- **Primary keys**: `uuid` (`gen_random_uuid()`) for entity tables (users, accounts, tags, transactions, recipes …). **`bigint GENERATED ALWAYS AS IDENTITY`** for append-only, high-volume rows where insertion order is meaningful: `postings`, `raw_events`, `usage_events`, `admin_audit`. Bigint is half the size, gives a monotonic watermark (`balance_snapshots.last_posting_id`), and keeps B-tree locality. Application may supply UUIDv7 for entity tables to improve locality; the default stays `gen_random_uuid()`.
- **Money**: `numeric(24,8)` everywhere; never `money`, never float. Currency/unit codes are `text` FKs to `units.code`.
- **Time**: `timestamptz` only; `date` for calendar buckets (`period_start`, `as_of` of snapshots/rates).
- **Enums**: `text` + `CHECK (col IN (...))` via named **domains**, not `CREATE TYPE ... AS ENUM`. Adding a value is `ALTER DOMAIN ... DROP/ADD CONSTRAINT` in a migration, no table rewrite, no ordering surprises.
- **Soft delete**: none. Ledger rows are immutable; corrections are reversals. Entities that must disappear use `is_archived`/`revoked_at`; tenant deletion is a hard delete of the tenant's rows.
- **`created_at`/`updated_at`**: `created_at timestamptz NOT NULL DEFAULT now()` everywhere; `updated_at` only on mutable entity tables, maintained by trigger `set_updated_at()`.
- **JSONB**: for open-ended payloads (`raw_events.payload`, `recipe_versions.body`, `interactions.options`, `accounts.terms`, `plans.limits`). No GIN on JSONB until a query needs it.
- **Naming of constraints/indexes**: `<table>_pkey`, `<table>_<cols>_key`, `<table>_<col>_fkey`, `<table>_<cols>_idx`, `<table>_<what>_check`.

## Roles and RLS

```
finassis_owner    owns all objects; used by Alembic only.               NOSUPERUSER, CREATEDB in dev
finassis_app      API + worker; RLS applies.                            NOBYPASSRLS, no DDL
finassis_admin    admin API, tenant export/delete, re-close jobs.       BYPASSRLS, DML only
finassis_readonly Grafana / ad-hoc analytics.                           BYPASSRLS, SELECT only, statement_timeout 10s
```

Row-level security is **enabled and forced** on every table with a `user_id` column. One policy shape:

```sql
CREATE POLICY tenant_isolation ON <table>
  USING      (user_id = current_setting('app.user_id', true)::uuid)
  WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
```

Reference tables mixing global and per-user rows (`tags`, `units`, `instruments`, `recipes`) get `USING (user_id IS NULL OR user_id = current_setting(...))` and a `WITH CHECK (user_id = current_setting(...))` so the app role can read system rows but only write its own. Global-only tables (`fx_rates`, `merchant_memory_global`, `seed_versions`, `plans`) have RLS off and are writable only by `finassis_admin`/owner.

The API sets `SET LOCAL app.user_id` inside each request transaction after auth. Workers iterate tenants and set it per tenant. `current_setting(..., true)` returns NULL when unset, so an app-role session without a tenant sees nothing rather than everything.

## Partitioning

Three append-only tables are **range-partitioned by month**:

| Table | Partition key | Retention |
|---|---|---|
| `postings` | `occurred_at` | forever; old partitions may be detached to cold storage |
| `raw_events` | `received_at` | text body nulled after plan retention; rows kept |
| `usage_events` | `occurred_at` | dropped after 13 months (rollups kept) |

Partitions are **created by the app**: a nightly job ensures partitions exist for the next 3 months (`ensure_partitions()` in `schema.sql` does the same in SQL for bootstrapping). A `DEFAULT` partition catches stragglers and is monitored (should stay empty). No `pg_partman` dependency.

Partitioned tables need the partition key in every unique constraint, so `postings` has `PRIMARY KEY (id, occurred_at)`; FKs *to* postings are avoided (we reference `transaction_id` instead) except `lots.opening_posting_id`, which is a plain bigint without an FK constraint.

## Indexing strategy

Hot paths and the indexes that serve them:

| Query | Index |
|---|---|
| balance of an account since watermark | `postings (user_id, account_id, occurred_at) INCLUDE (amount, currency)` |
| spend by tag over a period | `postings (user_id, tag_id, occurred_at)` (rollups serve most of this) |
| self-transfer pair detection | `transactions (user_id, occurred_at)` + `postings (user_id, amount, occurred_at)` |
| recipe lookup | `raw_events (user_id, fingerprint)`; idempotency `UNIQUE (user_id, content_hash)` |
| tag suggestion | `tags USING gin (name gin_trgm_ops)`, `tags (system_key)`; `tag_index USING hnsw (embedding vector_cosine_ops)` |
| kNN over user's examples | `tag_examples USING hnsw (embedding vector_cosine_ops)` with `WHERE user_id = $1` filter; pgvector ≥ 0.8 iterative scan handles the filter |
| open interactions for a user | `interactions (user_id, status, priority, created_at) WHERE status = 'open'` |
| quota check | Redis; DB fallback `usage_rollups` PK |
| dirty periods | `dirty_periods` PK; small table |

Partial indexes are preferred for status filters (`WHERE status = 'open'`, `WHERE revoked_at IS NULL`). No index on `description` beyond the trigram GIN on `transactions.description_norm` (generated, unaccented, lowercased) for the console's text filter.

## Triggers and functions

- `set_updated_at()` — before update on mutable entity tables.
- `postings_immutable()` — raises on UPDATE/DELETE of `postings` for any role but `finassis_admin`/owner (tenant deletion). Corrections are reversals.
- `mark_dirty_period()` — after insert on `postings`: upsert `(user_id, 'day', date)` and `(user_id, 'month', month)` into `dirty_periods`. Rollup jobs consume it.
- `ensure_partitions(months_ahead int)` — creates missing monthly partitions for the three partitioned tables.
- `transactions.description_norm` — `GENERATED ALWAYS AS (lower(unaccent(description))) STORED` for search and fingerprinting. (`unaccent` must be marked `IMMUTABLE` via a wrapper `immutable_unaccent()` for use in a generated column.)

## Integrity rules enforced in SQL

- Postings within a transaction balance per currency (**checked in the application** inside the write transaction; a deferred constraint trigger `check_transaction_balanced()` is provided and enabled).
- `tags`: customs are children only; system tags have `system_key`; roots have `kind`. Postings may reference a root tag except when the root is `off_report` (`CHECK` via trigger `check_posting_tag()`).
- `units`: `factor_to_base IS NULL` ⇔ `measure = 'money'`.
- `accounts.currency` and `postings.currency` reference `units(code)` where measure = money (trigger, since a partial FK isn't expressible).
- `user_plans`: no overlapping periods per user (`EXCLUDE USING gist`).
- `identities`: `(provider, provider_id)` unique globally.

## Backups, migration, environments

- Dev: Docker Compose Postgres 16 with the four extensions; `make db-up`, `make db-migrate`, `make db-check` (applies `schema.sql` to a scratch DB and runs smoke inserts).
- Prod (single VM): daily `pg_dump -Fc` to object storage + WAL archiving for PITR when the data starts to matter. Managed Postgres later needs only `DATABASE_URL`.
- Alembic runs on startup with an advisory lock (`pg_advisory_lock(hashtext('finassis.migrate'))`) so concurrent replicas don't race.
- Seed loader runs after migrations (ADR-025).

## What is deliberately not in the schema

- No `households`, no `tenant_id` above `user_id` (ADR-026).
- No `transit` account or `pending_match` status (deferred, ADR-027).
- No `exclude_from_reports` flag (the `off_report` root is the mechanism).
- No stored translations; no `currencies` table (units cover it).

## Notes for implementers

- **User creation is privileged.** The `users` policy is `id = current_user_id()`, which cannot authorise an insert for a not-yet-existing id. Registration (`/start`), `/link` code redemption and bootstrap run on the `finassis_admin` connection; everything after that runs as `finassis_app` with `app.user_id` set.
- **Posting legs.** A posting may carry an account, a tag, or both. A simple expense is `[account −X] + [tag +X]` (the tag leg is the "equity" side); a transfer is two account legs, both tagged `off_report.*`. Rollups use the all-zero uuid as the sentinel for "no account" / "no tag".
- **Idempotency on `raw_events`** is an index, not a unique constraint (partitioning forbids it); the app checks `(user_id, content_hash)` before insert inside the same transaction.
- **Partition jobs** call `ensure_partitions()` (SECURITY DEFINER, admin-only) nightly; the DEFAULT partitions must stay empty (alert if not).
- **Validation**: `make db-parse` (parser only, no server) runs in `make check`; `make db-check` applies `schema.sql` to a scratch database and runs `api/db/smoke.sql` (RLS isolation, immutability, balance check, off_report root rule, money-unit trigger, partition routing). Run `db-check` locally with `make db-up` first.
