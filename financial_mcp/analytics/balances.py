from __future__ import annotations

import datetime
from collections import defaultdict

from financial_mcp.monarch.models import BalanceSnapshot


def _as_of_one(points: list[BalanceSnapshot], target: datetime.date) -> BalanceSnapshot | None:
    """Last snapshot on-or-before target (points assumed same account)."""
    eligible = [p for p in points if p.date <= target]
    if not eligible:
        return None
    return max(eligible, key=lambda p: p.date)


def balance_as_of(snapshots: list[BalanceSnapshot], target_date: datetime.date) -> list[dict]:
    by_account: dict[str, list[BalanceSnapshot]] = defaultdict(list)
    for s in snapshots:
        by_account[s.account].append(s)
    rows: list[dict] = []
    for account in sorted(by_account):
        chosen = _as_of_one(by_account[account], target_date)
        if chosen is None:
            rows.append({
                "account": account,
                "balance": None,
                "as_of_requested": target_date.isoformat(),
                "snapshot_date": None,
                "is_carry_forward": False,
                "reason": "no snapshot on or before requested date",
            })
        else:
            rows.append({
                "account": account,
                "balance": chosen.balance,
                "as_of_requested": target_date.isoformat(),
                "snapshot_date": chosen.date.isoformat(),
                "is_carry_forward": chosen.date != target_date,
            })
    return rows
