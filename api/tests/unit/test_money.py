from decimal import Decimal

from finassis.money import (
    UnitInfo,
    UnitRegistry,
    convert_quantity,
    format_amount,
    from_minor,
    money_envelope,
    to_minor,
)

VND = UnitInfo("VND", "money", 0, "₫", None)
USD = UnitInfo("USD", "money", 2, "$", None)
BTC = UnitInfo("BTC", "money", 8, "₿", None)
G = UnitInfo("g", "mass", 3, "g", Decimal(1))
CHI = UnitInfo("chi", "mass", 3, "chỉ", Decimal("3.75"))
LUONG = UnitInfo("luong", "mass", 4, "lượng", Decimal("37.5"))


def test_minor_roundtrip():
    assert to_minor(Decimal("250000"), 0) == 250000
    assert to_minor(Decimal("1250.50"), 2) == 125050
    assert from_minor(125050, 2) == Decimal("1250.50")
    assert to_minor(Decimal("0.00000001"), 8) == 1
    assert from_minor(1, 8) == Decimal("0.00000001")


def test_format_vi_en():
    assert format_amount(Decimal("250000"), 0, "vi", "₫") == "250.000 ₫"
    assert format_amount(Decimal("250000"), 0, "en", "₫") == "₫250,000"
    assert format_amount(Decimal("-1250.5"), 2, "en", "$") == "-$1,250.50"
    assert format_amount(Decimal("-1250.5"), 2, "vi", "$") == "-1.250,50 $"


def test_envelope():
    e = money_envelope(Decimal("-45000"), VND, "vi")
    assert e == {"amount": -45000, "currency": "VND", "decimals": 0, "display": "-45.000 ₫"}
    e = money_envelope(Decimal("0.5"), BTC, "en")
    assert e["amount"] == 50_000_000 and e["decimals"] == 8


def test_registry_and_conversion():
    reg = UnitRegistry()
    reg.update([
        {"code": "g", "measure": "mass", "factor_to_base": "1", "decimals": 3, "symbol": "g"},
        {"code": "chi", "measure": "mass", "factor_to_base": "3.75", "decimals": 3, "symbol": "chỉ"},
        {"code": "luong", "measure": "mass", "factor_to_base": "37.5", "decimals": 4, "symbol": "lượng"},
        {"code": "VND", "measure": "money", "factor_to_base": None, "decimals": 0, "symbol": "₫"},
    ])
    assert reg.get("VND").is_money and not reg.get("chi").is_money
    assert reg.money_codes() == ["VND"]
    # 23 chỉ = 2.3 lượng, 3 chỉ = 11.25 g
    assert convert_quantity(Decimal(23), CHI, LUONG) == Decimal("2.3")
    assert convert_quantity(Decimal(3), CHI, G) == Decimal("11.25")
