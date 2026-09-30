"""asyncpg pools and tenant-scoped transactions.

Two pools:
  app   — role finassis_app in prod (RLS applies). Every use goes through tenant_tx(user_id),
          which opens a transaction and SET LOCAL app.user_id so RLS policies resolve.
  admin — role finassis_admin (BYPASSRLS). Privileged operations: user creation, bootstrap,
          seed loading, jobs iterating tenants.
In dev both DSNs may point at the same superuser; RLS still works because tenant_tx sets the GUC
and the superuser policies are simply not enforced (tests for RLS use the app role explicitly).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import asyncpg

from .config import Settings, get_settings
from .logging import get_logger

log = get_logger(__name__)

Conn = asyncpg.Connection


async def _init_conn(conn: asyncpg.Connection) -> None:
    # jsonb <-> python dict; numeric stays Decimal
    enc = lambda v: json.dumps(v, default=str)
    await conn.set_type_codec("jsonb", encoder=enc, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=enc, decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._app: asyncpg.Pool | None = None
        self._admin: asyncpg.Pool | None = None

    async def connect(self) -> None:
        s = self.settings
        self._app = await asyncpg.create_pool(
            s.app_dsn, min_size=s.db_pool_min, max_size=s.db_pool_max, init=_init_conn, command_timeout=30
        )
        if s.admin_dsn == s.app_dsn:
            self._admin = self._app
        else:
            self._admin = await asyncpg.create_pool(s.admin_dsn, min_size=1, max_size=4, init=_init_conn)
        log.info("db.connected", app=_redact(s.app_dsn), admin=_redact(s.admin_dsn))

    async def close(self) -> None:
        if self._app:
            await self._app.close()
        if self._admin and self._admin is not self._app:
            await self._admin.close()

    @property
    def app(self) -> asyncpg.Pool:
        assert self._app is not None, "database not connected"
        return self._app

    @property
    def admin(self) -> asyncpg.Pool:
        assert self._admin is not None, "database not connected"
        return self._admin

    @contextlib.asynccontextmanager
    async def tenant_tx(self, user_id: uuid.UUID | str) -> AsyncIterator[Conn]:
        """Transaction scoped to one tenant: RLS sees only this user's rows."""
        async with self.app.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', $1, true)", str(user_id))
            yield conn

    @contextlib.asynccontextmanager
    async def admin_tx(self) -> AsyncIterator[Conn]:
        async with self.admin.acquire() as conn, conn.transaction():
            yield conn

    @contextlib.asynccontextmanager
    async def admin_conn(self) -> AsyncIterator[Conn]:
        async with self.admin.acquire() as conn:
            yield conn


def _redact(dsn: str) -> str:
    if "@" in dsn and "//" in dsn:
        head, tail = dsn.split("//", 1)
        creds, host = tail.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{head}//{user}:***@{host}"
    return dsn


def record_to_dict(r: asyncpg.Record | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def dec(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))
