"""Interactions: pending questions with ≤ 6 options, rendered/resolved by any channel (tech/07)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..config import get_behaviour
from ..db import Conn
from ..errors import NotFound, Validation
from ..i18n import t as tr

RESOLVERS: dict[str, Any] = {}  # kind -> async fn(conn, user_id, interaction, choice) ; registered by domain modules


def register_resolver(kind: str):  # type: ignore[no-untyped-def]
    def deco(fn):  # type: ignore[no-untyped-def]
        RESOLVERS[kind] = fn
        return fn
    return deco


async def create(conn: Conn, user_id: uuid.UUID, *, kind: str, prompt_key: str, params: dict[str, Any] | None = None,
                 options: list[dict[str, Any]], subject_type: str | None = None, subject_id: str | None = None,
                 allow_free_text: bool = False, free_text_hint_key: str | None = None, priority: str = "today",
                 ttl_days: int | None = None) -> dict[str, Any]:
    if len(options) > 6:
        raise Validation("at most 6 options")
    ttl = ttl_days or get_behaviour().interaction_default_ttl_days
    row = await conn.fetchrow(
        """INSERT INTO interactions(user_id, kind, subject_type, subject_id, prompt, options, allow_free_text,
                                    free_text_hint_key, priority, expires_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now() + make_interval(days => $10)) RETURNING *""",
        user_id, kind, subject_type, subject_id, {"key": prompt_key, "params": params or {}}, options,
        allow_free_text, free_text_hint_key, priority, ttl,
    )
    assert row is not None
    return dict(row)


async def list_open(conn: Conn, user_id: uuid.UUID, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT * FROM interactions WHERE user_id = $1 AND status = 'open' AND ($2::text IS NULL OR kind = $2)
           ORDER BY CASE priority WHEN 'now' THEN 0 WHEN 'today' THEN 1 ELSE 2 END, created_at LIMIT $3""",
        user_id, kind, limit,
    )
    return [dict(r) for r in rows]


async def get(conn: Conn, user_id: uuid.UUID, interaction_id: uuid.UUID) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM interactions WHERE id = $1 AND user_id = $2", interaction_id, user_id)
    if row is None:
        raise NotFound("interaction not found")
    return dict(row)


async def resolve(conn: Conn, user_id: uuid.UUID, interaction_id: uuid.UUID, *, option_key: str | None = None,
                  text: str | None = None, via: str = "api") -> dict[str, Any]:
    it = await get(conn, user_id, interaction_id)
    if it["status"] != "open":
        raise Validation("interaction is not open", status=it["status"])
    if it["expires_at"] and it["expires_at"] < datetime.now(timezone.utc):
        await conn.execute("UPDATE interactions SET status = 'expired' WHERE id = $1", interaction_id)
        raise Validation("interaction expired")
    choice: dict[str, Any]
    if option_key is not None:
        opt = next((o for o in it["options"] if o.get("key") == option_key), None)
        if opt is None:
            raise Validation("unknown option", options=[o.get("key") for o in it["options"]])
        choice = {"option_key": option_key, "payload": opt.get("payload")}
    elif text is not None and it["allow_free_text"]:
        choice = {"text": text}
    else:
        raise Validation("option_key or text required")
    handler = RESOLVERS.get(it["kind"])
    result = await handler(conn, user_id, it, choice) if handler else None
    row = await conn.fetchrow(
        """UPDATE interactions SET status = 'resolved', resolution = $3, resolved_via = $4, resolved_at = now()
           WHERE id = $1 AND user_id = $2 RETURNING *""",
        interaction_id, user_id, {**choice, "result": result}, via,
    )
    assert row is not None
    return dict(row)


async def dismiss(conn: Conn, user_id: uuid.UUID, interaction_id: uuid.UUID, via: str = "api") -> dict[str, Any]:
    row = await conn.fetchrow(
        """UPDATE interactions SET status = 'dismissed', resolved_via = $3, resolved_at = now()
           WHERE id = $1 AND user_id = $2 AND status = 'open' RETURNING *""",
        interaction_id, user_id, via,
    )
    if row is None:
        raise NotFound("open interaction not found")
    return dict(row)


async def expire_stale(conn: Conn) -> int:
    n = await conn.execute("UPDATE interactions SET status = 'expired' WHERE status = 'open' AND expires_at < now()")
    return int(n.split()[-1])


def render(it: dict[str, Any], locale: str) -> dict[str, Any]:
    """Language-neutral row → rendered prompt + option labels for a channel."""
    prompt = it["prompt"]
    opts = []
    for o in it["options"]:
        label = o.get("label") or {}
        text = label.get("text") if isinstance(label, dict) else None
        key = label.get("key") if isinstance(label, dict) else None
        opts.append({"key": o["key"], "label": text or (tr(locale, key, **(o.get("params") or {})) if key else o["key"])})
    return {**it, "prompt_text": tr(locale, prompt["key"], **(prompt.get("params") or {})), "options_rendered": opts}


# built-in resolver: confirm_action just echoes; other kinds register from their modules
@register_resolver("confirm_action")
async def _confirm_action(conn: Conn, user_id: uuid.UUID, it: dict[str, Any], choice: dict[str, Any]) -> dict[str, Any]:
    return {"confirmed": choice.get("option_key") == "confirm"}


async def onboarding(conn: Conn, user_id: uuid.UUID) -> None:
    """Open the initial setup questions for a new user."""
    exists = await conn.fetchval("SELECT 1 FROM interactions WHERE user_id = $1 AND kind = 'onboarding_step' LIMIT 1", user_id)
    if exists:
        return
    await create(
        conn, user_id, kind="onboarding_step", prompt_key="interaction.onboarding.currency.prompt",
        options=[{"key": "VND", "label": {"text": "VND ₫"}}, {"key": "USD", "label": {"text": "USD $"}}],
        subject_type="profile", priority="now", ttl_days=30,
    )


@register_resolver("onboarding_step")
async def _onboarding(conn: Conn, user_id: uuid.UUID, it: dict[str, Any], choice: dict[str, Any]) -> dict[str, Any]:
    key = it["prompt"]["key"]
    if key.endswith("currency.prompt") and choice.get("option_key"):
        await conn.execute("UPDATE users SET default_currency = $1 WHERE id = $2", choice["option_key"], user_id)
        return {"default_currency": choice["option_key"]}
    return {}
