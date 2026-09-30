from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..deps import Auth, State, Tenant, UserId

router = APIRouter(tags=["reference"])


@router.get("/units")
async def list_units(st: State, conn: Tenant, p: Auth, measure: str | None = Query(default=None)) -> dict[str, Any]:
    rows = await conn.fetch(
        """SELECT code, measure, factor_to_base, decimals, symbol, name, user_id IS NOT NULL AS is_custom
           FROM units WHERE NOT is_deprecated AND ($1::text IS NULL OR measure = $1) ORDER BY measure, code""",
        measure,
    )
    return {"items": [dict(r) | {"factor_to_base": str(r["factor_to_base"]) if r["factor_to_base"] is not None else None} for r in rows]}


@router.get("/meta/enums")
async def enums(conn: Tenant, uid: UserId) -> dict[str, Any]:
    """Every enum value the API can return, for console translation coverage."""
    rows = await conn.fetch(
        """SELECT t.typname AS name, pg_get_constraintdef(c.oid) AS def
           FROM pg_constraint c JOIN pg_type t ON t.oid = c.contypid WHERE t.typname LIKE 'd\\_%' ESCAPE '\\'"""
    )
    import re
    out: dict[str, list[str]] = {}
    for r in rows:
        vals = re.findall(r"'([^']+)'", r["def"])
        if vals and "ANY" in r["def"]:
            out[r["name"][2:]] = vals
    tag_keys = [r["system_key"] for r in await conn.fetch("SELECT system_key FROM tags WHERE system_key IS NOT NULL ORDER BY sort_order")]
    unit_codes = [r["code"] for r in await conn.fetch("SELECT code FROM units WHERE user_id IS NULL ORDER BY code")]
    return {"enums": out, "tag_keys": tag_keys, "unit_codes": unit_codes}
