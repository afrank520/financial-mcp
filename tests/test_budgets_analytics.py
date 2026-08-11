import datetime
from decimal import Decimal

from financial_mcp.analytics import budgets
from financial_mcp.monarch.models import BudgetItem, Category

D = datetime.date


def _cats():
    return [
        Category(id="c1", name="Groceries", group="Food & Dining", group_type="expense"),
        Category(id="c2", name="Rent", group="Housing", group_type="expense"),
    ]


def _items():
    return [
        BudgetItem(category_id="c1", budgeted=Decimal("500.00"),
                   actual=Decimal("-620.00"), remaining=Decimal("-120.00")),   # over
        BudgetItem(category_id="c2", budgeted=Decimal("2000.00"),
                   actual=Decimal("-2000.00"), remaining=Decimal("0.00")),     # on target
    ]


def test_rows_joined_named_typed_and_flagged():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    groc = next(r for r in out["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"
    assert groc["type"] == "expense"
    assert groc["over_budget"] is True
    rent = next(r for r in out["categories"] if r["category"] == "Rent")
    assert rent["over_budget"] is False


def test_worst_over_budget_sorted_first():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"][0]["category"] == "Groceries"   # remaining -120 < 0


def test_totals_split_by_type_expense_bucket():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    exp = out["totals"]["expense"]
    assert exp["budgeted"] == Decimal("2500.00")
    assert exp["actual"] == Decimal("-2620.00")
    assert exp["remaining"] == Decimal("-120.00")
    assert exp["over_budget"] is True
    # income/transfer/unknown buckets absent when no such rows
    assert set(out["totals"]) == {"expense"}


def test_methodology_present_and_exact():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["methodology"] == (
        "Totals are split by Monarch's own income/expense/transfer category type. "
        "For expense categories, over_budget means you spent more than planned. "
        "For income, favorable means you earned more than planned — not overspending. "
        "Transfers are internal money movement, shown for completeness."
    )


def test_unknown_category_id_falls_back_to_id_and_type():
    items = [BudgetItem(category_id="cX", budgeted=Decimal("50.00"),
                        actual=Decimal("0.00"), remaining=Decimal("50.00"))]
    out = budgets.budget_status(items, _cats(), D(2026, 6, 1), D(2026, 6, 30))
    row = out["categories"][0]
    assert row["category"] == "cX"      # id shown, not dropped
    assert row["group"] is None
    assert row["type"] == "unknown"
    assert row["over_budget"] is False  # literal; not claimed favorable
    assert out["totals"]["unknown"]["remaining"] == Decimal("50.00")


def test_empty_budget_no_totals_blocks():
    out = budgets.budget_status([], _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"] == []
    assert out["totals"] == {}
    assert "methodology" in out


def test_income_row_flagged_favorable_when_over_earned():
    cats = [Category(id="i1", name="Paychecks", group="Income", group_type="income")]
    # earned MORE than planned -> Monarch remaining < 0 -> favorable
    items = [BudgetItem(category_id="i1", budgeted=Decimal("45000.00"),
                        actual=Decimal("45836.00"), remaining=Decimal("-836.00"))]
    out = budgets.budget_status(items, cats, D(2026, 6, 1), D(2026, 6, 30))
    row = out["categories"][0]
    assert row["type"] == "income"
    assert "over_budget" not in row
    assert row["favorable"] is True
    inc = out["totals"]["income"]
    assert inc["favorable"] is True
    assert "over_budget" not in inc


def test_unrecognized_group_type_falls_into_unknown_bucket():
    cats = [Category(id="x1", name="Odd", group="Weird", group_type="loan")]
    items = [BudgetItem(category_id="x1", budgeted=Decimal("10.00"),
                        actual=Decimal("-30.00"), remaining=Decimal("-20.00"))]
    out = budgets.budget_status(items, cats, D(2026, 6, 1), D(2026, 6, 30))
    row = out["categories"][0]
    assert row["type"] == "unknown"          # unrecognized string, not income
    assert row["over_budget"] is True        # literal flag, never favorable
    assert "favorable" not in row
    assert out["totals"]["unknown"]["remaining"] == Decimal("-20.00")
