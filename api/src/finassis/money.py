"""Money envelope and unit-aware formatting (ADR-017, ADR-021).

Storage is numeric(24,8) in the native currency. On the wire every monetary value is
  {"amount": <int minor units>, "currency": "VND", "decimals": 0, "display": "250.000"}
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

ZERO_UUID = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class UnitInfo:
    code: str
    measure: str
    decimals: int
    symbol: str | None
    factor_to_base: Decimal | None
    name: str | None = None

    @property
    def is_money(self) -> bool:
        return self.measure == "money"


class UnitRegistry:
    """In-memory cache of units (global + a user's own), refreshed from the DB."""

    def __init__(self, units: dict[str, UnitInfo] | None = None) -> None:
        self._units: dict[str, UnitInfo] = dict(units or {})

    def update(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            self._units[r["code"]] = UnitInfo(
                code=r["code"],
                measure=r["measure"],
                decimals=int(r["decimals"]),
                symbol=r.get("symbol"),
                factor_to_base=Decimal(str(r["factor_to_base"])) if r.get("factor_to_base") is not None else None,
                name=r.get("name"),
            )

    def get(self, code: str) -> UnitInfo:
        try:
            return self._units[code]
        except KeyError as e:
            raise KeyError(f"unknown unit {code}") from e

    def has(self, code: str) -> bool:
        return code in self._units

    def money_codes(self) -> list[str]:
        return sorted(c for c, u in self._units.items() if u.is_money)


def to_minor(amount: Decimal, decimals: int) -> int:
    q = Decimal(1).scaleb(-decimals)
    return int((amount.quantize(q, rounding=ROUND_HALF_EVEN) * (Decimal(10) ** decimals)).to_integral_value())


def from_minor(minor: int, decimals: int) -> Decimal:
    return (Decimal(minor) / (Decimal(10) ** decimals)).quantize(Decimal(1).scaleb(-decimals))


def format_amount(amount: Decimal, decimals: int, locale: str = "en", symbol: str | None = None) -> str:
    """Locale-aware grouping: vi uses '.' thousands and ',' decimal; en the reverse. Symbol optional."""
    q = amount.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)
    sign = "-" if q < 0 else ""
    q = abs(q)
    int_part, _, frac = f"{q:f}".partition(".")
    groups = []
    while int_part:
        groups.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    if locale.startswith("vi"):
        body = ".".join(groups) + (("," + frac) if decimals > 0 else "")
        return f"{sign}{body}" + (f" {symbol}" if symbol else "")
    body = ",".join(groups) + (("." + frac) if decimals > 0 else "")
    return f"{sign}{symbol or ''}{body}"


def money_envelope(amount: Decimal, unit: UnitInfo, locale: str = "en") -> dict[str, Any]:
    return {
        "amount": to_minor(amount, unit.decimals),
        "currency": unit.code,
        "decimals": unit.decimals,
        "display": format_amount(amount, unit.decimals, locale, unit.symbol),
    }


def quantity_envelope(value: Decimal, unit: UnitInfo) -> dict[str, Any]:
    q = value.quantize(Decimal(1).scaleb(-unit.decimals), rounding=ROUND_HALF_EVEN)
    return {"value": f"{q:f}", "unit": unit.code, "decimals": unit.decimals, "name": unit.name}


def convert_quantity(value: Decimal, src: UnitInfo, dst: UnitInfo) -> Decimal:
    if src.measure != dst.measure or src.is_money or dst.is_money:
        raise ValueError("cannot convert across measures or money units without FX")
    assert src.factor_to_base is not None and dst.factor_to_base is not None
    return value * src.factor_to_base / dst.factor_to_base
