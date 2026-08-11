import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from financial_mcp.monarch.csv_adapter import CsvAdapter

FIX = Path(__file__).parent / "fixtures"


def _adapter():
    return CsvAdapter(str(FIX / "transactions_sample.csv"), str(FIX / "balances_sample.csv"))


def test_source_name():
    assert _adapter().source_name() == "csv"


def test_transactions_parsed():
    txns = _adapter().get_transactions()
    assert len(txns) == 4
    first = txns[0]
    assert first.merchant == "Costco"
    assert first.amount == Decimal("-152.10")
    assert first.tags == ["food", "bulk"]
    assert first.date == datetime.date(2026, 1, 2)
    # empty tags -> []
    paycheck = next(t for t in txns if t.merchant == "Paycheck")
    assert paycheck.tags == []


def test_balances_parsed():
    bals = _adapter().get_balances()
    assert len(bals) == 3
    assert any(b.account == "Credit Card" and b.balance == Decimal("-450.00") for b in bals)


def test_accounts_use_latest_balance():
    accts = {a.name: a for a in _adapter().get_accounts()}
    assert accts["Checking"].latest_balance == Decimal("3200.00")
    assert accts["Checking"].as_of == datetime.date(2026, 1, 31)
    assert accts["Credit Card"].latest_balance == Decimal("-450.00")


def test_blank_amount_raises_error(tmp_path):
    import pytest

    csv_content = """Date,Merchant,Amount,Category,Account,Original Statement,Notes,Tags,Owner
2026-01-02,Costco,,Groceries,Checking,COSTCO #123,test note,food,Adam
"""
    txn_file = tmp_path / "transactions.csv"
    bal_file = tmp_path / "balances.csv"
    txn_file.write_text(csv_content)
    bal_file.write_text("Date,Balance,Account\n2026-01-02,100.00,Checking\n")

    adapter = CsvAdapter(str(txn_file), str(bal_file))
    with pytest.raises(ValueError, match=r"Empty Amount cell.*Date=2026-01-02.*Merchant=Costco"):
        adapter.get_transactions()


def test_csv_adapter_balance_history_is_empty():
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    # get_balance_history reads no files, so paths need not exist
    adapter = CsvAdapter(transactions_path="x", balances_path="y")
    assert adapter.get_balance_history() == []


def test_csv_adapter_get_cashflow_raises():
    import pytest
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    import datetime
    adapter = CsvAdapter(transactions_path="x", balances_path="y")
    with pytest.raises(RuntimeError, match="not available in CSV mode"):
        adapter.get_cashflow(datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))


def _csv_adapter():
    return CsvAdapter("unused.csv", "unused.csv")


def test_networth_history_not_available_in_csv():
    with pytest.raises(RuntimeError, match="not available in CSV mode"):
        _csv_adapter().get_networth_history()


def test_categories_not_available_in_csv():
    with pytest.raises(RuntimeError, match="not available in CSV mode"):
        _csv_adapter().get_categories()


def test_budgets_not_available_in_csv():
    import datetime as dt
    with pytest.raises(RuntimeError, match="not available in CSV mode"):
        _csv_adapter().get_budgets(dt.date(2026, 6, 1), dt.date(2026, 6, 30))
