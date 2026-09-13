"""Task 4 — integrity detectors I-1…I-8 (Steward Phase 2, spec §6.1).

Each detector is exercised against the live 2026-09-11 example that motivated it.
The headline is I-3: a live 529 flipping to carry-forward must fire high with the
exact balance that left net worth, while a registry-expected carry-forward (a
frozen 401k feed) must stay silent.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.monarch.models import Account, Transaction
from financial_mcp.steward.detectors import integrity as I
from financial_mcp.steward.policy import Policy

AS_OF = datetime.date(2026, 9, 11)
LEXI_529 = "237313797006441728"
AGENTSYNC_401K = "183541300701576552"


def _pol() -> Policy:
    return Policy.from_seed()


def _acct(account_id, name, balance, *, cf=False, inw=True, snap=AS_OF):
    return Account(
        name=name,
        latest_balance=Decimal(str(balance)),
        as_of=AS_OF,
        account_id=account_id,
        include_in_net_worth=inw,
        is_carry_forward=cf,
        snapshot_date=snap,
    )


def _ctx(**kw):
    kw.setdefault("as_of", AS_OF)
    kw.setdefault("policy", _pol())
    return I.IntegrityContext(**kw)


def _txn(tid, date=AS_OF, amount="-10.00"):
    return Transaction(
        id=tid, date=date, merchant="M", category="C", account="A",
        original_statement="s", notes="", amount=Decimal(amount), tags=[], owner="o",
    )


# --- I-1 -----------------------------------------------------------------
def test_i1_fires_when_not_live():
    out = I.detect_i1_not_live(_ctx(source="csv"))
    assert len(out) == 1 and out[0].detector == "I-1" and out[0].distortion is True


def test_i1_silent_when_live():
    assert I.detect_i1_not_live(_ctx(source="live")) == []


# --- I-2 -----------------------------------------------------------------
def test_i2_flags_stale_account_snapshot():
    stale = _acct("live1", "Checking", 500, snap=datetime.date(2026, 6, 15))  # months old
    out = I.detect_i2_stale_data(_ctx(accounts=[stale]))
    assert [f.detector for f in out] == ["I-2"]
    assert out[0].dollars_at_stake == 500.0


def test_i2_ignores_expected_carry_forward_account():
    frozen = _acct(AGENTSYNC_401K, "AgentSync 401k", 90000, cf=True, snap=datetime.date(2026, 1, 1))
    assert I.detect_i2_stale_data(_ctx(accounts=[frozen])) == []


def test_i2_flags_stale_transaction_feed():
    old = _txn("t1", date=datetime.date(2026, 6, 15))
    out = I.detect_i2_stale_data(_ctx(transactions=[old]))
    assert any(f.evidence.dates == ["2026-06-15"] for f in out)


# --- I-3 (headline) ------------------------------------------------------
def test_i3_fires_high_on_529_flip_with_exact_dollars_removed():
    lexi = _acct(LEXI_529, "Lexi 529 (02)", 40140.98, cf=True)
    out = I.detect_i3_carry_forward_transition(
        _ctx(accounts=[lexi], previous_carry_forward={LEXI_529: False})
    )
    assert len(out) == 1
    f = out[0]
    assert f.detector == "I-3" and f.confidence == "high" and f.distortion is True
    assert f.dollars_at_stake == 40140.98
    assert f.evidence.figures["removed_from_net_worth"] == 40140.98


def test_i3_silent_for_registry_expected_carry_forward():
    frozen = _acct(AGENTSYNC_401K, "AgentSync 401k", 90000, cf=True)
    out = I.detect_i3_carry_forward_transition(
        _ctx(accounts=[frozen], previous_carry_forward={AGENTSYNC_401K: False})
    )
    assert out == []


def test_i3_watch_529_fires_even_without_prior_state():
    # a 529 should never be carry-forward; alarm even on first run (no prior memory)
    lexi = _acct(LEXI_529, "Lexi 529 (02)", 40140.98, cf=True)
    out = I.detect_i3_carry_forward_transition(_ctx(accounts=[lexi], previous_carry_forward={}))
    assert len(out) == 1 and out[0].detector == "I-3"


def test_i3_fires_for_persistent_non_watch_carry_forward_without_transition():
    # a live, non-watch, non-expected account that is carry-forward with NO prior
    # transition (was already flagged last run) must STILL fire — the distortion is
    # durable, and a transition-only detector would let the ledger auto-resolve it.
    heloc = _acct("251845388366920938", "HELOC (…2800)", 12500.00, cf=True)
    out = I.detect_i3_carry_forward_transition(
        _ctx(accounts=[heloc], previous_carry_forward={"251845388366920938": True})
    )
    assert len(out) == 1 and out[0].detector == "I-3" and out[0].dollars_at_stake == 12500.00


def test_i3_silent_for_carry_forward_account_excluded_from_net_worth():
    # Live-prove 2026-09-13 regression: an account that is carry-forward AND
    # excluded from net worth (the legacy 529 range, excluded cards) distorts
    # nothing — $0 left net worth — so I-3 must NOT fire, even though it's
    # carry-forward, not registry-expected, and (here) not watched.
    excluded = _acct("169485859965254976", "Alexis 529 (02, old entity)",
                     33000.00, cf=True, inw=False)
    out = I.detect_i3_carry_forward_transition(
        _ctx(accounts=[excluded], previous_carry_forward={"169485859965254976": False})
    )
    assert out == []


def test_i3_stable_id_across_runs():
    lexi = _acct(LEXI_529, "Lexi 529 (02)", 40140.98, cf=True)
    a = I.detect_i3_carry_forward_transition(_ctx(accounts=[lexi], previous_carry_forward={LEXI_529: False}))[0]
    b = I.detect_i3_carry_forward_transition(_ctx(accounts=[lexi], previous_carry_forward={LEXI_529: False}))[0]
    assert a.id == b.id


# --- I-4 -----------------------------------------------------------------
def test_i4_fires_on_net_worth_mismatch_beyond_tolerance():
    out = I.detect_i4_net_worth_mismatch(
        _ctx(computed_net_worth=Decimal("2088841.08"), monarch_net_worth=Decimal("2089000.00"))
    )
    assert len(out) == 1 and out[0].detector == "I-4"


def test_i4_silent_within_dollar_tolerance():
    out = I.detect_i4_net_worth_mismatch(
        _ctx(computed_net_worth=Decimal("2088841.08"), monarch_net_worth=Decimal("2088841.50"))
    )
    assert out == []


# --- I-5 / I-6 -----------------------------------------------------------
def test_i5_fires_when_budgets_unavailable():
    assert I.detect_i5_budgets_missing(_ctx(budgets_available=False))[0].detector == "I-5"
    assert I.detect_i5_budgets_missing(_ctx(budgets_available=True)) == []


def test_i6_fires_on_refresh_error_or_epoch_zero():
    assert I.detect_i6_refresh_failure(_ctx(refresh_errors=["balances: 502"]))[0].detector == "I-6"
    assert I.detect_i6_refresh_failure(_ctx(epoch_zero_types=["holdings"]))[0].detector == "I-6"
    assert I.detect_i6_refresh_failure(_ctx()) == []


# --- I-7 -----------------------------------------------------------------
def test_i7_fires_on_duplicate_txn_ids():
    out = I.detect_i7_duplicate_txns(_ctx(transactions=[_txn("dup"), _txn("dup"), _txn("ok")]))
    assert len(out) == 1 and out[0].evidence.txn_ids == ["dup"]


def test_i7_silent_when_all_unique():
    assert I.detect_i7_duplicate_txns(_ctx(transactions=[_txn("a"), _txn("b")])) == []


# --- I-8 -----------------------------------------------------------------
def test_i8_flags_unknown_account_as_needs_input():
    unknown = _acct("999999999999999999", "Mystery Brokerage", 1234.56)
    out = I.detect_i8_new_account_entity(_ctx(accounts=[unknown]))
    assert len(out) == 1
    assert out[0].confidence == "needs_input" and out[0].status == "needs_input"
    assert out[0].question is not None


def test_i8_silent_for_known_accounts():
    known = _acct(LEXI_529, "Lexi 529 (02)", 40140.98)
    assert I.detect_i8_new_account_entity(_ctx(accounts=[known])) == []


# --- runner --------------------------------------------------------------
def test_run_integrity_concatenates_and_covers_ids():
    lexi = _acct(LEXI_529, "Lexi 529 (02)", 40140.98, cf=True)
    out = I.run_integrity(_ctx(source="csv", accounts=[lexi], previous_carry_forward={LEXI_529: False}))
    detectors = {f.detector for f in out}
    assert "I-1" in detectors  # not live
    assert "I-3" in detectors  # 529 flip
    assert I.INTEGRITY_DETECTOR_IDS == ["I-1", "I-2", "I-3", "I-4", "I-5", "I-6", "I-7", "I-8"]
