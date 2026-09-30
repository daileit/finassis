"""Turn DB rows into API responses with money envelopes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..money import UnitRegistry, money_envelope, quantity_envelope


def transaction(tx: dict[str, Any], units: UnitRegistry, locale: str) -> dict[str, Any]:
    posts = []
    for p in tx["postings"]:
        unit = units.get(p["currency"])
        d = {
            "id": p["id"], "account_id": p["account_id"], "account_name": p.get("account_name"),
            "tag_id": p["tag_id"], "tag_key": p.get("tag_key"), "tag_name": p.get("tag_name"), "root_key": p.get("root_key"),
            "tag_kind": p.get("tag_kind"), "amount": money_envelope(Decimal(p["amount_raw"]), unit, locale),
            "tag_source": p.get("tag_source"), "tag_confidence": p.get("tag_confidence"),
        }
        if p.get("quantity") is not None and p.get("unit"):
            d["quantity"] = quantity_envelope(Decimal(p["quantity"]), units.get(p["unit"]))
        posts.append(d)
    # summary: the account leg(s) and the primary tag
    tag_leg = next((p for p in posts if p["account_id"] is None), None) or next((p for p in posts if p["tag_id"]), None)
    return {
        "id": tx["id"], "occurred_at": tx["occurred_at"], "booked_at": tx["booked_at"], "description": tx.get("description"),
        "status": tx["status"], "source": tx["source"], "labels": list(tx.get("labels") or []), "pair_id": tx.get("pair_id"),
        "reverses_id": tx.get("reverses_id"), "income_stream_id": tx.get("income_stream_id"),
        "tag": {k: tag_leg[k] for k in ("tag_id", "tag_key", "tag_name", "root_key", "tag_kind", "tag_source", "tag_confidence")} if tag_leg else None,
        "postings": posts,
    }


def account(a: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    if out.get("expected_refresh_interval") is not None:
        out["expected_refresh_interval"] = str(out["expected_refresh_interval"])
    return out


def tag(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": t["id"], "system_key": t.get("system_key"), "name": t.get("name"), "label": t.get("label"),
        "root_id": t.get("root_id"), "root_key": t.get("root_key"), "kind": t.get("effective_kind") or t.get("kind"),
        "is_custom": t["is_custom"], "is_root": t.get("root_id") is None, "default_state": t.get("default_state"),
        "is_hidden": t.get("is_hidden", False), "display_name_override": t.get("display_name_override"), "region": t.get("region"),
    }
