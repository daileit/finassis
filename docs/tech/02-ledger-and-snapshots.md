# Ledger, rollups and snapshots

## The problem

The `postings` table is append-only and grows forever. Answering "net worth on 2026-09-30" or "dining spend per month for 3 years" by scanning it does not scale. We keep **pre-computed answers side by side with the detail**.

> Analogy check: this is *not* quite Loki's index + chunks. Loki's index tells you *where to look*; you still read the chunk. Our side tables hold the *answer*. The closer analogies are Prometheus recording rules, TimescaleDB continuous aggregates, or an accountant's period close.

## Three tiers

| Tier | Table | Grain | Written by | Purpose |
|------|-------|-------|-----------|---------|
| Detail | `postings` | one row per money movement | API / ingestion | Truth. Audit. Drill-down. |
| Rollup | `period_rollups` | user × account × tag × currency × period (day, month) | worker, incremental on `posting.committed` + nightly re-close | Fast sums for spend/income reports, budgets |
| Snapshot | `balance_snapshots`, `networth_snapshots` | user × account × currency at a point in time | nightly job (and on-demand) | Fast point-in-time balances and net-worth time series |

```
period_rollups
  user_id, period_kind (day | month), period_start date, account_id, tag_id, currency,
  debit NUMERIC, credit NUMERIC, net NUMERIC, posting_count int, closed_at timestamptz
  PK (user_id, period_kind, period_start, account_id, tag_id, currency)

balance_snapshots
  user_id, account_id, currency, as_of date, balance NUMERIC, quantity NUMERIC (nullable),
  last_posting_id  -- watermark: everything ≤ this id is included
  PK (user_id, account_id, currency, as_of)

networth_snapshots
  user_id, as_of date, currency (user default at that time), total_assets, total_liabilities, net_worth,
  breakdown jsonb  -- by account type / asset class / native currency
  PK (user_id, as_of)
```

## Balance formula

```
balance(account, t) = balance_snapshot(account, latest as_of ≤ t).balance
                    + SUM(postings.amount WHERE account_id = account
                                            AND id > snapshot.last_posting_id
                                            AND occurred_at ≤ t)
```

For **mark-to-market** accounts, replace the ledger sum with the latest `valuations.value` at or before `t`; postings on those accounts only adjust quantity/cost basis.

## Incremental vs. re-close

- **Incremental (hot path):** every committed posting increments the matching `period_rollups` row (`UPSERT ... net = net + $amount`). Cheap, keeps reports current within seconds.
- **Re-close (correctness path):** nightly job recomputes rollups and snapshots for any period touched by a posting whose `created_at` is later than the period's `closed_at` (i.e. backdated entries, corrections). We track dirty periods in a small `dirty_periods (user_id, period_kind, period_start)` table populated by a trigger or the worker, so re-close only touches what changed.

Backdated corrections are the whole reason snapshots carry `last_posting_id` watermarks rather than trusting `occurred_at` alone.

## Snapshot cadence

- `balance_snapshots`: daily per account (cheap: one row per account per day). Older than 2 years → thin to monthly.
- `networth_snapshots`: daily, computed from balance snapshots + valuations + FX. This is the "wealth over time" chart.
- Both are **idempotent**: re-running for a date overwrites.

## Caching (Redis)

- `balance:{user}:{account}` and `rollup:{user}:{month}` cached with short TTL.
- Invalidated by tenant on `posting.committed`; never a correctness dependency — Redis can be flushed at any time.

## Partitioning & retention

- `postings` range-partitioned by month on `occurred_at`. Partitions older than N years can be moved to cheaper storage or compressed (pg_squeeze / columnar) without affecting rollups.
- Nothing is deleted. A user export/delete request removes the tenant's partitions' rows and all side tables.

## Consistency guarantees

- Writing a transaction + postings is one Postgres transaction.
- Rollup increments are eventually consistent (seconds). Reports can request `?consistency=strong` to compute from the ledger directly for small windows.
- Nightly re-close is the reconciliation loop; a `rollup_lag` metric alerts if any dirty period is older than 24h.
