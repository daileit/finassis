"""Idempotent seed loader (ADR-025): units, tags, plans."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import get_behaviour, get_settings
from ..db import Conn, Database
from ..logging import get_logger

log = get_logger(__name__)


def _read(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


async def _needs_apply(conn: Conn, file: str, version: int, digest: str) -> bool:
    row = await conn.fetchrow("SELECT version, content_hash FROM seed_versions WHERE file = $1", file)
    return row is None or row["version"] != version or row["content_hash"] != digest


async def _mark(conn: Conn, file: str, version: int, digest: str) -> None:
    await conn.execute(
        """INSERT INTO seed_versions(file, version, content_hash) VALUES ($1,$2,$3)
           ON CONFLICT (file) DO UPDATE SET version = EXCLUDED.version, content_hash = EXCLUDED.content_hash,
           applied_at = now()""",
        file, version, digest,
    )


async def load_units(conn: Conn, path: Path) -> int:
    data, digest = _read(path)
    if not await _needs_apply(conn, "units.json", data["version"], digest):
        return 0
    n = 0
    for u in data["units"]:
        await conn.execute(
            """INSERT INTO units(code, measure, factor_to_base, decimals, symbol, is_system)
               VALUES ($1,$2,$3,$4,$5,true)
               ON CONFLICT (code) DO UPDATE SET measure = EXCLUDED.measure, factor_to_base = EXCLUDED.factor_to_base,
                 decimals = EXCLUDED.decimals, symbol = EXCLUDED.symbol, is_system = true, is_deprecated = false""",
            u["code"], u["measure"],
            Decimal(u["factor_to_base"]) if u["factor_to_base"] is not None else None,
            int(u["decimals"]), u.get("symbol"),
        )
        n += 1
    codes = [u["code"] for u in data["units"]]
    await conn.execute("UPDATE units SET is_deprecated = true WHERE is_system AND user_id IS NULL AND NOT (code = ANY($1))", codes)
    await _mark(conn, "units.json", data["version"], digest)
    return n


async def load_tags(conn: Conn, path: Path) -> int:
    data, digest = _read(path)
    if not await _needs_apply(conn, "tags.json", data["version"], digest):
        return 0
    n = 0
    seen: list[str] = []
    for order, root in enumerate(data["roots"]):
        root_id = await conn.fetchval(
            """INSERT INTO tags(system_key, kind, is_custom, region, sort_order)
               VALUES ($1,$2,false,NULL,$3)
               ON CONFLICT (system_key) WHERE system_key IS NOT NULL
               DO UPDATE SET kind = EXCLUDED.kind, sort_order = EXCLUDED.sort_order, is_deprecated = false
               RETURNING id""",
            root["key"], root["kind"], order * 100,
        )
        seen.append(root["key"])
        n += 1
        for corder, child in enumerate(root["children"]):
            child_id = await conn.fetchval(
                """INSERT INTO tags(system_key, root_id, is_custom, default_state, region, sort_order)
                   VALUES ($1,$2,false,$3,$4,$5)
                   ON CONFLICT (system_key) WHERE system_key IS NOT NULL
                   DO UPDATE SET root_id = EXCLUDED.root_id, default_state = EXCLUDED.default_state,
                     region = EXCLUDED.region, sort_order = EXCLUDED.sort_order, is_deprecated = false
                   RETURNING id""",
                child["key"], root_id, child["default"], child.get("region"), order * 100 + corder + 1,
            )
            seen.append(child["key"])
            n += 1
            await conn.execute("DELETE FROM tag_keywords WHERE tag_id = $1", child_id)
            for kw in child.get("keywords", []):
                await conn.execute(
                    "INSERT INTO tag_keywords(tag_id, keyword) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                    child_id, kw.strip().lower(),
                )
    await conn.execute(
        "UPDATE tags SET is_deprecated = true WHERE NOT is_custom AND system_key IS NOT NULL AND NOT (system_key = ANY($1))",
        seen,
    )
    await _mark(conn, "tags.json", data["version"], digest)
    return n


async def ensure_default_plan(conn: Conn) -> None:
    limits = get_behaviour().free_plan_limits
    await conn.execute(
        """INSERT INTO plans(id, name, limits, is_default) VALUES ('free','Free',$1,true)
           ON CONFLICT (id) DO UPDATE SET limits = EXCLUDED.limits""",
        limits,
    )


async def load_all(db: Database) -> None:
    seeds_dir = get_settings().seeds_dir
    async with db.admin_tx() as conn:
        u = await load_units(conn, seeds_dir / "units.json")
        t = await load_tags(conn, seeds_dir / "tags.json")
        await ensure_default_plan(conn)
    log.info("seeds.loaded", units_upserted=u, tags_upserted=t)
