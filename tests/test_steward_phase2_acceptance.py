"""Phase 2 acceptance criteria (Steward spec §11).

These are the four gates the spec names for Phase 2, exercised end-to-end through
the orchestrator (`Steward.check_with_context`) against a real (tmp) ledger:

1. A simulated carry-forward flip on a 529 produces exactly one high I-3 whose
   dollars are the balance that left net worth.
2. A registry-expected carry-forward (a frozen 401k feed) produces nothing.
3. Re-running yields the *same* finding id (stable upsert, no pile-up).
4. Dismiss-with-reason suppresses it on the next run.

Plus a run-shaping check: two unknown accounts still cap needs_input to one.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.monarch.models import Account
from financial_mcp.steward.detectors.integrity import IntegrityContext
from financial_mcp.steward.ledger import Ledger
from financial_mcp.steward.policy import Policy
from financial_mcp.steward.steward import Steward

AS_OF = datetime.date(2026, 9, 11)
LEXI_529 = "237313797006441728"
AGENTSYNC_401K = "183541300701576552"
HELOC_2800 = "251845388366920938"


class _Clock:
    def __init__(self, t: datetime.datetime):
        self.t = t

    def __call__(self) -> datetime.datetime:
        return self.t


def _utc(y, m, d):
    return datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)


def _acct(account_id, name, balance, *, cf=False, inw=True):
    return Account(
        name=name,
        latest_balance=Decimal(str(balance)),
        as_of=AS_OF,
        account_id=account_id,
        include_in_net_worth=inw,
        is_carry_forward=cf,
        snapshot_date=AS_OF,
    )


def _steward(tmp_path, clock):
    ledger = Ledger(str(tmp_path / "findings.db"), now=clock)
    # svc is unused when we drive check_with_context with a hand-built context
    return Steward(svc=None, ledger=ledger, policy_loader=Policy.from_seed)


def _ctx(accounts, previous_carry_forward):
    return IntegrityContext(
        as_of=AS_OF,
        policy=Policy.from_seed(),
        source="live",
        accounts=accounts,
        previous_carry_forward=previous_carry_forward,
    )


# --- criterion 1 + 2 + 3 -------------------------------------------------
def test_529_flip_yields_one_high_i3_with_exact_dollars_and_expected_is_silent(tmp_path):
    clock = _Clock(_utc(2026, 9, 11))
    steward = _steward(tmp_path, clock)

    lexi = _acct(LEXI_529, "Lexi 529 (02)", 40140.98, cf=True)  # flipped to carry-forward
    frozen = _acct(AGENTSYNC_401K, "AgentSync 401k", 90000, cf=True)  # registry-expected

    ctx = _ctx([lexi, frozen], previous_carry_forward={LEXI_529: False, AGENTSYNC_401K: True})
    out = steward.check_with_context(ctx)

    i3 = [f for f in out["items"] if f["detector"] == "I-3"]
    assert len(i3) == 1, out["items"]
    assert i3[0]["confidence"] == "high"
    assert i3[0]["dollars_at_stake"] == 40140.98
    assert i3[0]["evidence"]["figures"]["removed_from_net_worth"] == 40140.98
    # the frozen 401k produced nothing
    assert not any(f["detector"] == "I-3" and AGENTSYNC_401K in f["evidence"]["account_ids"]
                   for f in out["items"])
    # a high integrity finding degrades trust (spec §5.4)
    assert out["trust"] == "degraded"
    first_id = i3[0]["id"]

    # criterion 3: re-run yields the SAME finding id (stable upsert)
    clock.t = _utc(2026, 9, 12)
    ctx2 = _ctx([lexi, frozen], previous_carry_forward={LEXI_529: True, AGENTSYNC_401K: True})
    out2 = steward.check_with_context(ctx2)
    i3b = [f for f in out2["items"] if f["detector"] == "I-3"]
    assert len(i3b) == 1
    assert i3b[0]["id"] == first_id


def test_registry_expected_carry_forward_produces_nothing(tmp_path):
    clock = _Clock(_utc(2026, 9, 11))
    steward = _steward(tmp_path, clock)
    frozen = _acct(AGENTSYNC_401K, "AgentSync 401k", 90000, cf=True)
    out = steward.check_with_context(_ctx([frozen], previous_carry_forward={AGENTSYNC_401K: True}))
    assert out["items"] == []
    assert out["needs_input"] == []
    assert out["trust"] == "ok"


# --- criterion 4 ---------------------------------------------------------
def test_dismiss_with_reason_suppresses_on_next_run(tmp_path):
    clock = _Clock(_utc(2026, 9, 11))
    steward = _steward(tmp_path, clock)
    lexi = _acct(LEXI_529, "Lexi 529 (02)", 40140.98, cf=True)

    out = steward.check_with_context(_ctx([lexi], previous_carry_forward={LEXI_529: False}))
    fid = [f for f in out["items"] if f["detector"] == "I-3"][0]["id"]

    steward.ledger.update(fid, "dismissed", reason="feed re-linking; owner confirmed transient")

    clock.t = _utc(2026, 9, 12)
    out2 = steward.check_with_context(_ctx([lexi], previous_carry_forward={LEXI_529: True}))
    assert fid not in [f["id"] for f in out2["items"]]
    assert steward.ledger.get(fid)["status"] == "dismissed"


# --- run shaping: needs_input capped at one ------------------------------
def test_two_unknown_accounts_cap_needs_input_to_one(tmp_path):
    clock = _Clock(_utc(2026, 9, 11))
    steward = _steward(tmp_path, clock)
    a = _acct("900000000000000001", "Mystery Brokerage A", 1000)
    b = _acct("900000000000000002", "Mystery Brokerage B", 2000)
    out = steward.check_with_context(_ctx([a, b], previous_carry_forward={}))
    assert len(out["needs_input"]) == 1
    # the larger-dollar unknown leads
    assert out["needs_input"][0]["dollars_at_stake"] == 2000.0
