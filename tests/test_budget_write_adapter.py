from decimal import Decimal

import pytest

from financial_mcp.monarch.community_adapter import CommunityAdapter


def _capture_adapter(response):
    """Adapter whose fake transport records the last (op, variables) and returns `response`."""
    calls = []

    async def fake_transport(op, query, variables):
        calls.append((op, variables))
        return response

    ad = CommunityAdapter({"session_id": "s", "csrftoken": "c"}, transport=fake_transport)
    return ad, calls


def test_update_budget_item_sends_captured_shape_and_returns_saved():
    ad, calls = _capture_adapter(
        {"updateOrCreateBudgetItem": {"budgetItem": {"id": "b1", "plannedCashFlowAmount": 250.0}}}
    )
    saved = ad.update_budget_item("c1", Decimal("250.00"), "2026-09-01", False)
    assert saved == Decimal("250.0")
    op, variables = calls[0]
    assert op == "Common_UpdateBudgetItem"
    assert variables["input"] == {
        "categoryId": "c1",
        "startDate": "2026-09-01",
        "timeframe": "month",
        "amount": 250.0,
        "applyToFuture": False,
    }


def test_update_budget_item_all_future_flag_passes_through():
    ad, calls = _capture_adapter(
        {"updateOrCreateBudgetItem": {"budgetItem": {"id": "b1", "plannedCashFlowAmount": 300.0}}}
    )
    ad.update_budget_item("c1", Decimal("300.00"), "2026-09-01", True)
    assert calls[0][1]["input"]["applyToFuture"] is True


def test_update_budget_item_returns_none_when_no_item():
    ad, _ = _capture_adapter({"updateOrCreateBudgetItem": {"budgetItem": None}})
    assert ad.update_budget_item("c1", Decimal("250.00"), "2026-09-01", False) is None


def test_csv_update_budget_item_raises():
    from financial_mcp.monarch.csv_adapter import CsvAdapter

    ad = CsvAdapter(transactions_path=None, balances_path=None)
    with pytest.raises(RuntimeError):
        ad.update_budget_item("c1", Decimal("250.00"), "2026-09-01", False)
