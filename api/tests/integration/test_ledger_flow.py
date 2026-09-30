"""End-to-end ledger flow against a real database: user → accounts → expense → self-transfer → reports."""

from __future__ import annotations

import pytest

from finassis.domain import accounts, ledger, reports, users
from finassis.money import UnitRegistry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _units(db) -> UnitRegistry:  # type: ignore[no-untyped-def]
    reg = UnitRegistry()
    async with db.admin_conn() as conn:
        rows = await conn.fetch("SELECT code, measure, factor_to_base, decimals, symbol, name FROM units")
    reg.update([dict(r) for r in rows])
    return reg


async def test_expense_and_transfer_reports(database) -> None:  # type: ignore[no-untyped-def]
    db = database
    units = await _units(db)
    async with db.admin_tx() as conn:
        u = await users.create_user(conn, locale="vi", default_currency="VND")
    uid = u["id"]
    async with db.tenant_tx(uid) as conn:
        await accounts.create_account(conn, uid, name="TCB", type="bank", currency="VND", aliases=["tcb"])
        await accounts.create_account(conn, uid, name="Cash", type="cash", currency="VND")
        # expense
        tx = await ledger.build_simple(conn, uid, units, "vi", "Asia/Ho_Chi_Minh",
                                       {"account": "tcb", "amount": -45000, "occurred_at": "2026-10-01", "tag": "coffee_drinks", "description": "Highlands"})
        row = await ledger.write_transaction(conn, uid, tx)
        assert len(row["postings"]) == 2
        # transfer, both legs at once
        tx2 = await ledger.build_simple(conn, uid, units, "vi", "Asia/Ho_Chi_Minh",
                                        {"account": "tcb", "amount": -2_000_000, "occurred_at": "2026-10-01", "counter_account": "Cash"})
        row2 = await ledger.write_transaction(conn, uid, tx2)
        assert {p["tag_key"] for p in row2["postings"]} == {"self_transfer"}

        bals = {b["name"]: b["balance"]["amount"] for b in await reports.balances(conn, uid, units, "vi")}
        assert bals["TCB"] == -2_045_000 and bals["Cash"] == 2_000_000

        from datetime import date
        spend = await reports.rollup_by_tag(conn, uid, units, "vi", kind="expense", start=date(2026, 9, 1), end=date(2026, 11, 1))
        assert spend["totals"][0]["amount"] == 45000  # transfer excluded
        off = await reports.rollup_by_tag(conn, uid, units, "vi", kind="off_report", start=date(2026, 9, 1), end=date(2026, 11, 1), group_by="tag")
        assert any(i["key"] == "self_transfer" for i in off["items"])


async def test_pair_detection_and_rls(database) -> None:  # type: ignore[no-untyped-def]
    db = database
    units = await _units(db)
    async with db.admin_tx() as conn:
        a = await users.create_user(conn, locale="en", default_currency="USD")
        b = await users.create_user(conn, locale="en", default_currency="USD")
    async with db.tenant_tx(a["id"]) as conn:
        await accounts.create_account(conn, a["id"], name="Chase", type="bank", currency="USD")
        await accounts.create_account(conn, a["id"], name="Ally", type="bank", currency="USD")
        t1 = await ledger.build_simple(conn, a["id"], units, "en", "UTC", {"account": "Chase", "amount": -50000, "occurred_at": "2026-10-02"})
        r1 = await ledger.write_transaction(conn, a["id"], t1)
        assert {p["tag_key"] for p in r1["postings"] if p["tag_key"]} == {"untagged"}
        t2 = await ledger.build_simple(conn, a["id"], units, "en", "UTC", {"account": "Ally", "amount": 50000, "occurred_at": "2026-10-03"})
        r2 = await ledger.write_transaction(conn, a["id"], t2)
        assert r2["pair_id"] is not None
        r1b = await ledger.get_transaction(conn, a["id"], r1["id"])
        assert r1b["pair_id"] == r2["pair_id"]
        assert {p["tag_key"] for p in r2["postings"] if p["tag_key"]} == {"self_transfer"}
    # RLS: user b sees none of a's accounts (only meaningful when the pool role is finassis_app; superuser bypasses)
    async with db.tenant_tx(b["id"]) as conn:
        role_super = await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        n = await conn.fetchval("SELECT count(*) FROM accounts")
        if not role_super:
            assert n == 0
