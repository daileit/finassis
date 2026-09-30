"""Tiny asyncio scheduler for the worker process. Phase 0: interval + daily-hour jobs.
Redis Streams consumers (ingest, events) are added in Phase 1."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from ..config import get_behaviour
from ..db import Database
from ..logging import get_logger
from . import maintenance, rollups, snapshots

log = get_logger(__name__)

Job = Callable[[Database], Awaitable[dict[str, Any]]]


async def _every(db: Database, name: str, seconds: int, job: Job) -> None:
    while True:
        try:
            res = await job(db)
            log.debug("job.ok", job=name, **{k: str(v) for k, v in (res or {}).items()})
        except Exception as e:
            log.exception("job.failed", job=name, error=str(e))
        await asyncio.sleep(seconds)


async def _daily_at(db: Database, name: str, hour_utc: int, job: Job) -> None:
    last_run_day: str | None = None
    while True:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if now.hour >= hour_utc and last_run_day != today:
            try:
                res = await job(db)
                log.info("job.ok", job=name, **{k: str(v) for k, v in (res or {}).items()})
            except Exception as e:
                log.exception("job.failed", job=name, error=str(e))
            last_run_day = today
        await asyncio.sleep(300)


async def run_worker() -> None:
    b = get_behaviour()
    db = Database()
    await db.connect()
    log.info("worker.started")
    try:
        await asyncio.gather(
            _every(db, "reclose", b.rollup_reclose_interval_s, rollups.reclose_dirty),
            _every(db, "expire_interactions", 1800, maintenance.expire_interactions),
            _every(db, "usage_rollup", 900, maintenance.usage_rollup),
            _daily_at(db, "snapshots", b.snapshot_hour_utc, snapshots.run_daily),
            _daily_at(db, "partitions", 1, maintenance.ensure_partitions),
        )
    finally:
        await db.close()
