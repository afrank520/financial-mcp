import datetime
from decimal import Decimal
from pathlib import Path

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
