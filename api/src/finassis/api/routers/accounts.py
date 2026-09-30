from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from ...domain import accounts as acc
from ...errors import Validation
from ..deps import State, Tenant, UserId, require
from ..schemas import AccountCreate, AccountUpdate, AliasIn
from ..serialize import account as ser

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("")
async def list_accounts(conn: Tenant, uid: UserId, include_archived: bool = False) -> dict[str, Any]:
    return {"items": [ser(a) for a in await acc.list_accounts(conn, uid, include_archived)]}


@router.post("", status_code=201, dependencies=[require("accounts:write")])
async def create_account(body: AccountCreate, conn: Tenant, uid: UserId, st: State) -> dict[str, Any]:
    if not st.units.has(body.currency) or not st.units.get(body.currency).is_money:
        raise Validation(f"unknown currency {body.currency}")
    return ser(await acc.create_account(conn, uid, **body.model_dump()))


@router.get("/{account_id}")
async def get_account(account_id: uuid.UUID, conn: Tenant, uid: UserId) -> dict[str, Any]:
    return ser(await acc.get_account(conn, uid, account_id))


@router.patch("/{account_id}", dependencies=[require("accounts:write")])
async def update_account(account_id: uuid.UUID, body: AccountUpdate, conn: Tenant, uid: UserId) -> dict[str, Any]:
    return ser(await acc.update_account(conn, uid, account_id, **body.model_dump(exclude_none=True)))


@router.post("/{account_id}/aliases", status_code=201, dependencies=[require("accounts:write")])
async def add_alias(account_id: uuid.UUID, body: AliasIn, conn: Tenant, uid: UserId) -> dict[str, Any]:
    await acc.get_account(conn, uid, account_id)
    await acc.add_alias(conn, uid, account_id, body.alias)
    return ser(await acc.get_account(conn, uid, account_id))


@router.delete("/{account_id}/aliases/{alias}", status_code=204, dependencies=[require("accounts:write")])
async def remove_alias(account_id: uuid.UUID, alias: str, conn: Tenant, uid: UserId) -> None:
    await acc.get_account(conn, uid, account_id)
    await acc.remove_alias(conn, uid, alias)


@router.post("/system/{kind}", dependencies=[require("accounts:write")])
async def ensure_system(kind: str, conn: Tenant, uid: UserId, st: State, currency: str | None = None) -> dict[str, Any]:
    from ...domain import users
    cur = currency or (await users.get_user(conn, uid))["default_currency"]
    return ser(await acc.ensure_system_account(conn, uid, kind, cur))
