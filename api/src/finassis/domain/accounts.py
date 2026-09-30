"""Accounts, aliases, system accounts on demand."""

from __future__ import annotations

import uuid
from typing import Any

from ..db import Conn
from ..errors import Conflict, NotFound, Validation

LIABILITY_TYPES = {"credit_card", "loan", "payable"}
SYSTEM_NAMES = {"receivable": "People I lent to", "payable": "People I owe"}


async def list_accounts(conn: Conn, user_id: uuid.UUID, include_archived: bool = False) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT a.*, COALESCE(array_agg(al.alias) FILTER (WHERE al.alias IS NOT NULL), '{}') AS aliases
           FROM accounts a LEFT JOIN account_aliases al ON al.account_id = a.id
           WHERE a.user_id = $1 AND ($2 OR NOT a.is_archived)
           GROUP BY a.id ORDER BY a.is_system, a.created_at""",
        user_id, include_archived,
    )
    return [dict(r) for r in rows]


async def get_account(conn: Conn, user_id: uuid.UUID, account_id: uuid.UUID) -> dict[str, Any]:
    row = await conn.fetchrow(
        """SELECT a.*, COALESCE(array_agg(al.alias) FILTER (WHERE al.alias IS NOT NULL), '{}') AS aliases
           FROM accounts a LEFT JOIN account_aliases al ON al.account_id = a.id
           WHERE a.id = $1 AND a.user_id = $2 GROUP BY a.id""",
        account_id, user_id,
    )
    if row is None:
        raise NotFound("account not found")
    return dict(row)


async def resolve_account(conn: Conn, user_id: uuid.UUID, ref: str) -> dict[str, Any]:
    """id, alias, or exact name (case-insensitive)."""
    ref = ref.strip()
    try:
        return await get_account(conn, user_id, uuid.UUID(ref))
    except ValueError:
        pass
    row = await conn.fetchrow(
        """SELECT a.id FROM accounts a
           LEFT JOIN account_aliases al ON al.account_id = a.id AND al.user_id = a.user_id
           WHERE a.user_id = $1 AND NOT a.is_archived AND (lower(al.alias) = lower($2) OR lower(a.name) = lower($2))
           LIMIT 1""",
        user_id, ref,
    )
    if row is None:
        raise NotFound(f"account '{ref}' not found")
    return await get_account(conn, user_id, row["id"])


async def create_account(conn: Conn, user_id: uuid.UUID, **f: Any) -> dict[str, Any]:
    if not f.get("name"):
        raise Validation("name is required")
    is_liability = f.get("is_liability")
    if is_liability is None:
        is_liability = f["type"] in LIABILITY_TYPES
    row = await conn.fetchrow(
        """INSERT INTO accounts(user_id, name, type, currency, valuation_mode, is_liability, liquidity, purpose,
                                labels, terms, institution, external_ref, expected_refresh_interval)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) RETURNING id""",
        user_id, f["name"], f["type"], f["currency"], f.get("valuation_mode", "ledger"), is_liability,
        f.get("liquidity", "liquid"), f.get("purpose"), f.get("labels") or [], f.get("terms"),
        f.get("institution"), f.get("external_ref"), f.get("expected_refresh_interval"),
    )
    assert row is not None
    for alias in f.get("aliases") or []:
        await add_alias(conn, user_id, row["id"], alias)
    return await get_account(conn, user_id, row["id"])


async def update_account(conn: Conn, user_id: uuid.UUID, account_id: uuid.UUID, **f: Any) -> dict[str, Any]:
    allowed = {"name", "liquidity", "purpose", "labels", "terms", "institution", "external_ref", "is_archived",
               "expected_refresh_interval", "valuation_mode"}
    sets, vals = [], []
    for k, v in f.items():
        if k in allowed and v is not None:
            vals.append(v)
            sets.append(f"{k} = ${len(vals) + 2}")
    if sets:
        n = await conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = $1 AND user_id = $2", account_id, user_id, *vals)
        if n.endswith("0"):
            raise NotFound("account not found")
    return await get_account(conn, user_id, account_id)


async def add_alias(conn: Conn, user_id: uuid.UUID, account_id: uuid.UUID, alias: str) -> None:
    alias = alias.strip()
    if not alias:
        raise Validation("alias is empty")
    try:
        await conn.execute("INSERT INTO account_aliases(user_id, alias, account_id) VALUES ($1,$2,$3)", user_id, alias, account_id)
    except Exception as e:  # asyncpg.UniqueViolationError
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise Conflict(f"alias '{alias}' already exists") from e
        raise


async def remove_alias(conn: Conn, user_id: uuid.UUID, alias: str) -> None:
    await conn.execute("DELETE FROM account_aliases WHERE user_id = $1 AND alias = $2", user_id, alias)


async def ensure_system_account(conn: Conn, user_id: uuid.UUID, kind: str, currency: str) -> dict[str, Any]:
    """receivable / payable, created on first use."""
    if kind not in SYSTEM_NAMES:
        raise Validation("not a system account type")
    row = await conn.fetchrow("SELECT id FROM accounts WHERE user_id = $1 AND type = $2 AND is_system", user_id, kind)
    if row:
        return await get_account(conn, user_id, row["id"])
    aid = await conn.fetchval(
        """INSERT INTO accounts(user_id, name, type, currency, is_liability, is_system, liquidity)
           VALUES ($1,$2,$3,$4,$5,true,'semi') RETURNING id""",
        user_id, SYSTEM_NAMES[kind], kind, currency, kind == "payable",
    )
    return await get_account(conn, user_id, aid)
