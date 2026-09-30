from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from ...domain import keys as k
from ...domain import users
from ...errors import Validation
from ..deps import Auth, Tenant, UserId, require
from ..schemas import KeyCreate, LinkCodeIn

router = APIRouter(tags=["keys", "identities"])


@router.get("/keys")
async def list_keys(conn: Tenant, uid: UserId) -> dict[str, Any]:
    return {"items": await k.list_keys(conn, uid)}


@router.post("/keys", status_code=201, dependencies=[require("keys:manage")])
async def create_key(body: KeyCreate, conn: Tenant, uid: UserId, p: Auth) -> dict[str, Any]:
    bad = [s for s in body.scopes if s not in k.ALL_USER_SCOPES and s != "*"]
    if bad:
        raise Validation("unknown scopes", scopes=bad, allowed=k.ALL_USER_SCOPES)
    if "*" in body.scopes and p.kind != "admin":
        raise Validation("wildcard scope is admin-only")
    row, plain = await k.create_key(conn, uid, "user", body.name, body.scopes)
    return {**row, "key": plain, "note": "shown once"}


@router.delete("/keys/{key_id}", status_code=204, dependencies=[require("keys:manage")])
async def revoke(key_id: uuid.UUID, conn: Tenant, uid: UserId) -> None:
    await k.revoke_key(conn, uid, key_id)


@router.get("/identities")
async def identities(conn: Tenant, uid: UserId) -> dict[str, Any]:
    return {"items": await users.list_identities(conn, uid)}


@router.post("/identities/link-codes", status_code=201)
async def link_code(body: LinkCodeIn, conn: Tenant, uid: UserId) -> dict[str, Any]:
    return await users.mint_link_code(conn, uid, body.provider)
