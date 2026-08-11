import datetime
from decimal import Decimal

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
