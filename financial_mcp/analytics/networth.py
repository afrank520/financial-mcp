from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.analytics.balances import balance_as_of
from financial_mcp.monarch.models import BalanceSnapshot, NetWorthSnapshot


def _point(s: NetWorthSnapshot) -> dict:
    return {"date": s.date.isoformat(), "net_worth": s.net_worth}


def net_worth_as_of(
    snapshots: list[BalanceSnapshot], target_date: datetime.date
) -> dict:
    """Net worth computed on-device from per-account balance snapshots.

        net_worth = Σ balance  for accounts where
                      include_in_net_worth == True AND is_carry_forward == False

    This reproduces Monarch's own net-worth number to the penny (verified $0.00
    residual on three independent dates). A carry-forward balance is a stale
    repeat of a dead account's last-known value; Monarch excludes it, so we must
    too — summing include_in_net_worth alone overstates net worth (by $436,557.87
    on 2026-09-01 across four carry-forward accounts). Returns the level plus a
    full breakdown: every account lands in `included` or `excluded` (with a reason:
    no_snapshot | not_in_net_worth | carry_forward), so the exclusion is auditable.
    """
    rows = balance_as_of(snapshots, target_date)
    included: list[dict] = []
    excluded: list[dict] = []
    total = Decimal("0")
    for r in rows:
        label = {
            "account_id": r["account_id"],
            "display_name": r["display_name"],
            "balance": r["balance"],
            "snapshot_date": r["snapshot_date"],
        }
        if r["balance"] is None:
            excluded.append({**label, "reason": "no_snapshot"})
        elif not r["include_in_net_worth"]:
            excluded.append({**label, "reason": "not_in_net_worth"})
        elif r["is_carry_forward"]:
            excluded.append({**label, "reason": "carry_forward"})
        else:
            total += r["balance"]
            included.append(label)
    return {
        "as_of": target_date.isoformat(),
        "net_worth": total,
        "included": included,
        "excluded": excluded,
        "included_count": len(included),
        "excluded_count": len(excluded),
    }


def networth_trend(
    snapshots: list[NetWorthSnapshot], start: datetime.date, end: datetime.date
) -> dict:
    window = {"start": start.isoformat(), "end": end.isoformat()}
    in_window = sorted(
        (s for s in snapshots if start <= s.date <= end), key=lambda s: s.date
    )
    if not in_window:
        return {
            "window": window,
            "start_point": None,
            "end_point": None,
            "change": None,
            "points": [],
            "reason": "no net-worth snapshots in window",
        }
    sp, ep = in_window[0], in_window[-1]
    return {
        "window": window,
        "start_point": _point(sp),
        "end_point": _point(ep),
        "change": {"net_worth": ep.net_worth - sp.net_worth},
        "points": [_point(s) for s in in_window],
    }
