"""Usage metering and allowance (ADR-014/015). Events go to usage_events; hot counters in Redis;
quota checks read plan limits + grants − usage."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis

from ..config import get_behaviour
from ..db import Conn, Database
from ..errors import AllowanceExhausted
from ..logging import get_logger

log = get_logger(__name__)


def _month_key(user_id: uuid.UUID, kind: str, now: datetime) -> str:
    return f"usage:{user_id}:{kind}:{now:%Y-%m}"


class Meter:
    def __init__(self, db: Database, redis: Redis | None) -> None:
        self.db = db
        self.redis = redis

    async def record(self, user_id: uuid.UUID, kind: str, quantity: float = 1, ref_id: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        try:
            async with self.db.admin_conn() as conn:
                await conn.execute(
                    "INSERT INTO usage_events(user_id, kind, quantity, ref_id, occurred_at) VALUES ($1,$2,$3,$4,$5)",
                    user_id, kind, Decimal(str(quantity)), ref_id, now,
                )
            if self.redis is not None:
                key = _month_key(user_id, kind, now)
                await self.redis.incrbyfloat(key, quantity)
                await self.redis.expire(key, 40 * 24 * 3600)
        except Exception as e:  # metering must never break a request
            log.warning("metering.record_failed", kind=kind, error=str(e))

    async def used_this_month(self, user_id: uuid.UUID, kind: str) -> float:
        now = datetime.now(timezone.utc)
        if self.redis is not None:
            v = await self.redis.get(_month_key(user_id, kind, now))
            if v is not None:
                return float(v)
        async with self.db.admin_conn() as conn:
            v = await conn.fetchval(
                """SELECT COALESCE(sum(quantity),0) FROM usage_events
                   WHERE user_id = $1 AND kind = $2 AND occurred_at >= date_trunc('month', now())""",
                user_id, kind,
            )
        return float(v or 0)

    async def limits_for(self, conn: Conn, user_id: uuid.UUID) -> dict[str, Any]:
        row = await conn.fetchrow(
            """SELECT p.id, p.name, p.limits FROM user_plans up JOIN plans p ON p.id = up.plan_id
               WHERE up.user_id = $1 AND up.ended_at IS NULL ORDER BY up.started_at DESC LIMIT 1""",
            user_id,
        )
        limits: dict[str, Any] = dict(row["limits"]) if row else dict(get_behaviour().free_plan_limits)
        grants = await conn.fetch(
            "SELECT allowance FROM grants WHERE user_id = $1 AND (expires_at IS NULL OR expires_at > now())", user_id
        )
        extra: dict[str, float] = {}
        for g in grants:
            for k, v in (g["allowance"] or {}).items():
                extra[k] = extra.get(k, 0) + float(v)
        return {"plan": row["id"] if row else "free", "plan_name": row["name"] if row else "Free", "limits": limits, "grants": extra}

    async def check(self, conn: Conn, user_id: uuid.UUID, kind: str, locale: str = "en") -> None:
        """Raise AllowanceExhausted if the monthly limit for `kind` is used up. Only metered kinds are enforced."""
        if kind not in get_behaviour().metered_kinds:
            return
        info = await self.limits_for(conn, user_id)
        lim = info["limits"].get(kind, {})
        monthly = lim.get("month")
        if monthly is None:
            return
        allowed = float(monthly) + info["grants"].get(kind, 0)
        used = await self.used_this_month(user_id, kind)
        if used >= allowed:
            now = datetime.now(timezone.utc)
            reset = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)).replace(day=1)
            raise AllowanceExhausted(
                "allowance exhausted", kind=kind, limit=allowed, used=used, resets_at=reset.isoformat(), top_up_url=None
            )

    async def allowance(self, conn: Conn, user_id: uuid.UUID) -> dict[str, Any]:
        info = await self.limits_for(conn, user_id)
        out = {}
        for kind, lim in info["limits"].items():
            monthly = lim.get("month")
            used = await self.used_this_month(user_id, kind)
            allowed = (float(monthly) + info["grants"].get(kind, 0)) if monthly is not None else None
            out[kind] = {"used": used, "limit": allowed, "remaining": (allowed - used) if allowed is not None else None,
                         "cap": lim.get("cap"), "enforced": kind in get_behaviour().metered_kinds}
        return {"plan": info["plan"], "plan_name": info["plan_name"], "kinds": out}
