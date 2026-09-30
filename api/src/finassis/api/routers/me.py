from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...domain import users
from ..deps import Auth, State, Tenant, UserId
from ..schemas import MePatch

router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
async def get_me(st: State, p: Auth, conn: Tenant, uid: UserId) -> dict[str, Any]:
    u = await users.get_user(conn, uid)
    allowance = await st.meter.allowance(conn, uid)
    ids = await users.list_identities(conn, uid)
    return {
        "user": {k: u[k] for k in ("id", "locale", "default_currency", "timezone", "display_name", "is_admin", "created_at")},
        "personality_profile": u.get("personality_profile") or {},
        "key": {"id": p.key_id, "kind": p.kind, "scopes": p.scopes, "act_as": p.act_as},
        "identities": ids,
        "plan": allowance["plan"], "plan_name": allowance["plan_name"], "allowance": allowance["kinds"],
    }


@router.patch("")
async def patch_me(body: MePatch, conn: Tenant, uid: UserId, st: State) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if "default_currency" in fields and (not st.units.has(fields["default_currency"]) or not st.units.get(fields["default_currency"]).is_money):
        from ...errors import Validation
        raise Validation("unknown currency")
    u = await users.update_user(conn, uid, **fields)
    return {k: u[k] for k in ("id", "locale", "default_currency", "timezone", "display_name")}
