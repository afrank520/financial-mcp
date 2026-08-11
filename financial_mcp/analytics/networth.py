from __future__ import annotations

import datetime

from financial_mcp.monarch.models import NetWorthSnapshot


def _point(s: NetWorthSnapshot) -> dict:
    return {"date": s.date.isoformat(), "net_worth": s.net_worth}


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
