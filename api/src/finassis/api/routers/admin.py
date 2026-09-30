from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from ...domain import interactions, keys, users
from ..deps import Admin, State
from ..schemas import AdminUserCreate, GrantIn, KeyCreate

router = APIRouter(prefix="/admin", tags=["admin"])


async def _audit(st: State, actor: str, action: str, target: str | None, payload: dict[str, Any] | None = None) -> None:
    async with st.db.admin_conn() as conn:
        await conn.execute("INSERT INTO admin_audit(actor, action, target, payload) VALUES ($1,$2,$3,$4)", actor, action, target, payload)


@router.get("/users")
async def list_users(st: State, p: Admin, limit: int = 100) -> dict[str, Any]:
    async with st.db.admin_conn() as conn:
        rows = await conn.fetch(
            """SELECT u.id, u.display_name, u.locale, u.default_currency, u.is_admin, u.is_paused, u.created_at,
                      (SELECT plan_id FROM user_plans up WHERE up.user_id = u.id AND up.ended_at IS NULL ORDER BY started_at DESC LIMIT 1) AS plan,
                      (SELECT count(*) FROM transactions t WHERE t.user_id = u.id) AS transactions
               FROM users u ORDER BY u.created_at DESC LIMIT $1""",
            limit,
        )
    return {"items": [dict(r) for r in rows]}


@router.post("/users", status_code=201)
async def create_user(body: AdminUserCreate, st: State, p: Admin) -> dict[str, Any]:
    async with st.db.admin_tx() as conn:
        u = await users.create_user(conn, locale=body.locale, default_currency=body.default_currency, tz=body.timezone, display_name=body.display_name)
        if body.telegram_id:
            await users.link_identity(conn, u["id"], "telegram", body.telegram_id, body.display_name)
        await interactions.onboarding(conn, u["id"])
    await _audit(st, str(p.key_id), "user.create", str(u["id"]))
    return {k: u[k] for k in ("id", "locale", "default_currency", "timezone", "display_name", "created_at")}


@router.post("/users/{user_id}/keys", status_code=201)
async def mint_user_key(user_id: uuid.UUID, body: KeyCreate, st: State, p: Admin) -> dict[str, Any]:
    async with st.db.admin_tx() as conn:
        await users.get_user(conn, user_id)
        row, plain = await keys.create_key(conn, user_id, "user", body.name, body.scopes)
    await _audit(st, str(p.key_id), "key.create", str(user_id), {"key_id": str(row["id"])})
    return {**row, "key": plain, "note": "shown once"}


@router.post("/users/{user_id}/grants", status_code=201)
async def add_grant(user_id: uuid.UUID, body: GrantIn, st: State, p: Admin) -> dict[str, Any]:
    async with st.db.admin_tx() as conn:
        row = await conn.fetchrow(
            "INSERT INTO grants(user_id, kind, allowance, note, expires_at) VALUES ($1,$2,$3,$4,$5) RETURNING *",
            user_id, body.kind, body.allowance, body.note, body.expires_at,
        )
    await _audit(st, str(p.key_id), "grant.add", str(user_id), body.model_dump(mode="json"))
    return dict(row)  # type: ignore[arg-type]


@router.post("/users/{user_id}/pause")
async def pause(user_id: uuid.UUID, st: State, p: Admin, paused: bool = True) -> dict[str, Any]:
    async with st.db.admin_tx() as conn:
        await conn.execute("UPDATE users SET is_paused = $2 WHERE id = $1", user_id, paused)
    await _audit(st, str(p.key_id), "user.pause" if paused else "user.unpause", str(user_id))
    return {"ok": True, "paused": paused}


@router.get("/pipeline")
async def pipeline(st: State, p: Admin) -> dict[str, Any]:
    async with st.db.admin_conn() as conn:
        dirty = await conn.fetchval("SELECT count(*) FROM dirty_periods")
        oldest = await conn.fetchval("SELECT min(marked_at) FROM dirty_periods")
        open_i = await conn.fetchval("SELECT count(*) FROM interactions WHERE status = 'open'")
        parts = await conn.fetch("SELECT relname FROM pg_class WHERE relname ~ '^(postings|raw_events|usage_events)_\\d{4}_\\d{2}$' ORDER BY 1")
        defaults = {}
        for t in ("postings_default", "raw_events_default", "usage_events_default"):
            defaults[t] = await conn.fetchval(f"SELECT count(*) FROM {t}")
    return {"dirty_periods": dirty, "oldest_dirty": oldest, "open_interactions": open_i,
            "partitions": [r["relname"] for r in parts], "default_partition_rows": defaults}


@router.post("/jobs/{name}/run")
async def run_job(name: str, st: State, p: Admin) -> dict[str, Any]:
    from ...jobs import maintenance, rollups, snapshots
    jobs = {"reclose": rollups.reclose_dirty, "snapshots": snapshots.run_daily, "partitions": maintenance.ensure_partitions,
            "expire_interactions": maintenance.expire_interactions}
    if name not in jobs:
        from ...errors import NotFound
        raise NotFound("unknown job", jobs=list(jobs))
    result = await jobs[name](st.db)
    await _audit(st, str(p.key_id), f"job.{name}", None, {"result": result})
    return {"job": name, "result": result}
