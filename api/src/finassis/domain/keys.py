"""API keys: generation, hashing, lookup. Plaintext is shown once; only the sha256 is stored."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..db import Conn
from ..errors import NotFound

PREFIX = {"user": "fk_", "admin": "fa_", "channel": "fc_"}
ALL_USER_SCOPES = [
    "ledger:read", "ledger:write", "accounts:write", "tags:write", "reports:read",
    "annotations:write", "interactions:write", "keys:manage", "raw:write", "recipes:propose",
]


def generate(kind: str = "user") -> str:
    return PREFIX[kind] + secrets.token_urlsafe(32)


def hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


@dataclass
class Principal:
    key_id: uuid.UUID
    kind: str  # user | admin | channel
    user_id: uuid.UUID | None
    scopes: list[str] = field(default_factory=list)
    locale: str = "en"
    default_currency: str = "USD"
    timezone: str = "Asia/Ho_Chi_Minh"
    is_admin: bool = False
    act_as: uuid.UUID | None = None

    @property
    def effective_user_id(self) -> uuid.UUID | None:
        return self.act_as or self.user_id

    def has(self, scope: str) -> bool:
        return self.is_admin or self.kind == "admin" or scope in self.scopes or "*" in self.scopes


async def create_key(conn: Conn, user_id: uuid.UUID | None, kind: str, name: str, scopes: list[str]) -> tuple[dict[str, Any], str]:
    plain = generate(kind)
    row = await conn.fetchrow(
        """INSERT INTO api_keys(user_id, kind, name, scopes, key_prefix, key_hash)
           VALUES ($1,$2,$3,$4,$5,$6)
           RETURNING id, user_id, kind, name, scopes, key_prefix, created_at, expires_at, last_used_at""",
        user_id, kind, name, scopes, plain[:8], hash_key(plain),
    )
    assert row is not None
    return dict(row), plain


async def list_keys(conn: Conn, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT id, kind, name, scopes, key_prefix, created_at, expires_at, last_used_at, revoked_at
           FROM api_keys WHERE user_id = $1 ORDER BY created_at DESC""",
        user_id,
    )
    return [dict(r) for r in rows]


async def revoke_key(conn: Conn, user_id: uuid.UUID, key_id: uuid.UUID) -> None:
    n = await conn.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND user_id = $2 AND revoked_at IS NULL", key_id, user_id
    )
    if n.endswith("0"):
        raise NotFound("key not found")


async def authenticate(conn: Conn, plain: str) -> Principal | None:
    """Admin-connection lookup (api_keys is RLS-protected). Returns None when invalid/revoked/expired."""
    row = await conn.fetchrow(
        """SELECT k.id, k.kind, k.user_id, k.scopes, u.locale, u.default_currency, u.timezone, u.is_admin, u.is_paused
           FROM api_keys k LEFT JOIN users u ON u.id = k.user_id
           WHERE k.key_hash = $1 AND k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at > now())""",
        hash_key(plain),
    )
    if row is None or row["is_paused"]:
        return None
    await conn.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE id = $1 AND (last_used_at IS NULL OR last_used_at < now() - interval '60 seconds')",
        row["id"],
    )
    return Principal(
        key_id=row["id"], kind=row["kind"], user_id=row["user_id"], scopes=list(row["scopes"] or []),
        locale=row["locale"] or "en", default_currency=row["default_currency"] or "USD",
        timezone=row["timezone"] or "Asia/Ho_Chi_Minh", is_admin=bool(row["is_admin"]),
    )
