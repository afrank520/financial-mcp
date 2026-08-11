from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.monarch.models import BudgetItem, Category


def budget_status(
    items: list[BudgetItem],
    categories: list[Category],
    start: datetime.date,
    end: datetime.date,
) -> dict:
    by_id = {c.id: c for c in categories}
    rows: list[dict] = []
    for it in items:
        c = by_id.get(it.category_id)
        rows.append({
            "category": c.name if c else it.category_id,
            "group": c.group if c else None,
            "budgeted": it.budgeted,
            "actual": it.actual,
            "remaining": it.remaining,
            "over_budget": it.remaining < 0,
        })
    rows.sort(key=lambda r: r["remaining"])  # most-negative remaining (worst) first

    total_budgeted = sum((it.budgeted for it in items), Decimal("0"))
    total_actual = sum((it.actual for it in items), Decimal("0"))
    total_remaining = sum((it.remaining for it in items), Decimal("0"))
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "categories": rows,
        "totals": {
            "budgeted": total_budgeted,
            "actual": total_actual,
            "remaining": total_remaining,
            "over_budget": total_remaining < 0,
        },
    }
