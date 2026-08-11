from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.monarch.models import BudgetItem, Category

METHODOLOGY = (
    "Totals are split by Monarch's own income/expense/transfer category type. "
    "For expense categories, over_budget means you spent more than planned. "
    "For income, favorable means you earned more than planned — not overspending. "
    "Transfers are internal money movement, shown for completeness."
)

_KNOWN_TYPES = {"income", "expense", "transfer"}


def _row_flag(row_type: str, remaining: Decimal) -> dict:
    """Type-appropriate flag: income -> favorable, everything else -> over_budget."""
    neg = remaining < 0
    return {"favorable": neg} if row_type == "income" else {"over_budget": neg}


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
        gt = c.group_type if c else None
        row_type = gt if gt in _KNOWN_TYPES else "unknown"
        row = {
            "category": c.name if c else it.category_id,
            "group": c.group if c else None,
            "type": row_type,
            "budgeted": it.budgeted,
            "actual": it.actual,
            "remaining": it.remaining,
        }
        row.update(_row_flag(row_type, it.remaining))
        rows.append(row)
    rows.sort(key=lambda r: r["remaining"])  # most-negative remaining (worst) first

    # Split totals by type; a type with no rows produces no sub-block.
    totals: dict[str, dict] = {}
    by_type: dict[str, list[BudgetItem]] = {}
    for it in items:
        c = by_id.get(it.category_id)
        gt = c.group_type if c else None
        t = gt if gt in _KNOWN_TYPES else "unknown"
        by_type.setdefault(t, []).append(it)
    for t, its in by_type.items():
        tb = sum((i.budgeted for i in its), Decimal("0"))
        ta = sum((i.actual for i in its), Decimal("0"))
        tr = sum((i.remaining for i in its), Decimal("0"))
        block = {"budgeted": tb, "actual": ta, "remaining": tr}
        block.update(_row_flag(t, tr))
        totals[t] = block

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "categories": rows,
        "totals": totals,
        "methodology": METHODOLOGY,
    }
