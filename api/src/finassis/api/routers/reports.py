from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query

from ...domain import reports
from ..deps import Auth, State, Tenant, UserId

router = APIRouter(prefix="/reports", tags=["reports"])


def _period(period: str | None, start: date | None, end: date | None) -> tuple[date, date]:
    if start and end:
        return start, end
    today = date.today()
    if period == "week":
        s = today - timedelta(days=today.weekday())
        return s, s + timedelta(days=7)
    if period == "last_month":
        first = today.replace(day=1)
        prev_last = first - timedelta(days=1)
        return prev_last.replace(day=1), first
    if period == "year":
        return today.replace(month=1, day=1), today.replace(month=1, day=1).replace(year=today.year + 1)
    s = today.replace(day=1)
    e = (s + timedelta(days=32)).replace(day=1)
    return s, e


@router.get("/spend")
async def spend(conn: Tenant, uid: UserId, p: Auth, st: State,
                period: Literal["week", "month", "last_month", "year"] | None = "month",
                start: date | None = None, end: date | None = None,
                group_by: Literal["root", "tag"] = "root") -> dict[str, Any]:
    s, e = _period(period, start, end)
    return await reports.rollup_by_tag(conn, uid, st.units, p.locale, kind="expense", start=s, end=e, group_by=group_by)


@router.get("/income")
async def income(conn: Tenant, uid: UserId, p: Auth, st: State,
                 period: Literal["week", "month", "last_month", "year"] | None = "month",
                 start: date | None = None, end: date | None = None,
                 group_by: Literal["root", "tag"] = "root") -> dict[str, Any]:
    s, e = _period(period, start, end)
    return await reports.rollup_by_tag(conn, uid, st.units, p.locale, kind="income", start=s, end=e, group_by=group_by)


@router.get("/off-report")
async def off_report(conn: Tenant, uid: UserId, p: Auth, st: State,
                     period: Literal["week", "month", "last_month", "year"] | None = "month",
                     start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Savings deposits, investment buys, transfers — the flows excluded from spend/income, by purpose."""
    s, e = _period(period, start, end)
    return await reports.rollup_by_tag(conn, uid, st.units, p.locale, kind="off_report", start=s, end=e, group_by="tag")


@router.get("/net-worth")
async def net_worth(conn: Tenant, uid: UserId, p: Auth, st: State, currency: str | None = Query(default=None)) -> dict[str, Any]:
    return await reports.net_worth(conn, uid, st.units, p.locale, currency or p.default_currency)
