from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query

from ...domain import tags as tg
from ..deps import Auth, State, Tenant, UserId, require
from ..schemas import TagCreate, TagPatch
from ..serialize import tag as ser_tag

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("")
async def list_tags(conn: Tenant, uid: UserId, p: Auth, include_hidden: bool = False) -> dict[str, Any]:
    items = await tg.list_tags(conn, uid, p.locale, include_hidden=include_hidden)
    return {"items": [ser_tag(t) for t in items]}


@router.get("/suggest")
async def suggest(conn: Tenant, uid: UserId, p: Auth, q: str = Query(min_length=1, max_length=60), limit: int = 3) -> dict[str, Any]:
    items = await tg.suggest_tags(conn, uid, q, p.locale, limit=min(limit, 10))
    return {"query": q, "suggestions": items, "create_as_child_of": items[0]["root_key"] if items else None}


@router.post("", status_code=201, dependencies=[require("tags:write")])
async def create_tag(body: TagCreate, conn: Tenant, uid: UserId, p: Auth, st: State) -> dict[str, Any]:
    await st.meter.check(conn, uid, "tag.custom", p.locale)
    t = await tg.create_custom_tag(conn, uid, body.name, body.root)
    await st.meter.record(uid, "tag.custom", 1, ref_id=str(t["id"]))
    return ser_tag(t)


@router.get("/{tag_id}")
async def get_tag(tag_id: uuid.UUID, conn: Tenant, uid: UserId) -> dict[str, Any]:
    return ser_tag(await tg.get_tag(conn, uid, tag_id))


@router.patch("/{tag_id}", dependencies=[require("tags:write")])
async def patch_tag(tag_id: uuid.UUID, body: TagPatch, conn: Tenant, uid: UserId) -> dict[str, Any]:
    t = await tg.update_tag_prefs(conn, uid, tag_id, is_hidden=body.is_hidden, display_name_override=body.display_name_override, name=body.name)
    return ser_tag(t)


@router.delete("/{tag_id}", status_code=204, dependencies=[require("tags:write")])
async def delete_tag(tag_id: uuid.UUID, conn: Tenant, uid: UserId) -> None:
    await tg.delete_custom_tag(conn, uid, tag_id)
