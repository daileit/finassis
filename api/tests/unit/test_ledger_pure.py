from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from finassis.api.schemas import TransactionExplicit, TransactionSimple
from finassis.domain.keys import Principal, generate, hash_key
from finassis.domain.ledger import parse_occurred_at


def test_parse_occurred_at_date_only_is_local_midnight():
    dt = parse_occurred_at("2026-10-01", "Asia/Ho_Chi_Minh")
    assert dt == datetime(2026, 10, 1, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert dt.astimezone(timezone.utc).hour == 17  # previous day 17:00 UTC


def test_parse_occurred_at_variants():
    assert parse_occurred_at(date(2026, 1, 2), "UTC").isoformat() == "2026-01-02T00:00:00+00:00"
    assert parse_occurred_at("2026-01-02T09:12:00Z", "Asia/Ho_Chi_Minh").tzinfo is not None
    naive = parse_occurred_at("2026-01-02T09:12:00", "Asia/Ho_Chi_Minh")
    assert naive.utcoffset().total_seconds() == 7 * 3600


def test_simple_form_validation():
    TransactionSimple(account="tcb", amount=-45000, occurred_at="2026-10-01", tag="coffee_drinks")
    with pytest.raises(ValueError):
        TransactionSimple(account="tcb", amount=-1, occurred_at="2026-10-01", tag="x", counter_account="cash")
    TransactionExplicit(occurred_at="2026-10-01", postings=[{"account": "a", "amount": -5, "currency": "VND"}, {"tag": "food", "amount": 5, "currency": "VND"}])
    with pytest.raises(ValueError):
        TransactionExplicit(occurred_at="2026-10-01", postings=[{"account": "a", "amount": -5, "currency": "VND"}])


def test_keys_and_scopes():
    k = generate("user")
    assert k.startswith("fk_") and len(k) > 30
    assert hash_key(k) != hash_key(generate("user"))
    p = Principal(key_id=None, kind="user", user_id=None, scopes=["ledger:read"])  # type: ignore[arg-type]
    assert p.has("ledger:read") and not p.has("ledger:write")
    admin = Principal(key_id=None, kind="admin", user_id=None, scopes=["*"])  # type: ignore[arg-type]
    assert admin.has("anything")
