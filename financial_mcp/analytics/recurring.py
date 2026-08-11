from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.monarch.models import RecurringItem

METHODOLOGY = (
    "Amounts and frequencies are Monarch's own recurring-stream data. "
    "total_expected sums the amounts of all recurring items in the window "
    "(Monarch's own per-instance amounts); it is a sum, not a normalized monthly estimate."
)


def recurring_summary(
    items: list[RecurringItem], start: datetime.date, end: datetime.date
) -> dict:
    rows = [
        {
            "date": it.date.isoformat(),
            "merchant": it.merchant,
            "amount": it.amount,
            "frequency": it.frequency,
            "account": it.account,
            "category": it.category,
            "is_past": it.is_past,
        }
        for it in items
    ]
    rows.sort(key=lambda r: r["date"])
    total = sum((it.amount for it in items), Decimal("0"))
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "items": rows,
        "total_expected": total,
        "methodology": METHODOLOGY,
    }
