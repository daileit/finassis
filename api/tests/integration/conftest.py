"""Integration tests need a Postgres 16 with pgvector and the application role already provisioned
(api/db/init/*). They create a scratch database per session, install extensions there (superuser),
apply schema.sql as the app role, load seeds, and drop the database afterwards.

Env:
  PG_SUPER_URL           superuser DSN (CREATE DATABASE, extensions). Falls back to FINASSIS_DATABASE_URL.
  FINASSIS_DATABASE_URL  the application role DSN — required
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from finassis.config import Settings, get_settings
from finassis.db import Database
from finassis.domain import seeds

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

APP_URL = os.environ.get("FINASSIS_DATABASE_URL")
SUPER_URL = os.environ.get("PG_SUPER_URL") or APP_URL
INIT_DIR = Path(__file__).resolve().parents[2] / "db" / "init"


def _with_db(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture(scope="session")
def scratch_name() -> str:
    if not APP_URL or not SUPER_URL:
        pytest.skip("FINASSIS_DATABASE_URL not set")
    return f"finassis_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
async def database(scratch_name: str) -> AsyncIterator[Database]:
    assert SUPER_URL and APP_URL
    app_role = APP_URL.split("//", 1)[1].split(":", 1)[0]
    su = await asyncpg.connect(SUPER_URL)
    await su.execute(f'CREATE DATABASE "{scratch_name}" OWNER "{app_role}"')
    await su.close()

    # superuser: extensions in the scratch db (mirrors api/db/init on a fresh cluster)
    su_db = await asyncpg.connect(_with_db(SUPER_URL, scratch_name))
    await su_db.execute((INIT_DIR / "00-extensions.sql").read_text(encoding="utf-8"))
    await su_db.close()

    # app role: the application schema (what Alembic 0001 does)
    app_db = await asyncpg.connect(_with_db(APP_URL, scratch_name))
    await app_db.execute(get_settings().schema_sql.read_text(encoding="utf-8"))
    await app_db.close()

    get_settings.cache_clear()
    os.environ["FINASSIS_DATABASE_URL"] = _with_db(APP_URL, scratch_name)
    os.environ.pop("FINASSIS_DATABASE_URL_APP", None)
    os.environ.pop("FINASSIS_DATABASE_URL_ADMIN", None)
    db = Database(Settings())
    await db.connect()
    await seeds.load_all(db)
    yield db
    await db.close()

    su = await asyncpg.connect(SUPER_URL)
    await su.execute(f'DROP DATABASE "{scratch_name}" WITH (FORCE)')
    await su.close()
