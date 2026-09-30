from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Query

from ...domain import ledger
from ...errors import Validation
from ..deps import Auth, State, Tenant, UserId, require
from ..schemas import Reverse, SetTag, TransactionExplicit, TransactionSimple
from ..serialize import transaction as ser

router = APIRouter(prefix="/transactions", tags=["ledger"])


@router.post("", status_code=201, dependencies=[require("ledger:write")])
async def create_transaction(
    conn: Tenant, uid: UserId, p: Auth, st: State,
    body: dict[str, Any] = Body(..., description="Simple form {account, amount, occurred_at, ...} or explicit form {occurred_at, postings[]}"),
) -> dict[str, Any]:
    if "postings" in body:
        model = TransactionExplicit.model_validate(body)
        tx = await ledger.build_explicit(conn, uid, st.units, p.locale, p.timezone, model.model_dump())
    elif "account" in body:
        model_s = TransactionSimple.model_validate(body)
        tx = await ledger.build_simple(conn, uid, st.units, p.locale, p.timezone, model_s.model_dump())
    else:
        raise Validation("body must be the simple form (account, amount, occurred_at) or the explicit form (postings[])")
    row = await ledger.write_transaction(conn, uid, tx)
    return ser(row, st.units, p.locale)


@router.get("")
async def list_transactions(
    conn: Tenant, uid: UserId, p: Auth, st: State,
    limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None,
    account_id: uuid.UUID | None = None, tag_id: uuid.UUID | None = None,
    since: datetime | None = None, until: datetime | None = None, q: str | None = None, untagged: bool = False,
) -> dict[str, Any]:
    items, next_cursor = await ledger.list_transactions(
        conn, uid, limit=limit, cursor=cursor, account_id=account_id, tag_id=tag_id, since=since, until=until, q=q, untagged_only=untagged
    )
    return {"items": [ser(t, st.units, p.locale) for t in items], "next_cursor": next_cursor}


@router.get("/{tx_id}")
async def get_transaction(tx_id: uuid.UUID, conn: Tenant, uid: UserId, p: Auth, st: State) -> dict[str, Any]:
    return ser(await ledger.get_transaction(conn, uid, tx_id), st.units, p.locale)


@router.post("/{tx_id}/tag", dependencies=[require("ledger:write")])
async def set_tag(tx_id: uuid.UUID, body: SetTag, conn: Tenant, uid: UserId, p: Auth, st: State) -> dict[str, Any]:
    row = await ledger.set_tag(conn, uid, tx_id, body.tag, p.locale)
    # pin: Phase 1 writes a high-weight tag_example / merchant memory; recorded in metadata for now
    if body.pin:
        await conn.execute("UPDATE transactions SET metadata = metadata || '{\"pinned_tag\": true}'::jsonb WHERE id = $1", tx_id)
    return ser(row, st.units, p.locale)


@router.post("/{tx_id}/reverse", status_code=201, dependencies=[require("ledger:write")])
async def reverse(tx_id: uuid.UUID, body: Reverse, conn: Tenant, uid: UserId, p: Auth, st: State) -> dict[str, Any]:
    return ser(await ledger.reverse_transaction(conn, uid, tx_id, body.reason), st.units, p.locale)
