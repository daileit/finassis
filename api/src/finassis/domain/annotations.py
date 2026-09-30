from __future__ import annotations

import uuid
from typing import Any

from ..db import Conn
from ..errors import NotFound


async def list_annotations(conn: Conn, user_id: uuid.UUID, target_type: str | None = None, target_id: str | None = None,
                           limit: int = 100) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT * FROM annotations WHERE user_id = $1
             AND ($2::text IS NULL OR target_type = $2) AND ($3::text IS NULL OR target_id = $3)
             AND (valid_until IS NULL OR valid_until > now())
           ORDER BY created_at DESC LIMIT $4""",
        user_id, target_type, target_id, limit,
    )
    return [dict(r) for r in rows]


async def add_annotation(conn: Conn, user_id: uuid.UUID, *, target_type: str, target_id: str | None, body: str,
                         author_kind: str, author_name: str | None, tags: list[str] | None = None, valid_until: Any = None) -> dict[str, Any]:
    row = await conn.fetchrow(
        """INSERT INTO annotations(user_id, target_type, target_id, body, author_kind, author_name, tags, valid_until)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *""",
        user_id, target_type, target_id, body, author_kind, author_name, tags or [], valid_until,
    )
    assert row is not None
    return dict(row)


async def delete_annotation(conn: Conn, user_id: uuid.UUID, annotation_id: uuid.UUID) -> None:
    n = await conn.execute("DELETE FROM annotations WHERE id = $1 AND user_id = $2", annotation_id, user_id)
    if n.endswith("0"):
        raise NotFound("annotation not found")
