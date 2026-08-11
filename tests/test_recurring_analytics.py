import datetime
from decimal import Decimal

from financial_mcp.analytics import recurring as recurring_analytics
from financial_mcp.monarch.models import RecurringItem


def _r(day, merchant, amount):
    return RecurringItem(
        date=datetime.date(2026, 6, day), merchant=merchant, amount=Decimal(amount),
        frequency="monthly", account="Card", category="Subs", is_past=day < 15,
    )


def test_recurring_summary_sorts_by_date_and_sums():
    out = recurring_analytics.recurring_summary(
        [_r(20, "Spotify", "-9.99"), _r(3, "Netflix", "-15.99")],
        datetime.date(2026, 6, 1), datetime.date(2026, 6, 30),
    )
    assert [i["merchant"] for i in out["items"]] == ["Netflix", "Spotify"]  # date asc
    assert out["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    assert out["total_expected"] == Decimal("-25.98")
    assert out["items"][0]["is_past"] is True
    assert out["methodology"].startswith("Amounts and frequencies are Monarch's own")


def test_recurring_summary_empty_window():
    out = recurring_analytics.recurring_summary(
        [], datetime.date(2026, 6, 1), datetime.date(2026, 6, 30)
    )
    assert out["items"] == []
    assert out["total_expected"] == Decimal("0")
    assert "methodology" in out
