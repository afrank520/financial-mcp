from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from financial_mcp.analytics import networth
from financial_mcp.config import load_config
from financial_mcp.credentials import make_credential_store
from financial_mcp.monarch.community_adapter import CommunityAdapter

# Golden net-worth acceptance dates (Increment 1 "Honest Data"). The dormant
# test in tests/test_networth_golden.py pins the totals; this script only
# captures the fixture and prints the computed totals so you can eyeball them
# against Monarch before trusting the pin.
GOLDEN_DATES = (
    datetime.date(2025, 9, 1),
    datetime.date(2026, 3, 1),
    datetime.date(2026, 9, 1),
)

# The fixture is REAL account balances — personal financial data. It is written
# to a gitignored *.local.json path and never committed. The test reads it from
# here and skips when absent.
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "networth_snapshots.local.json"


def _load_session():
    return make_credential_store(load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))).load_session()


def run() -> int:
    session = _load_session()
    if not session:
        print("❌ no saved session — run `financial-mcp-login` first")
        return 1

    adapter = CommunityAdapter(session)  # read-only: only get_balance_history is called
    snapshots = adapter.get_balance_history()
    if not snapshots:
        print("❌ no balance-history snapshots returned")
        return 1

    payload = [s.model_dump(mode="json") for s in snapshots]
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(payload, indent=2))
    print(f"✅ wrote {len(payload)} snapshots to {FIXTURE_PATH} (gitignored, not committed)")

    print("\nComputed net worth (eyeball these against Monarch):")
    for d in GOLDEN_DATES:
        nw = networth.net_worth_as_of(snapshots, d)
        cf = [e for e in nw["excluded"] if e.get("reason") == "carry_forward"]
        cf_mag = sum(abs(e["balance"]) for e in cf if e["balance"] is not None)
        print(
            f"  {d.isoformat()}: net_worth={nw['net_worth']} "
            f"included={nw['included_count']} excluded={nw['excluded_count']} "
            f"carry_forward={len(cf)} (magnitude={cf_mag})"
        )
        if cf:
            for e in cf:
                print(f"      - carry-forward excluded: {e['display_name']} = {e['balance']}")
    print("\nIf these match Monarch to the penny, the golden test will pass locally.")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
