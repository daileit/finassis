"""Tags: combined list (system ∪ customs, prefs applied), custom children, resolution, suggestions."""

from __future__ import annotations

import uuid
from typing import Any

from ..db import Conn
from ..errors import Conflict, NotFound, UnknownTag, Validation
from ..i18n import t as tr

SYSTEM_TAG_COLS = """t.id, t.user_id, t.system_key, t.name, t.root_id, t.kind, t.is_custom, t.default_state,
  t.region, t.is_deprecated, t.sort_order,
  r.system_key AS root_key, COALESCE(t.kind, r.kind) AS effective_kind"""


async def ensure_default_tag_prefs(conn: Conn, user_id: uuid.UUID) -> None:
    """Hide system children whose default is 'off' or 'sys' for a new user."""
    await conn.execute(
        """INSERT INTO tag_prefs(user_id, tag_id, is_hidden)
           SELECT $1::uuid, id, true FROM tags WHERE NOT is_custom AND default_state IN ('off','sys')
           ON CONFLICT DO NOTHING""",
        user_id,
    )


async def list_tags(conn: Conn, user_id: uuid.UUID, locale: str, include_hidden: bool = False) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        f"""SELECT {SYSTEM_TAG_COLS}, p.is_hidden, p.display_name_override, p.sort_order AS pref_sort
            FROM tags t
            LEFT JOIN tags r ON r.id = t.root_id
            LEFT JOIN tag_prefs p ON p.tag_id = t.id AND p.user_id = $1
            WHERE (t.user_id IS NULL OR t.user_id = $1) AND NOT t.is_deprecated
            ORDER BY COALESCE(r.sort_order, t.sort_order), t.sort_order, t.name""",
        user_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        hidden = bool(d.pop("is_hidden") or False)
        if hidden and not include_hidden:
            continue
        d["is_hidden"] = hidden
        d["label"] = d["display_name_override"] or d["name"] or tr(locale, f"tag.{d['system_key']}")
        out.append(d)
    return out


async def get_tag(conn: Conn, user_id: uuid.UUID, tag_id: uuid.UUID) -> dict[str, Any]:
    row = await conn.fetchrow(
        f"SELECT {SYSTEM_TAG_COLS} FROM tags t LEFT JOIN tags r ON r.id = t.root_id WHERE t.id = $1 AND (t.user_id IS NULL OR t.user_id = $2)",
        tag_id, user_id,
    )
    if row is None:
        raise NotFound("tag not found")
    return dict(row)


async def resolve_tag(conn: Conn, user_id: uuid.UUID, ref: str, locale: str = "en") -> dict[str, Any]:
    """Accept a tag id, a system_key, or a custom tag name. Raise UnknownTag with suggestions otherwise."""
    ref = ref.strip()
    try:
        tid = uuid.UUID(ref)
        return await get_tag(conn, user_id, tid)
    except ValueError:
        pass
    row = await conn.fetchrow(
        f"""SELECT {SYSTEM_TAG_COLS} FROM tags t LEFT JOIN tags r ON r.id = t.root_id
            WHERE NOT t.is_deprecated AND (
                  (t.system_key = $1)
               OR (t.is_custom AND t.user_id = $2 AND lower(t.name) = lower($1)))
            ORDER BY t.is_custom DESC LIMIT 1""",
        ref, user_id,
    )
    if row:
        return dict(row)
    suggestions = await suggest_tags(conn, user_id, ref, locale)
    root_hint = suggestions[0]["root_key"] if suggestions else None
    raise UnknownTag(tr(locale, "error.unknown_tag", name=ref), name=ref, suggestions=suggestions, create_as_child_of=root_hint)


async def suggest_tags(conn: Conn, user_id: uuid.UUID, q: str, locale: str, limit: int = 3) -> list[dict[str, Any]]:
    """Trigram similarity over system keys, custom names, and keyword dictionary (no LLM)."""
    rows = await conn.fetch(
        """WITH cand AS (
             SELECT t.id, greatest(
                      similarity(coalesce(t.system_key,''), $2),
                      similarity(coalesce(t.name,''), $2),
                      coalesce((SELECT max(similarity(k.keyword, $2)) FROM tag_keywords k WHERE k.tag_id = t.id), 0)
                    ) AS score
             FROM tags t
             WHERE NOT t.is_deprecated AND (t.user_id IS NULL OR t.user_id = $1) AND t.root_id IS NOT NULL
           )
           SELECT t.id, t.system_key, t.name, r.system_key AS root_key, c.score
           FROM cand c JOIN tags t ON t.id = c.id JOIN tags r ON r.id = t.root_id
           WHERE c.score > 0.15 ORDER BY c.score DESC LIMIT $3""",
        user_id, q.lower(), limit,
    )
    return [
        {"tag_id": str(r["id"]), "system_key": r["system_key"], "name": r["name"], "root_key": r["root_key"],
         "label": r["name"] or tr(locale, f"tag.{r['system_key']}"), "score": round(float(r["score"]), 3)}
        for r in rows
    ]


async def create_custom_tag(conn: Conn, user_id: uuid.UUID, name: str, root_ref: str) -> dict[str, Any]:
    name = name.strip()
    if not name or len(name) > 60:
        raise Validation("tag name must be 1–60 characters")
    root = await conn.fetchrow("SELECT id, kind FROM tags WHERE system_key = $1 AND root_id IS NULL", root_ref)
    if root is None:
        raise Validation(f"unknown root tag {root_ref}")
    dup = await conn.fetchval(
        "SELECT 1 FROM tags WHERE is_custom AND user_id = $1 AND root_id = $2 AND lower(name) = lower($3)", user_id, root["id"], name
    )
    if dup:
        raise Conflict("a custom tag with that name already exists under this root")
    tid = await conn.fetchval(
        "INSERT INTO tags(user_id, name, root_id, is_custom) VALUES ($1,$2,$3,true) RETURNING id", user_id, name, root["id"]
    )
    return await get_tag(conn, user_id, tid)


async def update_tag_prefs(conn: Conn, user_id: uuid.UUID, tag_id: uuid.UUID, *, is_hidden: bool | None = None,
                           display_name_override: str | None = None, name: str | None = None) -> dict[str, Any]:
    tag = await get_tag(conn, user_id, tag_id)
    if tag["is_custom"]:
        if name:
            await conn.execute("UPDATE tags SET name = $1 WHERE id = $2 AND user_id = $3", name.strip(), tag_id, user_id)
    if is_hidden is not None or display_name_override is not None:
        await conn.execute(
            """INSERT INTO tag_prefs(user_id, tag_id, is_hidden, display_name_override)
               VALUES ($1,$2,COALESCE($3,false),$4)
               ON CONFLICT (user_id, tag_id) DO UPDATE SET
                 is_hidden = COALESCE($3, tag_prefs.is_hidden),
                 display_name_override = COALESCE($4, tag_prefs.display_name_override)""",
            user_id, tag_id, is_hidden, display_name_override,
        )
    return await get_tag(conn, user_id, tag_id)


async def delete_custom_tag(conn: Conn, user_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    tag = await get_tag(conn, user_id, tag_id)
    if not tag["is_custom"]:
        raise Validation("system tags cannot be deleted; hide them instead")
    # postings re-tag to root (ledger rows are immutable except tag re-assignment, which is a classification, not a value)
    await conn.execute("UPDATE postings SET tag_id = $1 WHERE tag_id = $2 AND user_id = $3", tag["root_id"], tag_id, user_id)
    await conn.execute("DELETE FROM tags WHERE id = $1 AND user_id = $2", tag_id, user_id)


async def untagged_id(conn: Conn) -> uuid.UUID:
    return await conn.fetchval("SELECT id FROM tags WHERE system_key = 'untagged'")  # type: ignore[no-any-return]
