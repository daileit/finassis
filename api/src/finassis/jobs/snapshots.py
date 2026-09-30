"""Daily balance and net-worth snapshots (tech/02). Idempotent per (user, as_of)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ..db import Database
from ..logging import get_logger

log = get_logger(__name__)


async def run_daily(db: Database, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or (datetime.now(timezone.utc).date() - timedelta(days=0))
    cutoff = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    users_done = 0
    async with db.admin_tx() as conn:
        users = await conn.fetch("SELECT id, default_currency FROM users WHERE NOT is_paused")
        for u in users:
            uid = u["id"]
            rows = await conn.fetch(
                """SELECT p.account_id, p.currency, sum(p.amount) AS balance, max(p.id) AS last_id, a.is_liability
                   FROM postings p JOIN accounts a ON a.id = p.account_id
                   WHERE p.user_id = $1 AND p.account_id IS NOT NULL AND p.occurred_at < $2 AND a.valuation_mode = 'ledger'
                   GROUP BY p.account_id, p.currency, a.is_liability""",
                uid, cutoff,
            )
            assets: dict[str, Decimal] = {}
            liabs: dict[str, Decimal] = {}
            for r in rows:
                await conn.execute(
                    """INSERT INTO balance_snapshots(user_id, account_id, currency, as_of, balance, last_posting_id)
                       VALUES ($1,$2,$3,$4,$5,$6)
                       ON CONFLICT (user_id, account_id, currency, as_of) DO UPDATE SET balance = EXCLUDED.balance, last_posting_id = EXCLUDED.last_posting_id""",
                    uid, r["account_id"], r["currency"], as_of, r["balance"], r["last_id"],
                )
                bucket = liabs if r["is_liability"] else assets
                bucket[r["currency"]] = bucket.get(r["currency"], Decimal(0)) + Decimal(r["balance"])
            # net worth in the user's default currency using latest FX rates where available
            cur = u["default_currency"]
            total_a = total_l = Decimal(0)
            missing = []
            for bucket, is_l in ((assets, False), (liabs, True)):
                for c, v in bucket.items():
                    rate = Decimal(1)
                    if c != cur:
                        rr = await conn.fetchval("SELECT rate FROM fx_rates WHERE base=$1 AND quote=$2 ORDER BY as_of DESC LIMIT 1", c, cur)
                        if rr is None:
                            missing.append(c)
                            continue
                        rate = Decimal(rr)
                    if is_l:
                        total_l += abs(v) * rate
                    else:
                        total_a += v * rate
            await conn.execute(
                """INSERT INTO networth_snapshots(user_id, as_of, currency, total_assets, total_liabilities, net_worth, breakdown)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)
                   ON CONFLICT (user_id, as_of) DO UPDATE SET currency = EXCLUDED.currency, total_assets = EXCLUDED.total_assets,
                     total_liabilities = EXCLUDED.total_liabilities, net_worth = EXCLUDED.net_worth, breakdown = EXCLUDED.breakdown""",
                uid, as_of, cur, total_a, total_l, total_a - total_l,
                {"assets_by_currency": {k: str(v) for k, v in assets.items()}, "liabilities_by_currency": {k: str(v) for k, v in liabs.items()},
                 "unconverted": missing, "scope": "ledger_only"},
            )
            users_done += 1
    log.info("snapshots.done", as_of=str(as_of), users=users_done)
    return {"as_of": str(as_of), "users": users_done}
