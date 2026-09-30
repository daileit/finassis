"""Balances and simple reports, served from period_rollups with a ledger fallback for the current day."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from ..db import Conn
from ..money import ZERO_UUID, UnitRegistry, money_envelope

ZERO = uuid.UUID(ZERO_UUID)


async def balances(conn: Conn, user_id: uuid.UUID, units: UnitRegistry, locale: str, at: datetime | None = None) -> list[dict[str, Any]]:
    """Ledger-valued balances per account × currency. Uses the latest snapshot ≤ at plus postings after its watermark."""
    at = at or datetime.now(timezone.utc)
    rows = await conn.fetch(
        """WITH snap AS (
             SELECT DISTINCT ON (account_id, currency) account_id, currency, balance, last_posting_id
             FROM balance_snapshots WHERE user_id = $1 AND as_of <= ($2::timestamptz)::date
             ORDER BY account_id, currency, as_of DESC
           )
           SELECT a.id AS account_id, a.name, a.type, a.is_liability, a.is_system, a.liquidity, a.currency AS account_currency,
                  p.currency,
                  COALESCE(s.balance, 0) + COALESCE(sum(p.amount) FILTER (WHERE p.id > COALESCE(s.last_posting_id, 0) AND p.occurred_at <= $2::timestamptz), 0) AS balance
           FROM accounts a
           LEFT JOIN postings p ON p.account_id = a.id AND p.user_id = a.user_id
           LEFT JOIN snap s ON s.account_id = a.id AND s.currency = p.currency
           WHERE a.user_id = $1 AND NOT a.is_archived AND a.valuation_mode = 'ledger'
           GROUP BY a.id, p.currency, s.balance, s.last_posting_id
           ORDER BY a.is_system, a.created_at""",
        user_id, at,
    )
    out = []
    for r in rows:
        cur = r["currency"] or r["account_currency"]
        bal = Decimal(r["balance"] or 0)
        out.append({
            "account_id": r["account_id"], "name": r["name"], "type": r["type"], "is_liability": r["is_liability"],
            "is_system": r["is_system"], "liquidity": r["liquidity"],
            "balance": money_envelope(bal, units.get(cur), locale), "as_of": at, "source": "ledger", "confidence": "exact",
        })
    return out


async def rollup_by_tag(conn: Conn, user_id: uuid.UUID, units: UnitRegistry, locale: str, *, kind: str,
                        start: date, end: date, group_by: str = "root") -> dict[str, Any]:
    """Spend / income / off-report flows between start (incl.) and end (excl.), from day rollups.

    Leg convention (ledger.build_simple): the tag-only leg of an expense is +X (money went *to* the tag),
    of an income −X. So spend = +net, income = −net over tag-only legs (account_id = sentinel).
    off_report flows (savings deposit, investment buy, transfers) live on account legs that also carry the tag,
    or on ± tag-only legs of a detected pair; the meaningful number is the inflow, so we sum `credit` over all legs."""
    if kind == "off_report":
        rows = await conn.fetch(
            """SELECT COALESCE(r.system_key, g.system_key) AS root_key, g.system_key AS tag_key, g.name AS tag_name, g.id AS tag_id,
                      COALESCE(r.id, g.id) AS root_id, pr.currency, sum(pr.credit) AS net, sum(pr.posting_count) AS n
               FROM period_rollups pr JOIN tags g ON g.id = pr.tag_id LEFT JOIN tags r ON r.id = g.root_id
               WHERE pr.user_id = $1 AND pr.period_kind = 'day' AND pr.period_start >= $2 AND pr.period_start < $3
                 AND COALESCE(g.kind, r.kind) = 'off_report'
               GROUP BY 1,2,3,4,5,6 ORDER BY 1,2""",
            user_id, start, end,
        )
    else:
        rows = await conn.fetch(
            """SELECT COALESCE(r.system_key, g.system_key) AS root_key, g.system_key AS tag_key, g.name AS tag_name, g.id AS tag_id,
                      COALESCE(r.id, g.id) AS root_id, pr.currency, sum(pr.net) AS net, sum(pr.posting_count) AS n
               FROM period_rollups pr JOIN tags g ON g.id = pr.tag_id LEFT JOIN tags r ON r.id = g.root_id
               WHERE pr.user_id = $1 AND pr.period_kind = 'day' AND pr.period_start >= $2 AND pr.period_start < $3
                 AND pr.account_id = $4 AND COALESCE(g.kind, r.kind) = $5
               GROUP BY 1,2,3,4,5,6 ORDER BY 1,2""",
            user_id, start, end, ZERO, kind,
        )
    sign = Decimal(-1) if kind == "income" else Decimal(1)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    totals: dict[str, Decimal] = {}
    for r in rows:
        amt = Decimal(r["net"]) * sign
        cur = r["currency"]
        totals[cur] = totals.get(cur, Decimal(0)) + amt
        key = (r["root_key"], cur) if group_by == "root" else (r["tag_key"] or str(r["tag_id"]), cur)
        g = groups.setdefault(key, {"key": key[0], "root_key": r["root_key"], "currency": cur, "amount": Decimal(0), "count": 0,
                                    "tag_id": r["root_id"] if group_by == "root" else r["tag_id"], "name": r["tag_name"]})
        g["amount"] += amt
        g["count"] += int(r["n"])
    items = []
    for g in groups.values():
        items.append({**g, "amount": money_envelope(g["amount"], units.get(g["currency"]), locale)})
    items.sort(key=lambda x: -abs(x["amount"]["amount"]))
    return {
        "kind": kind, "from": start, "to": end, "group_by": group_by,
        "totals": [money_envelope(v, units.get(c), locale) for c, v in totals.items()],
        "items": items, "source": "rollup", "confidence": "exact",
    }


async def net_worth(conn: Conn, user_id: uuid.UUID, units: UnitRegistry, locale: str, currency: str) -> dict[str, Any]:
    """Phase 0: ledger-valued accounts only, per native currency, converted with the latest FX rate when available."""
    bals = await balances(conn, user_id, units, locale)
    assets: dict[str, Decimal] = {}
    liabs: dict[str, Decimal] = {}
    for b in bals:
        cur = b["balance"]["currency"]
        amt = Decimal(b["balance"]["amount"]) / (Decimal(10) ** b["balance"]["decimals"])
        (liabs if b["is_liability"] else assets)[cur] = (liabs if b["is_liability"] else assets).get(cur, Decimal(0)) + amt
    fx_used = []
    total_a = total_l = Decimal(0)
    unknown = []
    for bucket, sink in ((assets, "a"), (liabs, "l")):
        for cur, amt in bucket.items():
            rate = Decimal(1)
            if cur != currency:
                row = await conn.fetchrow(
                    "SELECT rate, as_of FROM fx_rates WHERE base = $1 AND quote = $2 ORDER BY as_of DESC LIMIT 1", cur, currency
                )
                if row is None:
                    inv = await conn.fetchrow(
                        "SELECT rate, as_of FROM fx_rates WHERE base = $1 AND quote = $2 ORDER BY as_of DESC LIMIT 1", currency, cur
                    )
                    if inv is None:
                        unknown.append(cur)
                        continue
                    rate = Decimal(1) / Decimal(inv["rate"])
                    fx_used.append({"from": cur, "to": currency, "rate": str(rate), "rate_as_of": inv["as_of"]})
                else:
                    rate = Decimal(row["rate"])
                    fx_used.append({"from": cur, "to": currency, "rate": str(rate), "rate_as_of": row["as_of"]})
            if sink == "a":
                total_a += amt * rate
            else:
                total_l += -amt * rate if amt < 0 else amt * rate
    unit = units.get(currency)
    return {
        "currency": currency,
        "total_assets": money_envelope(total_a, unit, locale),
        "total_liabilities": money_envelope(total_l, unit, locale),
        "net_worth": money_envelope(total_a - total_l, unit, locale),
        "by_currency": {"assets": {c: str(v) for c, v in assets.items()}, "liabilities": {c: str(v) for c, v in liabs.items()}},
        "fx": fx_used, "unconverted_currencies": unknown,
        "as_of": datetime.now(timezone.utc), "source": "ledger", "confidence": "exact" if not unknown else "medium",
        "note": "Phase 0: ledger-valued accounts only; mark-to-market assets arrive in Phase 3",
    }
