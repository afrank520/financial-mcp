import datetime
from decimal import Decimal

from financial_mcp.analytics import networth
from financial_mcp.monarch.models import NetWorthSnapshot

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
