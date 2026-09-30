"""Alembic environment: synchronous psycopg (v3) engine so a multi-statement schema.sql can be
executed as one script (asyncpg prepared statements cannot). Serialised with a session-level
advisory lock.

Careful: SQLAlchemy 2 autobegins a transaction on the first statement. If we take the lock and leave
that transaction open, Alembic sees an externally managed transaction and does NOT commit — the whole
migration silently rolls back on connection close. So we commit right after locking/unlocking."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, text

config = context.config


def _url() -> str:
    url = os.environ.get("FINASSIS_DATABASE_URL_ADMIN") or os.environ.get("FINASSIS_DATABASE_URL")
    if not url:
        url = "postgresql://finassis:finassis@localhost:5432/finassis"
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(hashtext('finassis.migrate'))"))
        connection.commit()  # end the autobegun txn; the advisory lock is session-level and survives
        try:
            context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(hashtext('finassis.migrate'))"))
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
