"""Process-wide services attached to the FastAPI app (built in the lifespan)."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from ..db import Database
from ..domain.metering import Meter
from ..money import UnitRegistry


@dataclass
class AppState:
    db: Database
    redis: Redis | None
    meter: Meter
    units: UnitRegistry

    async def refresh_units(self) -> None:
        async with self.db.admin_conn() as conn:
            rows = await conn.fetch("SELECT code, measure, factor_to_base, decimals, symbol, name FROM units WHERE NOT is_deprecated")
        self.units.update([dict(r) for r in rows])
