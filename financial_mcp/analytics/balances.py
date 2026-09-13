from __future__ import annotations

import datetime
from collections import defaultdict

from financial_mcp.monarch.models import Account, BalanceSnapshot


def latest_snapshot_date(snapshots: list[BalanceSnapshot]) -> datetime.date | None:
    """The most recent date any account reported a snapshot.

    Used as the carry-forward reference instead of literal "today": an account is
    carry-forward when its own latest snapshot predates the freshest data we hold.
    This is robust to a day-stale cache — literal "today" would wrongly mark every
    account carry-forward whenever the pull lags by a day, collapsing net worth."""
    return max((s.date for s in snapshots), default=None)


def merge_carry_forward(
    accounts: list[Account],
    snapshots: list[BalanceSnapshot],
    reference_date: datetime.date | None = None,
) -> list[Account]:
    """Enrich each Account's is_carry_forward / snapshot_date from balance snapshots.

    Full-coverage join: every input account is returned. An account with no
    snapshot series keeps its defaults (is_carry_forward=False, whatever
    snapshot_date the adapter set) rather than being dropped — a snapshot-only
    rebuild would silently lose brand-new / manual / zero-activity accounts.
    reference_date defaults to the latest snapshot date present."""
    ref = reference_date or latest_snapshot_date(snapshots)
    if ref is None:
        return list(accounts)
    as_of_rows = {r["account_id"]: r for r in balance_as_of(snapshots, ref)}
    out: list[Account] = []
    for a in accounts:
        row = as_of_rows.get(a.account_id)
        if row is not None and row["snapshot_date"] is not None:
            out.append(a.model_copy(update={
                "is_carry_forward": row["is_carry_forward"],
                "snapshot_date": datetime.date.fromisoformat(row["snapshot_date"]),
            }))
        else:
            out.append(a)
    return out


def _as_of_one(points: list[BalanceSnapshot], target: datetime.date) -> BalanceSnapshot | None:
    """Last snapshot on-or-before target (points assumed same account)."""
    eligible = [p for p in points if p.date <= target]
    if not eligible:
        return None
    return max(eligible, key=lambda p: p.date)


def balance_as_of(snapshots: list[BalanceSnapshot], target_date: datetime.date) -> list[dict]:
    by_id: dict[str, list[BalanceSnapshot]] = defaultdict(list)
    for s in snapshots:
        by_id[s.account_id].append(s)
    rows: list[dict] = []
    for account_id, points in by_id.items():
        latest = points[-1]  # any snapshot carries account-level label + classification
        base = {
            "account_id": account_id,
            "display_name": latest.display_name,
            "type_name": latest.type_name,
            "include_in_net_worth": latest.include_in_net_worth,
            "as_of_requested": target_date.isoformat(),
        }
        chosen = _as_of_one(points, target_date)
        if chosen is None:
            rows.append({
                **base,
                "balance": None,
                "snapshot_date": None,
                "is_carry_forward": False,
                "reason": "no snapshot on or before requested date",
            })
        else:
            rows.append({
                **base,
                "balance": chosen.balance,
                "snapshot_date": chosen.date.isoformat(),
                "is_carry_forward": chosen.date != target_date,
            })
    rows.sort(key=lambda r: (r["display_name"], r["account_id"]))
    return rows


def balance_delta(
    snapshots: list[BalanceSnapshot], start: datetime.date, end: datetime.date
) -> list[dict]:
    start_rows = {r["account_id"]: r for r in balance_as_of(snapshots, start)}
    end_rows = {r["account_id"]: r for r in balance_as_of(snapshots, end)}
    rows: list[dict] = []
    for account_id in set(start_rows) | set(end_rows):
        s = start_rows.get(account_id)
        e = end_rows.get(account_id)
        meta = s or e  # both carry the same account-level label + classification
        s_bal = None if s is None else s["balance"]
        e_bal = None if e is None else e["balance"]
        row = {
            "account_id": account_id,
            "display_name": meta["display_name"],
            "type_name": meta["type_name"],
            "include_in_net_worth": meta["include_in_net_worth"],
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
    rows.sort(key=lambda r: (r["display_name"], r["account_id"]))
    return rows
