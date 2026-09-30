"""Auth and per-request dependencies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request

from ..db import Conn
from ..domain import keys
from ..domain.keys import Principal
from ..errors import Forbidden, Unauthorized
from .state import AppState


def state(request: Request) -> AppState:
    return request.app.state.services  # type: ignore[no-any-return]


State = Annotated[AppState, Depends(state)]


async def principal(
    request: Request,
    st: State,
    authorization: Annotated[str | None, Header()] = None,
    x_act_as_user: Annotated[str | None, Header(alias="X-Act-As-User")] = None,
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    async with st.db.admin_conn() as conn:
        p = await keys.authenticate(conn, token)
    if p is None:
        raise Unauthorized("invalid or revoked key")
    if x_act_as_user:
        if p.kind not in ("channel", "admin"):
            raise Forbidden("act-as requires a channel or admin key")
        p.act_as = uuid.UUID(x_act_as_user)
        async with st.db.admin_conn() as conn:
            row = await conn.fetchrow("SELECT locale, default_currency, timezone FROM users WHERE id = $1", p.act_as)
        if row is None:
            raise Forbidden("act-as user not found")
        p.locale, p.default_currency, p.timezone = row["locale"], row["default_currency"], row["timezone"]
    request.state.principal = p
    return p


Auth = Annotated[Principal, Depends(principal)]


def require(scope: str):  # type: ignore[no-untyped-def]
    async def _dep(p: Auth) -> Principal:
        if not p.has(scope):
            raise Forbidden(f"scope {scope} required")
        return p
    return Depends(_dep)


async def admin_only(p: Auth) -> Principal:
    if p.kind != "admin" and not p.is_admin:
        raise Forbidden("admin only")
    return p


Admin = Annotated[Principal, Depends(admin_only)]


async def tenant_conn(st: State, p: Auth) -> AsyncIterator[Conn]:
    """A tenant-scoped transaction for the authenticated (or acted-as) user. Commits on success."""
    uid = p.effective_user_id
    if uid is None:
        raise Forbidden("this key is not bound to a user")
    async with st.db.tenant_tx(uid) as conn:
        yield conn


Tenant = Annotated[Conn, Depends(tenant_conn)]


def user_id(p: Auth) -> uuid.UUID:
    uid = p.effective_user_id
    if uid is None:
        raise Forbidden("this key is not bound to a user")
    return uid


UserId = Annotated[uuid.UUID, Depends(user_id)]
