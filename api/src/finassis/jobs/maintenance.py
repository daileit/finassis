from __future__ import annotations

from typing import Any

from ..config import get_behaviour
from ..db import Database
from ..domain import interactions
from ..logging import get_logger

log = get_logger(__name__)


async def ensure_partitions(db: Database) -> dict[str, Any]:
    months = get_behaviour().partitions_months_ahead
    async with db.admin_conn() as conn:
        await conn.execute("SELECT ensure_partitions($1)", months)
        defaults = {}
        for t in ("postings_default", "raw_events_default", "usage_events_default"):
            defaults[t] = await conn.fetchval(f"SELECT count(*) FROM {t}")
    if any(defaults.values()):
        log.warning("partitions.default_not_empty", **defaults)
    return {"months_ahead": months, "default_partition_rows": defaults}


async def expire_interactions(db: Database) -> dict[str, Any]:
    async with db.admin_tx() as conn:
        n = await interactions.expire_stale(conn)
    return {"expired": n}


async def usage_rollup(db: Database) -> dict[str, Any]:
    """Roll usage_events into usage_rollups (day + month) for the last 2 days."""
    async with db.admin_tx() as conn:
        n = await conn.execute(
            """INSERT INTO usage_rollups(user_id, kind, period_kind, period_start, quantity, closed_at)
               SELECT user_id, kind, 'day', (occurred_at AT TIME ZONE 'UTC')::date, sum(quantity), now()
               FROM usage_events WHERE occurred_at >= now() - interval '2 days'
               GROUP BY 1,2,4
               ON CONFLICT (user_id, kind, period_kind, period_start) DO UPDATE SET quantity = EXCLUDED.quantity, closed_at = now()"""
        )
        await conn.execute(
            """INSERT INTO usage_rollups(user_id, kind, period_kind, period_start, quantity, closed_at)
               SELECT user_id, kind, 'month', date_trunc('month', occurred_at AT TIME ZONE 'UTC')::date, sum(quantity), now()
               FROM usage_events WHERE occurred_at >= date_trunc('month', now() - interval '1 month')
               GROUP BY 1,2,4
               ON CONFLICT (user_id, kind, period_kind, period_start) DO UPDATE SET quantity = EXCLUDED.quantity, closed_at = now()"""
        )
    return {"rows": n}
