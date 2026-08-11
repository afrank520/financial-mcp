import datetime
from decimal import Decimal

from financial_mcp.analytics import holdings as holdings_analytics
from financial_mcp.monarch.models import Holding, RecurringItem


def test_holding_and_recurring_models_construct():
    h = Holding(
        account="Brokerage", ticker="VTI", name="Vanguard Total Stock Market ETF",
        quantity=Decimal("120.5"), price=Decimal("268.40"), value=Decimal("32341.20"),
        cost_basis=Decimal("25000.00"),
        day_change_dollars=Decimal("180.75"), day_change_percent=Decimal("0.56"),
    )
    assert h.cost_basis == Decimal("25000.00")
    # nullable fields default-safe
    h2 = Holding(
        account="IRA", ticker=None, name="Manual Gold",
        quantity=Decimal("1"), price=Decimal("0"), value=Decimal("1900.00"),
        cost_basis=None, day_change_dollars=None, day_change_percent=None,
    )
    assert h2.ticker is None and h2.cost_basis is None

    r = RecurringItem(
        date=datetime.date(2026, 6, 3), merchant="Netflix", amount=Decimal("-15.99"),
        frequency="monthly", account="Credit Card", category="Entertainment", is_past=True,
    )
    assert r.date == datetime.date(2026, 6, 3)
    assert r.frequency == "monthly"
    r2 = RecurringItem(
        date=datetime.date(2026, 6, 4), merchant="Unknown", amount=Decimal("0"),
        frequency=None, account=None, category=None, is_past=False,
    )
    assert r2.account is None


def _h(account, name, value, basis, ticker="X"):
    return Holding(
        account=account, ticker=ticker, name=name,
        quantity=Decimal("1"), price=Decimal("1"), value=Decimal(value),
        cost_basis=None if basis is None else Decimal(basis),
        day_change_dollars=None, day_change_percent=None,
    )


def test_holdings_summary_gain_loss_totals_and_sort():
    out = holdings_analytics.holdings_summary([
        _h("Brokerage", "Small", "1000", "800"),
        _h("Brokerage", "Big", "5000", "4000"),
        _h("IRA", "NoBasis", "300", None, ticker=None),
    ])
    # sorted by value desc
    assert [p["name"] for p in out["positions"]] == ["Big", "Small", "NoBasis"]
    # per-position gain_loss = value - cost_basis; null when no basis (key present)
    big = out["positions"][0]
    assert big["gain_loss"] == Decimal("1000")
    nob = out["positions"][2]
    assert nob["gain_loss"] is None
    assert "gain_loss" in nob
    # totals: value includes ALL; basis/gain_loss EXCLUDE the basis-less position
    assert out["totals"]["value"] == Decimal("6300")
    assert out["totals"]["cost_basis"] == Decimal("4800")
    assert out["totals"]["gain_loss"] == Decimal("1200")
    assert out["methodology"].startswith("Values, cost basis, and day-change are Monarch's own.")


def test_holdings_summary_empty_has_no_totals_block():
    out = holdings_analytics.holdings_summary([])
    assert out["positions"] == []
    assert out["totals"] == {}
    assert "methodology" in out
