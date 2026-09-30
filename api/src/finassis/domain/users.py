"""Users and identities. Creation is privileged (admin connection); see docs/tech/08."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import Conn
from ..errors import NotFound, Validation
from .tags import ensure_default_tag_prefs


async def create_user(
    conn: Conn, *, locale: str = "en", default_currency: str = "USD", tz: str = "Asia/Ho_Chi_Minh",
    display_name: str | None = None, is_admin: bool = False,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """INSERT INTO users(locale, default_currency, timezone, display_name, is_admin)
           VALUES ($1,$2,$3,$4,$5) RETURNING *""",
        locale, default_currency, tz, display_name, is_admin,
    )
    assert row is not None
    user = dict(row)
    await conn.execute(
        "INSERT INTO user_plans(user_id, plan_id) SELECT $1, id FROM plans WHERE is_default LIMIT 1", user["id"]
    )
    await ensure_default_tag_prefs(conn, user["id"])
    return user


async def get_user(conn: Conn, user_id: uuid.UUID) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    if row is None:
        raise NotFound("user not found")
    return dict(row)


async def update_user(conn: Conn, user_id: uuid.UUID, **fields: Any) -> dict[str, Any]:
    allowed = {"locale", "default_currency", "timezone", "display_name", "personality_profile"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            vals.append(v)
            sets.append(f"{k} = ${len(vals) + 1}")
    if not sets:
        return await get_user(conn, user_id)
    row = await conn.fetchrow(f"UPDATE users SET {', '.join(sets)} WHERE id = $1 RETURNING *", user_id, *vals)
    if row is None:
        raise NotFound("user not found")
    return dict(row)


async def find_by_identity(conn: Conn, provider: str, provider_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """SELECT u.* FROM identities i JOIN users u ON u.id = i.user_id
           WHERE i.provider = $1 AND i.provider_id = $2""",
        provider, provider_id,
    )
    return dict(row) if row else None


async def link_identity(conn: Conn, user_id: uuid.UUID, provider: str, provider_id: str, display_name: str | None) -> None:
    await conn.execute(
        """INSERT INTO identities(provider, provider_id, user_id, display_name, is_primary)
           VALUES ($1,$2,$3,$4, NOT EXISTS (SELECT 1 FROM identities WHERE user_id = $3))
           ON CONFLICT (provider, provider_id) DO UPDATE SET display_name = EXCLUDED.display_name""",
        provider, provider_id, user_id, display_name,
    )


async def list_identities(conn: Conn, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await conn.fetch("SELECT provider, provider_id, display_name, is_primary, linked_at FROM identities WHERE user_id = $1", user_id)
    return [dict(r) for r in rows]


async def mint_link_code(conn: Conn, user_id: uuid.UUID, provider: str, ttl_minutes: int = 10) -> dict[str, Any]:
    code = secrets.token_hex(3).upper()  # 6 hex chars, easy to type
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    await conn.execute(
        "INSERT INTO link_codes(code, user_id, provider, expires_at) VALUES ($1,$2,$3,$4)", code, user_id, provider, expires
    )
    return {"code": code, "provider": provider, "expires_at": expires}


async def redeem_link_code(conn: Conn, code: str, provider: str, provider_id: str, display_name: str | None) -> dict[str, Any]:
    """Admin connection: attaches provider identity to the code's user."""
    row = await conn.fetchrow(
        """UPDATE link_codes SET used_at = now()
           WHERE code = $1 AND provider = $2 AND used_at IS NULL AND expires_at > now()
           RETURNING user_id""",
        code.upper(), provider,
    )
    if row is None:
        raise Validation("link code invalid or expired")
    await link_identity(conn, row["user_id"], provider, provider_id, display_name)
    return await get_user(conn, row["user_id"])
