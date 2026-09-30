"""Integration tests need FINASSIS_DATABASE_URL pointing at a Postgres 16 with pgvector.
They create a scratch database per session, apply schema.sql, load seeds, and drop it afterwards."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from finassis.config import Settings, get_settings
from finassis.db import Database
from finassis.domain import seeds

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("FINASSIS_DATABASE_URL")


@pytest.fixture(scope="session")
def scratch_url() -> str:
    if not BASE_URL:
        pytest.skip("FINASSIS_DATABASE_URL not set")
    return BASE_URL.rsplit("/", 1)[0] + f"/finassis_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
async def database(scratch_url: str) -> AsyncIterator[Database]:
    admin = await asyncpg.connect(BASE_URL)
    name = scratch_url.rsplit("/", 1)[1]
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    conn = await asyncpg.connect(scratch_url)
    sql = get_settings().schema_sql.read_text(encoding="utf-8")
    await conn.execute(sql)
    await conn.close()
    get_settings.cache_clear()
    os.environ["FINASSIS_DATABASE_URL"] = scratch_url
    db = Database(Settings())
    await db.connect()
    await seeds.load_all(db)
    yield db
    await db.close()
    admin = await asyncpg.connect(BASE_URL)
    await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    await admin.close()
