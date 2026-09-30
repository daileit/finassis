"""Re-close dirty periods: recompute period_rollups for (user, kind, period) from postings (tech/02)."""

from __future__ import annotations

from typing import Any

from ..db import Database
from ..logging import get_logger
from ..money import ZERO_UUID

log = get_logger(__name__)


async def reclose_dirty(db: Database, limit: int = 500) -> dict[str, Any]:
    done = 0
    async with db.admin_tx() as conn:
        rows = await conn.fetch("SELECT user_id, period_kind, period_start FROM dirty_periods ORDER BY marked_at LIMIT $1", limit)
        for r in rows:
            uid, kind, start = r["user_id"], r["period_kind"], r["period_start"]
            end_expr = "($3::date + interval '1 day')" if kind == "day" else "($3::date + interval '1 month')"
            await conn.execute(
                "DELETE FROM period_rollups WHERE user_id = $1 AND period_kind = $2 AND period_start = $3", uid, kind, start
            )
            await conn.execute(
                f"""INSERT INTO period_rollups(user_id, period_kind, period_start, account_id, tag_id, currency, debit, credit, net, posting_count, closed_at)
                    SELECT p.user_id, $2, $3, COALESCE(p.account_id, $4::uuid), COALESCE(p.tag_id, $4::uuid), p.currency,
                           sum(CASE WHEN p.amount < 0 THEN -p.amount ELSE 0 END), sum(CASE WHEN p.amount > 0 THEN p.amount ELSE 0 END),
                           sum(p.amount), count(*), now()
                    FROM postings p JOIN transactions t ON t.id = p.transaction_id
                    WHERE p.user_id = $1 AND t.status <> 'projected'
                      AND (p.occurred_at AT TIME ZONE 'UTC') >= $3::date AND (p.occurred_at AT TIME ZONE 'UTC') < {end_expr}
                    GROUP BY p.user_id, COALESCE(p.account_id, $4::uuid), COALESCE(p.tag_id, $4::uuid), p.currency""",
                uid, kind, start, ZERO_UUID,
            )
            await conn.execute(
                "DELETE FROM dirty_periods WHERE user_id = $1 AND period_kind = $2 AND period_start = $3", uid, kind, start
            )
            done += 1
    if done:
        log.info("rollups.reclosed", periods=done)
    return {"reclosed": done}
