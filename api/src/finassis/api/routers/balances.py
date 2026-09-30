from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter

from ...domain import reports
from ..deps import Auth, State, Tenant, UserId

router = APIRouter(tags=["reports"])


@router.get("/balances")
async def balances(conn: Tenant, uid: UserId, p: Auth, st: State, at: datetime | None = None) -> dict[str, Any]:
    items = await reports.balances(conn, uid, st.units, p.locale, at)
    return {"items": items, "as_of": at or datetime.now().astimezone()}
