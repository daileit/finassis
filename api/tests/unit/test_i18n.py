from pathlib import Path

from finassis.i18n import Catalogue, render

ROOT = Path(__file__).resolve().parents[3]


def test_render_placeholders_and_plural():
    assert render("Hi {name}", {"name": "Dai"}) == "Hi Dai"
    msg = "{count, plural, =0 {Nothing untagged.} one {# untagged transaction} other {# untagged transactions}}"
    assert render(msg, {"count": 0}) == "Nothing untagged."
    assert render(msg, {"count": 1}) == "1 untagged transaction"
    assert render(msg, {"count": 7}) == "7 untagged transactions"
    sel = "{name}: {value} · {as_of}{stale, select, true { ⚠} other {}}"
    assert render(sel, {"name": "House", "value": "4,2 tỷ", "as_of": "03/2026", "stale": True}) == "House: 4,2 tỷ · 03/2026 ⚠"
    assert render(sel, {"name": "House", "value": "x", "as_of": "y", "stale": False}) == "House: x · y"


def test_catalogue_fallbacks():
    cat = Catalogue(ROOT / "i18n")
    assert "vi" in cat.locales and "en" in cat.locales
    assert cat.t("vi", "tag.coffee_drinks") == "Cà phê / trà sữa"
    assert cat.t("en", "tag.coffee_drinks") == "Coffee & drinks"
    # generated + hand-written merged
    assert cat.t("vi", "kind.off_report") == "Không tính vào báo cáo"
    # missing locale key → en; missing everywhere → humanised key
    assert cat.t("fr", "kind.income") == "Income"
    assert cat.t("en", "enum.made_up.some_value") == "Some value"
    assert "Dai" in cat.t("vi", "telegram.start.welcome", name="Dai")
