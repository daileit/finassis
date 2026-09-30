from __future__ import annotations

from fastapi import APIRouter

from ... import __version__
from ..deps import State

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(st: State) -> dict[str, object]:
    db_ok = redis_ok = False
    try:
        async with st.db.admin_conn() as conn:
            db_ok = (await conn.fetchval("SELECT 1")) == 1
    except Exception:
        db_ok = False
    if st.redis is not None:
        try:
            redis_ok = bool(await st.redis.ping())
        except Exception:
            redis_ok = False
    return {"status": "ok" if db_ok else "degraded", "version": __version__, "db": db_ok, "redis": redis_ok}
