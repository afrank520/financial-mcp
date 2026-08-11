from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from financial_mcp.monarch.models import Category, Transaction


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


def category_summary(txns: list[Transaction], categories: list[Category]) -> dict:
    """Net total and count per category, enriched with each category's Monarch
    group, plus group-level subtotals.

    Categories with no matching group (from the caller-supplied `categories`
    list) get `group: None` on their row and roll into the "Uncategorized"
    group bucket rather than being dropped.
    """
    name_to_group = {c.name: c.group for c in categories}
    base = _group_totals(txns, "category")  # [{category, total, count}], biggest net first

    cat_rows: list[dict] = []
    group_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    group_counts: dict[str, int] = defaultdict(int)
    for row in base:
        group = name_to_group.get(row["category"])
        cat_rows.append({**row, "group": group})
        bucket = group or "Uncategorized"
        group_totals[bucket] += row["total"]
        group_counts[bucket] += row["count"]

    groups = [
        {"group": g, "total": group_totals[g], "count": group_counts[g]}
        for g in group_totals
    ]
    groups.sort(key=lambda r: r["total"])  # biggest net spend first
    return {"categories": cat_rows, "groups": groups}


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


def date_range_summary(
    txns: list[Transaction],
    start: datetime.date,
    end: datetime.date,
    *,
    merchant: str | None = None,
    category: str | None = None,
) -> dict:
    """Server-computed spending summary over an inclusive [start, end] date window.

    Reuses search() for merchant/category filtering, then computes exact
    Decimal totals so callers never sum rows by hand.

    total is net spend (gross_charges - refunds), sign-flipped so ordinary
    spending is positive, matching Monarch's "Total spending" display. When
    refunds exceed charges in the window, total can be negative. Sign convention:
    negative amount = charge, positive = refund.

    count is the total number of matching transactions. Because transactions with
    amount exactly Decimal("0.00") are neither charges nor refunds, charge_count +
    refund_count may be less than count when zero-amount rows exist.
    """
    in_window = [t for t in txns if start <= t.date <= end]
    filtered = search(in_window, merchant=merchant, category=category)
    charges = [t for t in filtered if t.amount < 0]
    refunds = [t for t in filtered if t.amount > 0]
    gross_charges = -sum((t.amount for t in charges), Decimal("0"))
    refund_total = sum((t.amount for t in refunds), Decimal("0"))
    return {
        "total": gross_charges - refund_total,
        "gross_charges": gross_charges,
        "refunds": refund_total,
        "count": len(filtered),
        "charge_count": len(charges),
        "refund_count": len(refunds),
    }
