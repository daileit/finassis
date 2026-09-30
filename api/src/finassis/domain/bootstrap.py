"""Bootstrap admin: ensure one admin user exists; attach Telegram id if configured;
ensure an admin API key — from config, or generated and printed to stdout ONCE."""

from __future__ import annotations

import sys

from ..config import get_settings
from ..db import Database
from ..logging import get_logger
from . import keys, users

log = get_logger(__name__)


async def ensure_admin(db: Database) -> None:
    s = get_settings()
    async with db.admin_tx() as conn:
        admin = await conn.fetchrow("SELECT * FROM users WHERE is_admin ORDER BY created_at LIMIT 1")
        if admin is None:
            admin_d = await users.create_user(conn, locale="en", default_currency="USD", display_name="admin", is_admin=True)
            log.info("bootstrap.admin_user_created", user_id=str(admin_d["id"]))
        else:
            admin_d = dict(admin)
        if s.bootstrap_admin_telegram_id:
            await users.link_identity(conn, admin_d["id"], "telegram", s.bootstrap_admin_telegram_id, "bootstrap")

        existing = await conn.fetchval(
            "SELECT count(*) FROM api_keys WHERE kind = 'admin' AND revoked_at IS NULL AND user_id = $1", admin_d["id"]
        )
        if s.bootstrap_admin_api_key:
            await conn.execute(
                """INSERT INTO api_keys(user_id, kind, name, scopes, key_prefix, key_hash)
                   VALUES ($1,'admin','bootstrap',ARRAY['*'],$2,$3) ON CONFLICT (key_hash) DO NOTHING""",
                admin_d["id"], s.bootstrap_admin_api_key[:8], keys.hash_key(s.bootstrap_admin_api_key),
            )
        elif existing == 0:
            _, plain = await keys.create_key(conn, admin_d["id"], "admin", "bootstrap", ["*"])
            banner = f"\n{'=' * 72}\nBOOTSTRAP: admin API key = {plain}\nThis is printed once. Store it now.\n{'=' * 72}\n"
            print(banner, file=sys.stdout, flush=True)
            log.warning("bootstrap.admin_key_generated", user_id=str(admin_d["id"]))
