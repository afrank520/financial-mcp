import datetime
from decimal import Decimal
from financial_mcp.monarch.models import BalanceSnapshot
from financial_mcp.analytics.balances import balance_as_of

D = datetime.date

def _snaps():
    return [
        BalanceSnapshot(account="HELOC", date=D(2026, 5, 31), balance=Decimal("-224600.00")),
        BalanceSnapshot(account="HELOC", date=D(2026, 6, 28), balance=Decimal("-202143.55")),
        BalanceSnapshot(account="Brokerage", date=D(2026, 6, 15), balance=Decimal("50000.00")),
    ]

def test_exact_hit_is_not_carry_forward():
    rows = balance_as_of(_snaps(), D(2026, 6, 28))
    heloc = next(r for r in rows if r["account"] == "HELOC")
    assert heloc["balance"] == Decimal("-202143.55")
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is False
    assert heloc["as_of_requested"] == "2026-06-28"

def test_gap_carries_forward_last_known_with_provenance():
    rows = balance_as_of(_snaps(), D(2026, 6, 30))  # weekend gap after 6/28
    heloc = next(r for r in rows if r["account"] == "HELOC")
    assert heloc["balance"] == Decimal("-202143.55")
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is True

def test_before_first_snapshot_is_null_with_reason():
    rows = balance_as_of(_snaps(), D(2026, 6, 1))  # before Brokerage's 6/15
    bro = next(r for r in rows if r["account"] == "Brokerage")
    assert bro["balance"] is None
    assert bro["snapshot_date"] is None
    assert "reason" in bro

def test_rows_cover_all_accounts_sorted():
    rows = balance_as_of(_snaps(), D(2026, 6, 30))
    assert [r["account"] for r in rows] == ["Brokerage", "HELOC"]
