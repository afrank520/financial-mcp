"""Golden net-worth regression (Increment 1 "Honest Data", Task 3).

Pins the on-device net worth to Monarch's own number on three independent
dates, and — critically — asserts the carry-forward exclusion set and its
magnitude, so a future regression that drops the is_carry_forward filter can't
slip through behind a still-correct-looking total.

The fixture is REAL account balances (personal financial data) and is NEVER
committed: capture it locally with `financial-mcp-capture-networth` (read-only),
which writes the gitignored tests/fixtures/networth_snapshots.local.json. When
that file is absent (any clone but the owner's Mac) these tests skip.
"""
from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path

import pytest

from financial_mcp.analytics import networth
from financial_mcp.monarch.models import BalanceSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "networth_snapshots.local.json"

# Pinned to Monarch's own net-worth number, verified $0.00 residual on capture.
GOLDEN = {
    datetime.date(2025, 9, 1): Decimal("1623956.88"),
    datetime.date(2026, 3, 1): Decimal("1718936.28"),
    datetime.date(2026, 9, 1): Decimal("2092237.35"),
}

# Accounts that are included_in_net_worth per Monarch but whose feed has died —
# on 2026-09-01 their last snapshot is stale, so they are carry-forward and MUST
# be excluded. Naive summing would add them back. The magnitude is the hard,
# spec-mandated pin; the name markers are distinctive substrings (not exact
# Monarch displayName strings, which the capture step confirms) so a formatting
# difference doesn't false-fail while a dropped filter still would.
CARRY_FORWARD_COUNT_2026_09_01 = 4
CARRY_FORWARD_NAME_MARKERS_2026_09_01 = ("AgentSync", "HELOC 8300", "403b", "Savings")
CARRY_FORWARD_MAGNITUDE_2026_09_01 = Decimal("436557.87")


def _load_snapshots() -> list[BalanceSnapshot]:
    if not FIXTURE.exists():
        pytest.skip(
            "golden fixture absent — run `financial-mcp-capture-networth` on the "
            "owner's Mac to capture it (read-only, gitignored)"
        )
    return [BalanceSnapshot(**d) for d in json.loads(FIXTURE.read_text())]


@pytest.mark.parametrize("target,expected", list(GOLDEN.items()))
def test_net_worth_matches_monarch_to_the_penny(target, expected):
    snaps = _load_snapshots()
    out = networth.net_worth_as_of(snaps, target)
    assert out["net_worth"] == expected


def test_carry_forward_accounts_are_excluded_on_2026_09_01():
    snaps = _load_snapshots()
    out = networth.net_worth_as_of(snaps, datetime.date(2026, 9, 1))
    cf_names = [e["display_name"] for e in out["excluded"] if e.get("reason") == "carry_forward"]
    assert len(cf_names) == CARRY_FORWARD_COUNT_2026_09_01
    # each known dead-feed account is caught (distinctive substring match)
    for marker in CARRY_FORWARD_NAME_MARKERS_2026_09_01:
        assert any(marker in n for n in cf_names), f"{marker} not found in carry-forward set {cf_names}"


def test_carry_forward_excluded_magnitude_on_2026_09_01():
    snaps = _load_snapshots()
    out = networth.net_worth_as_of(snaps, datetime.date(2026, 9, 1))
    cf_total = sum(
        abs(e["balance"])
        for e in out["excluded"]
        if e.get("reason") == "carry_forward" and e["balance"] is not None
    )
    # the exclusion removes the known ~$436,557.87 that a naive sum re-adds
    assert cf_total == CARRY_FORWARD_MAGNITUDE_2026_09_01
