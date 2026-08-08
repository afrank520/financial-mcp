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


def balance_delta(
    snapshots: list[BalanceSnapshot], start: datetime.date, end: datetime.date
) -> list[dict]:
    start_rows = {r["account"]: r for r in balance_as_of(snapshots, start)}
    end_rows = {r["account"]: r for r in balance_as_of(snapshots, end)}
    rows: list[dict] = []
    for account in sorted(set(start_rows) | set(end_rows)):
        s = start_rows.get(account)
        e = end_rows.get(account)
        s_bal = None if s is None else s["balance"]
        e_bal = None if e is None else e["balance"]
        row = {
            "account": account,
            "start": None if s is None else {
                "balance": s["balance"],
                "snapshot_date": s["snapshot_date"],
                "is_carry_forward": s["is_carry_forward"],
            },
            "end": None if e is None else {
                "balance": e["balance"],
                "snapshot_date": e["snapshot_date"],
                "is_carry_forward": e["is_carry_forward"],
            },
        }
        if s_bal is None or e_bal is None:
            row["delta"] = None
            row["reason"] = "missing balance at one or both endpoints"
        else:
            row["delta"] = e_bal - s_bal
        rows.append(row)
    return rows
