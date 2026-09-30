"""init: apply the hand-written api/db/schema.sql

Revision ID: 0001_init
Revises:
Create Date: 2026-10-01
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def upgrade() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    # schema.sql wraps itself in BEGIN/COMMIT; Alembic already runs us in a transaction.
    body = sql.replace("\nBEGIN;\n", "\n", 1)
    idx = body.rfind("\nCOMMIT;")
    if idx != -1:
        body = body[:idx] + "\n"
    # psycopg3 executes a multi-statement string in one round trip when there are no parameters
    raw = op.get_bind().connection.driver_connection
    with raw.cursor() as cur:
        cur.execute(body)


def downgrade() -> None:
    raise RuntimeError("0001_init is not reversible; drop the database instead")
