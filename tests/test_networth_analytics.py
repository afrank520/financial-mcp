import datetime
from decimal import Decimal

from financial_mcp.analytics import networth
from financial_mcp.monarch.models import BalanceSnapshot, NetWorthSnapshot

D = datetime.date


def _snaps():
    return [
        NetWorthSnapshot(date=D(2026, 1, 31), net_worth=Decimal("1250000.00")),
        NetWorthSnapshot(date=D(2026, 3, 31), net_worth=Decimal("1262500.55")),
        NetWorthSnapshot(date=D(2026, 6, 30), net_worth=Decimal("1300000.00")),
    ]


def test_endpoints_and_change():
    out = networth.networth_trend(_snaps(), D(2026, 1, 1), D(2026, 6, 30))
    assert out["window"] == {"start": "2026-01-01", "end": "2026-06-30"}
    assert out["start_point"]["net_worth"] == Decimal("1250000.00")
    assert out["end_point"]["net_worth"] == Decimal("1300000.00")
    assert out["change"]["net_worth"] == Decimal("50000.00")
    assert len(out["points"]) == 3


def test_window_filters_points():
    out = networth.networth_trend(_snaps(), D(2026, 2, 1), D(2026, 4, 30))
    assert [p["date"] for p in out["points"]] == ["2026-03-31"]
    assert out["start_point"]["net_worth"] == out["end_point"]["net_worth"]  # single point
    assert out["change"]["net_worth"] == Decimal("0")


def test_empty_window_returns_reason_not_crash():
    out = networth.networth_trend(_snaps(), D(2025, 1, 1), D(2025, 12, 31))
    assert out["start_point"] is None
    assert out["end_point"] is None
    assert out["change"] is None
    assert out["points"] == []
    assert out["reason"] == "no net-worth snapshots in window"


# --- net_worth_as_of: on-device net worth with auditable breakdown ---

def _bal_snaps():
    # Freshest snapshot present is 2026-06-28. An account whose last snapshot
    # predates that is "carry-forward" (a dead/stale feed) and must be excluded.
    return [
        # Live checking — fresh on the reference date, counts.
        BalanceSnapshot(account_id="chk", display_name="Checking", date=D(2026, 6, 28),
                        balance=Decimal("10000.00"), type_name="depository",
                        include_in_net_worth=True),
        # HELOC — fresh, counts (negative reduces net worth).
        BalanceSnapshot(account_id="heloc", display_name="HELOC", date=D(2026, 6, 28),
                        balance=Decimal("-2000.00"), type_name="loan",
                        include_in_net_worth=True),
        # Dead 403b — INCLUDED in net worth per Monarch, but its feed died: last
        # snapshot is weeks stale, so on 2026-06-28 it is carry-forward. This is
        # the exact class of account that inflates a naive sum. Must be EXCLUDED.
        BalanceSnapshot(account_id="403b", display_name="403b ...665Y71", date=D(2026, 5, 1),
                        balance=Decimal("500000.00"), type_name="brokerage",
                        include_in_net_worth=True),
        # Old savings — user-excluded from net worth entirely.
        BalanceSnapshot(account_id="old", display_name="Old Savings", date=D(2026, 6, 28),
                        balance=Decimal("99999.00"), type_name="depository",
                        include_in_net_worth=False),
    ]


def test_net_worth_as_of_sums_only_fresh_in_networth_accounts():
    out = networth.net_worth_as_of(_bal_snaps(), D(2026, 6, 28))
    # 10000 - 2000 = 8000. The 403b (carry-forward) and Old Savings (not in net
    # worth) are BOTH excluded — a naive sum would add back $500,000.
    assert out["net_worth"] == Decimal("8000.00")
    assert out["as_of"] == "2026-06-28"
    assert out["included_count"] == 2
    included_ids = {r["account_id"] for r in out["included"]}
    assert included_ids == {"chk", "heloc"}


def test_net_worth_as_of_labels_every_exclusion_with_a_reason():
    out = networth.net_worth_as_of(_bal_snaps(), D(2026, 6, 28))
    reasons = {r["account_id"]: r["reason"] for r in out["excluded"]}
    assert reasons["403b"] == "carry_forward"      # in net worth, but stale feed
    assert reasons["old"] == "not_in_net_worth"    # user-excluded
    assert out["excluded_count"] == 2


def test_net_worth_as_of_flags_account_with_no_snapshot_yet():
    # A date before any snapshot: every account is "no_snapshot", none counted.
    out = networth.net_worth_as_of(_bal_snaps(), D(2026, 1, 1))
    assert out["net_worth"] == Decimal("0")
    assert out["included_count"] == 0
    assert all(r["reason"] == "no_snapshot" for r in out["excluded"])
