"""The ledger write path: transactions + postings, incremental rollups, pair detection, reversal.

Simple form:   {account, amount(signed minor units), currency?, occurred_at, description?, tag? | counter_account?}
Explicit form: {occurred_at, description?, postings: [{account?|tag?, amount, currency}]}
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ..config import get_behaviour
from ..db import Conn
from ..errors import NotFound, Unbalanced, Validation
from ..money import ZERO_UUID, UnitRegistry, from_minor
from . import accounts as acc
from . import tags as tg

ZERO = uuid.UUID(ZERO_UUID)


@dataclass
class Leg:
    amount: Decimal
    currency: str
    account_id: uuid.UUID | None = None
    tag_id: uuid.UUID | None = None
    tag_source: str | None = None
    tag_confidence: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    instrument_id: uuid.UUID | None = None


@dataclass
class TxInput:
    occurred_at: datetime
    legs: list[Leg]
    description: str | None = None
    labels: list[str] = field(default_factory=list)
    idempotency_key: str | None = None
    source: str = "api"
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_occurred_at(value: str | datetime | date, tz: str) -> datetime:
    """Date-only → 00:00 in the user's timezone; naive datetimes are interpreted in the user's tz."""
    zone = ZoneInfo(tz)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=zone)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=zone)
    s = value.strip()
    if len(s) == 10:
        return datetime.combine(date.fromisoformat(s), time.min, tzinfo=zone)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=zone)


async def build_simple(conn: Conn, user_id: uuid.UUID, units: UnitRegistry, locale: str, tz: str, body: dict[str, Any]) -> TxInput:
    account = await acc.resolve_account(conn, user_id, str(body["account"]))
    currency = body.get("currency") or account["currency"]
    if not units.has(currency) or not units.get(currency).is_money:
        raise Validation(f"unknown or non-money currency {currency}")
    amount = from_minor(int(body["amount"]), units.get(currency).decimals)
    if amount == 0:
        raise Validation("amount must be non-zero")
    if body.get("tag") and body.get("counter_account"):
        raise Validation("tag and counter_account are mutually exclusive")

    legs: list[Leg] = []
    if body.get("counter_account"):
        counter = await acc.resolve_account(conn, user_id, str(body["counter_account"]))
        if counter["id"] == account["id"]:
            raise Validation("counter_account must differ from account")
        default_key = "credit_card_payment" if (counter["is_liability"] or account["is_liability"]) else "self_transfer"
        tag = await tg.resolve_tag(conn, user_id, body.get("transfer_tag") or default_key, locale)
        legs.append(Leg(amount=amount, currency=currency, account_id=account["id"], tag_id=tag["id"], tag_source="caller", tag_confidence="exact"))
        legs.append(Leg(amount=-amount, currency=currency, account_id=counter["id"], tag_id=tag["id"], tag_source="caller", tag_confidence="exact"))
    else:
        tag_id: uuid.UUID | None = None
        tsrc = tconf = None
        if body.get("tag"):
            tag = await tg.resolve_tag(conn, user_id, str(body["tag"]), locale)
            if tag["effective_kind"] == "off_report" and tag["root_id"] is None:
                raise Validation("off_report root cannot be used directly; pick a child")
            tag_id, tsrc, tconf = tag["id"], "caller", "exact"
        else:
            tag_id = await tg.untagged_id(conn)
        legs.append(Leg(amount=amount, currency=currency, account_id=account["id"]))
        legs.append(Leg(amount=-amount, currency=currency, tag_id=tag_id, tag_source=tsrc, tag_confidence=tconf))
        q = body.get("quantity")
        if q:
            if not units.has(q["unit"]):
                raise Validation(f"unknown unit {q['unit']}")
            legs[0].quantity = Decimal(str(q["value"]))
            legs[0].unit = q["unit"]
            if body.get("instrument"):
                legs[0].instrument_id = uuid.UUID(str(body["instrument"]))

    return TxInput(
        occurred_at=parse_occurred_at(body["occurred_at"], tz), legs=legs, description=body.get("description"),
        labels=list(body.get("labels") or []), idempotency_key=body.get("idempotency_key"), source=body.get("source", "api"),
    )


async def build_explicit(conn: Conn, user_id: uuid.UUID, units: UnitRegistry, locale: str, tz: str, body: dict[str, Any]) -> TxInput:
    legs: list[Leg] = []
    for p in body["postings"]:
        cur = p.get("currency")
        if not cur:
            raise Validation("each posting needs a currency")
        if not units.has(cur) or not units.get(cur).is_money:
            raise Validation(f"unknown or non-money currency {cur}")
        leg = Leg(amount=from_minor(int(p["amount"]), units.get(cur).decimals), currency=cur)
        if p.get("account"):
            leg.account_id = (await acc.resolve_account(conn, user_id, str(p["account"])))["id"]
        if p.get("tag"):
            tag = await tg.resolve_tag(conn, user_id, str(p["tag"]), locale)
            if tag["effective_kind"] == "off_report" and tag["root_id"] is None:
                raise Validation("off_report root cannot be used directly; pick a child")
            leg.tag_id, leg.tag_source, leg.tag_confidence = tag["id"], "caller", "exact"
        if leg.account_id is None and leg.tag_id is None:
            raise Validation("each posting needs an account or a tag")
        legs.append(leg)
    totals: dict[str, Decimal] = {}
    for leg in legs:
        totals[leg.currency] = totals.get(leg.currency, Decimal(0)) + leg.amount
    bad = {c: str(v) for c, v in totals.items() if v != 0}
    if bad:
        raise Unbalanced("postings do not balance per currency", unbalanced=bad)
    return TxInput(
        occurred_at=parse_occurred_at(body["occurred_at"], tz), legs=legs, description=body.get("description"),
        labels=list(body.get("labels") or []), idempotency_key=body.get("idempotency_key"), source=body.get("source", "api"),
    )


async def write_transaction(conn: Conn, user_id: uuid.UUID, tx: TxInput) -> dict[str, Any]:
    if tx.idempotency_key:
        existing = await conn.fetchval(
            "SELECT id FROM transactions WHERE user_id = $1 AND idempotency_key = $2", user_id, tx.idempotency_key
        )
        if existing:
            return await get_transaction(conn, user_id, existing)
    txid = await conn.fetchval(
        """INSERT INTO transactions(user_id, occurred_at, description, source, labels, idempotency_key, metadata)
           VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
        user_id, tx.occurred_at, tx.description, tx.source, tx.labels, tx.idempotency_key, tx.metadata,
    )
    for leg in tx.legs:
        await conn.execute(
            """INSERT INTO postings(user_id, transaction_id, account_id, tag_id, amount, currency, quantity, unit,
                                    instrument_id, tag_source, tag_confidence, occurred_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
            user_id, txid, leg.account_id, leg.tag_id, leg.amount, leg.currency, leg.quantity, leg.unit,
            leg.instrument_id, leg.tag_source, leg.tag_confidence, tx.occurred_at,
        )
        await _rollup_increment(conn, user_id, tx.occurred_at, leg)
    await _detect_pair(conn, user_id, txid, tx)
    return await get_transaction(conn, user_id, txid)


async def _rollup_increment(conn: Conn, user_id: uuid.UUID, occurred_at: datetime, leg: Leg, sign: int = 1) -> None:
    d = occurred_at.astimezone(timezone.utc).date()
    amt = leg.amount * sign
    for kind, start in (("day", d), ("month", d.replace(day=1))):
        await conn.execute(
            """INSERT INTO period_rollups(user_id, period_kind, period_start, account_id, tag_id, currency, debit, credit, net, posting_count)
               VALUES ($1,$2,$3,$4,$5,$6,
                       CASE WHEN $7::numeric < 0 THEN -$7::numeric ELSE 0 END,
                       CASE WHEN $7::numeric > 0 THEN $7::numeric ELSE 0 END, $7::numeric, $8::int)
               ON CONFLICT (user_id, period_kind, period_start, account_id, tag_id, currency) DO UPDATE SET
                 debit = period_rollups.debit + EXCLUDED.debit, credit = period_rollups.credit + EXCLUDED.credit,
                 net = period_rollups.net + EXCLUDED.net, posting_count = period_rollups.posting_count + EXCLUDED.posting_count""",
            user_id, kind, start, leg.account_id or ZERO, leg.tag_id or ZERO, leg.currency, amt, sign,
        )


async def _detect_pair(conn: Conn, user_id: uuid.UUID, txid: uuid.UUID, tx: TxInput) -> None:
    """Light self-transfer detection: an opposite-sign, equal-amount account leg on another own asset
    account within the window → tag both off_report.self_transfer and link with pair_id."""
    account_legs = [leg for leg in tx.legs if leg.account_id and not leg.tag_id]
    if len(account_legs) != 1:
        return
    leg = account_legs[0]
    window = get_behaviour().pair_detection_window_days
    row = await conn.fetchrow(
        """SELECT p.transaction_id, p.id AS posting_id, p.occurred_at
           FROM postings p JOIN transactions t ON t.id = p.transaction_id JOIN accounts a ON a.id = p.account_id
           WHERE p.user_id = $1 AND p.account_id IS NOT NULL AND p.account_id <> $2 AND p.currency = $3
             AND p.amount = -($4::numeric) AND t.pair_id IS NULL AND t.id <> $5 AND t.status = 'posted'
             AND p.occurred_at BETWEEN $6::timestamptz - make_interval(days => $7) AND $6::timestamptz + make_interval(days => $7)
             AND NOT EXISTS (SELECT 1 FROM postings q JOIN tags g ON g.id = q.tag_id JOIN tags r ON r.id = COALESCE(g.root_id, g.id)
                             WHERE q.transaction_id = t.id AND r.kind = 'off_report')
           ORDER BY abs(extract(epoch from (p.occurred_at - $6::timestamptz))) LIMIT 1""",
        user_id, leg.account_id, leg.currency, leg.amount, txid, tx.occurred_at, window,
    )
    if row is None:
        return
    other_liab = await conn.fetchval(
        "SELECT bool_or(a.is_liability) FROM postings p JOIN accounts a ON a.id = p.account_id WHERE p.transaction_id IN ($1,$2)",
        txid, row["transaction_id"],
    )
    key = "credit_card_payment" if other_liab else "self_transfer"
    tag_id = await conn.fetchval("SELECT id FROM tags WHERE system_key = $1", key)
    pair = uuid.uuid4()
    for t in (txid, row["transaction_id"]):
        await conn.execute("UPDATE transactions SET pair_id = $1 WHERE id = $2", pair, t)
        # re-tag the tag legs (untagged/caller) of both to the transfer purpose; rollups adjust via tag_id change
        await _retag_transaction(conn, user_id, t, tag_id, "rule", "high")


async def _retag_transaction(conn: Conn, user_id: uuid.UUID, txid: uuid.UUID, new_tag: uuid.UUID,
                             source: str = "caller", confidence: str = "exact") -> None:
    rows = await conn.fetch("SELECT id, occurred_at, amount, currency, account_id, tag_id FROM postings WHERE transaction_id = $1 AND account_id IS NULL", txid)
    for r in rows:
        if r["tag_id"] == new_tag:
            continue
        old = Leg(amount=r["amount"], currency=r["currency"], account_id=None, tag_id=r["tag_id"])
        await _rollup_increment(conn, user_id, r["occurred_at"], old, sign=-1)
        # re-tagging changes classification only; postings_immutable() permits tag-column updates
        await conn.execute(
            "UPDATE postings SET tag_id = $1, tag_source = $2, tag_confidence = $3 WHERE id = $4",
            new_tag, source, confidence, r["id"],
        )
        await _rollup_increment(conn, user_id, r["occurred_at"], Leg(amount=r["amount"], currency=r["currency"], tag_id=new_tag))


async def set_tag(conn: Conn, user_id: uuid.UUID, txid: uuid.UUID, tag_ref: str, locale: str) -> dict[str, Any]:
    tag = await tg.resolve_tag(conn, user_id, tag_ref, locale)
    if tag["effective_kind"] == "off_report" and tag["root_id"] is None:
        raise Validation("off_report root cannot be used directly; pick a child")
    await get_transaction(conn, user_id, txid)
    await _retag_transaction(conn, user_id, txid, tag["id"], "caller", "exact")
    return await get_transaction(conn, user_id, txid)


async def reverse_transaction(conn: Conn, user_id: uuid.UUID, txid: uuid.UUID, reason: str | None = None) -> dict[str, Any]:
    orig = await get_transaction(conn, user_id, txid)
    if orig["status"] == "void":
        raise Validation("transaction already reversed")
    new_id = await conn.fetchval(
        """INSERT INTO transactions(user_id, occurred_at, description, source, reverses_id, metadata)
           VALUES ($1, now(), $2, 'system', $3, $4) RETURNING id""",
        user_id, f"Reversal: {orig.get('description') or ''}".strip(), txid, {"reason": reason},
    )
    now = datetime.now(timezone.utc)
    for p in orig["postings"]:
        leg = Leg(amount=-Decimal(p["amount_raw"]), currency=p["currency"], account_id=p["account_id"], tag_id=p["tag_id"],
                  quantity=(-Decimal(p["quantity"]) if p.get("quantity") is not None else None), unit=p.get("unit"))
        await conn.execute(
            """INSERT INTO postings(user_id, transaction_id, account_id, tag_id, amount, currency, quantity, unit, tag_source, tag_confidence, occurred_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'caller','exact',$9)""",
            user_id, new_id, leg.account_id, leg.tag_id, leg.amount, leg.currency, leg.quantity, leg.unit, now,
        )
        await _rollup_increment(conn, user_id, now, leg)
    await conn.execute("UPDATE transactions SET status = 'void' WHERE id = $1", txid)
    return await get_transaction(conn, user_id, new_id)


async def get_transaction(conn: Conn, user_id: uuid.UUID, txid: uuid.UUID) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM transactions WHERE id = $1 AND user_id = $2", txid, user_id)
    if row is None:
        raise NotFound("transaction not found")
    tx = dict(row)
    posts = await conn.fetch(
        """SELECT p.id, p.account_id, a.name AS account_name, p.tag_id, t.system_key AS tag_key, t.name AS tag_name,
                  r.system_key AS root_key, COALESCE(t.kind, r.kind) AS tag_kind,
                  p.amount AS amount_raw, p.currency, p.quantity, p.unit, p.instrument_id, p.tag_source, p.tag_confidence
           FROM postings p LEFT JOIN accounts a ON a.id = p.account_id
           LEFT JOIN tags t ON t.id = p.tag_id LEFT JOIN tags r ON r.id = t.root_id
           WHERE p.transaction_id = $1 ORDER BY p.id""",
        txid,
    )
    tx["postings"] = [dict(p) for p in posts]
    return tx


async def list_transactions(conn: Conn, user_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None,
                            account_id: uuid.UUID | None = None, tag_id: uuid.UUID | None = None,
                            since: datetime | None = None, until: datetime | None = None, q: str | None = None,
                            untagged_only: bool = False) -> tuple[list[dict[str, Any]], str | None]:
    conds = ["t.user_id = $1", "t.status <> 'projected'"]
    args: list[Any] = [user_id]

    def add(cond: str, val: Any) -> None:
        args.append(val)
        conds.append(cond.replace("?", f"${len(args)}"))

    if cursor:
        ts, tid = cursor.split("|", 1)
        args.extend([datetime.fromisoformat(ts), uuid.UUID(tid)])
        conds.append(f"(t.occurred_at, t.id) < (${len(args) - 1}::timestamptz, ${len(args)}::uuid)")
    if account_id:
        add("EXISTS (SELECT 1 FROM postings p WHERE p.transaction_id = t.id AND p.account_id = ?)", account_id)
    if tag_id:
        args.append(tag_id)
        conds.append(f"EXISTS (SELECT 1 FROM postings p JOIN tags g ON g.id = p.tag_id WHERE p.transaction_id = t.id AND (g.id = ${len(args)} OR g.root_id = ${len(args)}))")
    if since:
        add("t.occurred_at >= ?", since)
    if until:
        add("t.occurred_at < ?", until)
    if q:
        args.append(q)
        conds.append(f"(t.description_norm % norm_text(${len(args)}) OR t.description_norm LIKE '%' || norm_text(${len(args)}) || '%')")
    if untagged_only:
        conds.append("EXISTS (SELECT 1 FROM postings p JOIN tags g ON g.id = p.tag_id WHERE p.transaction_id = t.id AND g.system_key = 'untagged')")
    args.append(limit + 1)
    rows = await conn.fetch(
        f"SELECT t.id FROM transactions t WHERE {' AND '.join(conds)} ORDER BY t.occurred_at DESC, t.id DESC LIMIT ${len(args)}", *args
    )
    ids = [r["id"] for r in rows]
    next_cursor = None
    if len(ids) > limit:
        ids = ids[:limit]
    out = [await get_transaction(conn, user_id, i) for i in ids]
    if len(rows) > limit and out:
        last = out[-1]
        next_cursor = f"{last['occurred_at'].isoformat()}|{last['id']}"
    return out, next_cursor
