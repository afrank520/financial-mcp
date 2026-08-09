import datetime
from decimal import Decimal

from financial_mcp.analytics.balances import balance_as_of, balance_delta
from financial_mcp.monarch.models import BalanceSnapshot

D = datetime.date


def _snaps():
    return [
        BalanceSnapshot(account_id="heloc", display_name="HELOC", date=D(2026, 5, 31),
                        balance=Decimal("-224600.00"), type_name="loan", include_in_net_worth=True),
        BalanceSnapshot(account_id="heloc", display_name="HELOC", date=D(2026, 6, 28),
                        balance=Decimal("-202143.55"), type_name="loan", include_in_net_worth=True),
        BalanceSnapshot(account_id="broker", display_name="Brokerage", date=D(2026, 6, 15),
                        balance=Decimal("50000.00"), type_name="brokerage", include_in_net_worth=True),
    ]


def test_exact_hit_is_not_carry_forward():
    rows = balance_as_of(_snaps(), D(2026, 6, 28))
    heloc = next(r for r in rows if r["account_id"] == "heloc")
    assert heloc["display_name"] == "HELOC"
    assert heloc["type_name"] == "loan"
    assert heloc["include_in_net_worth"] is True
    assert heloc["balance"] == Decimal("-202143.55")
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is False
    assert heloc["as_of_requested"] == "2026-06-28"


def test_gap_carries_forward_last_known_with_provenance():
    rows = balance_as_of(_snaps(), D(2026, 6, 30))
    heloc = next(r for r in rows if r["account_id"] == "heloc")
    assert heloc["balance"] == Decimal("-202143.55")
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is True


def test_before_first_snapshot_is_null_with_reason():
    rows = balance_as_of(_snaps(), D(2026, 6, 1))
    bro = next(r for r in rows if r["account_id"] == "broker")
    assert bro["balance"] is None
    assert bro["snapshot_date"] is None
    assert "reason" in bro


def test_rows_cover_all_accounts_sorted_by_display_then_id():
    rows = balance_as_of(_snaps(), D(2026, 6, 30))
    assert [r["display_name"] for r in rows] == ["Brokerage", "HELOC"]


def test_duplicate_display_names_are_not_merged():
    # 529 succession: same display_name, different ids -> two distinct rows.
    snaps = [
        BalanceSnapshot(account_id="529-old", display_name="Braden 529 (01)", date=D(2026, 6, 1),
                        balance=Decimal("1000.00"), type_name="brokerage", include_in_net_worth=False),
        BalanceSnapshot(account_id="529-new", display_name="Braden 529 (01)", date=D(2026, 6, 1),
                        balance=Decimal("2000.00"), type_name="brokerage", include_in_net_worth=True),
    ]
    rows = balance_as_of(snaps, D(2026, 6, 1))
    ids = sorted(r["account_id"] for r in rows)
    assert ids == ["529-new", "529-old"]
    by_id = {r["account_id"]: r for r in rows}
    assert by_id["529-old"]["balance"] == Decimal("1000.00")
    assert by_id["529-new"]["balance"] == Decimal("2000.00")


def test_delta_heloc_paydown_is_positive():
    rows = balance_delta(_snaps(), D(2026, 5, 31), D(2026, 6, 28))
    heloc = next(r for r in rows if r["account_id"] == "heloc")
    assert heloc["delta"] == Decimal("22456.45")
    assert heloc["display_name"] == "HELOC"
    assert heloc["type_name"] == "loan"
    assert heloc["include_in_net_worth"] is True
    assert heloc["start"]["balance"] == Decimal("-224600.00")
    assert heloc["end"]["balance"] == Decimal("-202143.55")


def test_delta_null_when_endpoint_missing():
    rows = balance_delta(_snaps(), D(2026, 6, 1), D(2026, 6, 28))
    bro = next(r for r in rows if r["account_id"] == "broker")
    assert bro["delta"] is None
    assert "reason" in bro
