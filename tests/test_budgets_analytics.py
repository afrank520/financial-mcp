import datetime
from decimal import Decimal

from financial_mcp.analytics import budgets
from financial_mcp.monarch.models import BudgetItem, Category

D = datetime.date


def _cats():
    return [
        Category(id="c1", name="Groceries", group="Food & Dining"),
        Category(id="c2", name="Rent", group="Housing"),
    ]


def _items():
    return [
        BudgetItem(category_id="c1", budgeted=Decimal("500.00"),
                   actual=Decimal("-620.00"), remaining=Decimal("-120.00")),   # over
        BudgetItem(category_id="c2", budgeted=Decimal("2000.00"),
                   actual=Decimal("-2000.00"), remaining=Decimal("0.00")),     # on target
    ]


def test_rows_joined_named_and_flagged():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    groc = next(r for r in out["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"
    assert groc["over_budget"] is True
    rent = next(r for r in out["categories"] if r["category"] == "Rent")
    assert rent["over_budget"] is False


def test_worst_over_budget_sorted_first():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"][0]["category"] == "Groceries"   # remaining -120 < 0


def test_totals_rolled_up():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    t = out["totals"]
    assert t["budgeted"] == Decimal("2500.00")
    assert t["actual"] == Decimal("-2620.00")
    assert t["remaining"] == Decimal("-120.00")
    assert t["over_budget"] is True


def test_unknown_category_id_falls_back_to_id():
    items = [BudgetItem(category_id="cX", budgeted=Decimal("50.00"),
                        actual=Decimal("0.00"), remaining=Decimal("50.00"))]
    out = budgets.budget_status(items, _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"][0]["category"] == "cX"   # id shown, not dropped
    assert out["categories"][0]["group"] is None


def test_empty_budget_zeroed_totals_no_crash():
    out = budgets.budget_status([], _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"] == []
    assert out["totals"] == {"budgeted": Decimal("0"), "actual": Decimal("0"),
                             "remaining": Decimal("0"), "over_budget": False}
