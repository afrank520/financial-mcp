import datetime
from decimal import Decimal

from financial_mcp.analytics.spending import (
    category_summary,
    merchant_summary,
    monthly_spending,
    search,
)
from financial_mcp.monarch.models import Transaction


def _txn(merchant, category, amount, month=1, day=1, year=2026):
    d = datetime.date(year, month, day)
    return Transaction(
        id=Transaction.synth_id(d, merchant, Decimal(amount), merchant),
        date=d, merchant=merchant, category=category, account="Checking",
        original_statement=merchant, notes="", amount=Decimal(amount), tags=[], owner="Adam",
    )


TXNS = [
    _txn("Costco", "Groceries", "-152.10", month=1),
    _txn("Costco", "Groceries", "-88.40", month=2),
    _txn("Netflix", "Entertainment", "-15.99", month=1),
    _txn("Paycheck", "Income", "2500.00", month=1),
]


def test_category_summary_sorted_biggest_spend_first():
    rows = category_summary(TXNS)
    assert rows[0]["category"] == "Groceries"
    assert rows[0]["total"] == Decimal("-240.50")
    assert rows[0]["count"] == 2
    assert rows[-1]["category"] == "Income"


def test_merchant_summary():
    rows = {r["merchant"]: r for r in merchant_summary(TXNS)}
    assert rows["Costco"]["total"] == Decimal("-240.50")
    assert rows["Netflix"]["count"] == 1


def test_monthly_spending_negative_only():
    rows = {r["month"]: r["total"] for r in monthly_spending(TXNS, 2026)}
    assert rows[1] == Decimal("-168.09")  # -152.10 + -15.99 (paycheck excluded)
    assert rows[2] == Decimal("-88.40")
    assert set(rows.keys()) == {1, 2}


def test_search_filters():
    assert len(search(TXNS, merchant="cost")) == 2
    assert len(search(TXNS, category="income")) == 1
    assert len(search(TXNS, min_amount=Decimal("0"))) == 1  # only paycheck >= 0
