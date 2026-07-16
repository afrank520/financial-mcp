from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from financial_mcp.monarch.models import Transaction


def _group_totals(txns: list[Transaction], key: str) -> list[dict]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for t in txns:
        k = getattr(t, key)
        totals[k] += t.amount
        counts[k] += 1
    rows = [{key: k, "total": totals[k], "count": counts[k]} for k in totals]
    rows.sort(key=lambda r: r["total"])
    return rows


def category_summary(txns: list[Transaction]) -> list[dict]:
    return _group_totals(txns, "category")


def merchant_summary(txns: list[Transaction]) -> list[dict]:
    return _group_totals(txns, "merchant")


def monthly_spending(txns: list[Transaction], year: int) -> list[dict]:
    totals: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in txns:
        if t.date.year == year and t.amount < 0:
            totals[t.date.month] += t.amount
    return [{"month": m, "total": totals[m]} for m in sorted(totals)]


def search(
    txns: list[Transaction],
    *,
    merchant: str | None = None,
    category: str | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
) -> list[Transaction]:
    result = txns
    if merchant is not None:
        m = merchant.lower()
        result = [t for t in result if m in t.merchant.lower()]
    if category is not None:
        c = category.lower()
        result = [t for t in result if c in t.category.lower()]
    if min_amount is not None:
        result = [t for t in result if t.amount >= min_amount]
    if max_amount is not None:
        result = [t for t in result if t.amount <= max_amount]
    return result
