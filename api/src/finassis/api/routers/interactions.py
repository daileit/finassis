from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from ...domain import interactions as it
from ..deps import Auth, Tenant, UserId, require
from ..schemas import ResolveIn

router = APIRouter(prefix="/interactions", tags=["interactions"])


@router.get("")
async def list_open(conn: Tenant, uid: UserId, p: Auth, kind: str | None = None) -> dict[str, Any]:
    rows = await it.list_open(conn, uid, kind)
    return {"items": [it.render(r, p.locale) for r in rows]}


@router.get("/{interaction_id}")
async def get_one(interaction_id: uuid.UUID, conn: Tenant, uid: UserId, p: Auth) -> dict[str, Any]:
    return it.render(await it.get(conn, uid, interaction_id), p.locale)


@router.post("/{interaction_id}/resolve", dependencies=[require("interactions:write")])
async def resolve(interaction_id: uuid.UUID, body: ResolveIn, conn: Tenant, uid: UserId, p: Auth) -> dict[str, Any]:
    via = "mcp" if "mcp" in (p.scopes or []) else "api"
    return it.render(await it.resolve(conn, uid, interaction_id, option_key=body.option_key, text=body.text, via=via), p.locale)


@router.post("/{interaction_id}/dismiss", dependencies=[require("interactions:write")])
async def dismiss(interaction_id: uuid.UUID, conn: Tenant, uid: UserId, p: Auth) -> dict[str, Any]:
    return it.render(await it.dismiss(conn, uid, interaction_id), p.locale)
