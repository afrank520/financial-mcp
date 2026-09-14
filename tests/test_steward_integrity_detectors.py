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


def _acct(
    account_id,
    name,
    balance,
    *,
    cf=False,
    inw=True,
    snap=AS_OF,
    has_credential=False,
    sync_disabled=False,
    deactivated=False,
    disconnected=False,
    update_required=False,
    last_updated=None,
):
    return Account(
        name=name,
        latest_balance=Decimal(str(balance)),
        as_of=AS_OF,
        account_id=account_id,
        include_in_net_worth=inw,
        is_carry_forward=cf,
        snapshot_date=snap,
        has_credential=has_credential,
        sync_disabled=sync_disabled,
        is_deactivated=deactivated,
        credential_disconnected=disconnected,
        credential_update_required=update_required,
        last_updated=last_updated,
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


OLD = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)      # >7d before AS_OF
RECENT = datetime.datetime(2026, 9, 11, tzinfo=datetime.timezone.utc)  # AS_OF


def test_i6_epoch_zero_fires_as_refresh_level_finding():
    out = I.detect_i6_refresh_failure(_ctx(epoch_zero_types=["holdings"]))
    assert len(out) == 1
    assert out[0].detector == "I-6" and out[0].evidence.figures["epoch_zero_types"] == ["holdings"]


def test_i6_silent_when_everything_healthy():
    healthy = _acct("live1", "Checking", 500, has_credential=True, last_updated=RECENT)
    assert I.detect_i6_refresh_failure(_ctx(accounts=[healthy])) == []


def test_i6_fires_on_disconnected_credential():
    a = _acct("live1", "Frozen 401k", 90000, has_credential=True,
              disconnected=True, last_updated=RECENT)
    out = I.detect_i6_refresh_failure(_ctx(accounts=[a]))
    assert len(out) == 1
    f = out[0]
    assert f.detector == "I-6" and f.confidence == "high" and f.distortion is True
    assert f.dollars_at_stake == 90000.0
    assert f.evidence.account_ids == ["live1"]
    assert "disconnected" in f.evidence.figures["reasons"]


def test_i6_fires_on_sync_disabled_and_deactivated():
    disabled = _acct("a1", "Disabled", 100, has_credential=True,
                     sync_disabled=True, last_updated=RECENT)
    deact = _acct("a2", "Deactivated", 200, has_credential=True,
                  deactivated=True, last_updated=RECENT)
    out = I.detect_i6_refresh_failure(_ctx(accounts=[disabled, deact]))
    assert len(out) == 2
    assert {f.evidence.account_ids[0] for f in out} == {"a1", "a2"}
    reasons = {r for f in out for r in f.evidence.figures["reasons"]}
    assert "sync_disabled" in reasons and "deactivated" in reasons


def test_i6_fires_on_stale_beyond_threshold_for_live_credential():
    stale = _acct("live1", "Stale live feed", 4200, has_credential=True, last_updated=OLD)
    out = I.detect_i6_refresh_failure(_ctx(accounts=[stale]))
    assert len(out) == 1 and out[0].detector == "I-6"
    assert "stale" in out[0].evidence.figures["reasons"]
    assert out[0].dollars_at_stake == 4200.0


def test_i6_exempts_manual_valuation_account_with_no_credential():
    # Branwood-shaped: a valuation/manual account has no credential and legitimately
    # never syncs — an old last_updated must NOT flag it (the false-positive we must avoid).
    branwood = _acct("169484792332435402", "15 Branwood Drive", 2246000,
                     has_credential=False, last_updated=OLD)
    assert I.detect_i6_refresh_failure(_ctx(accounts=[branwood])) == []


def test_i6_one_finding_per_account_with_combined_reasons():
    # An account with multiple problems collapses to ONE finding (stable id per
    # account) whose reasons list carries every applicable signal.
    a = _acct("live1", "Really broken", 5000, has_credential=True,
              disconnected=True, sync_disabled=True, last_updated=OLD)
    out = I.detect_i6_refresh_failure(_ctx(accounts=[a]))
    assert len(out) == 1
    assert set(out[0].evidence.figures["reasons"]) >= {"disconnected", "sync_disabled", "stale"}


def test_i6_account_finding_has_stable_id_across_runs():
    a = _acct("live1", "Frozen", 100, has_credential=True, disconnected=True, last_updated=RECENT)
    x = I.detect_i6_refresh_failure(_ctx(accounts=[a]))[0]
    y = I.detect_i6_refresh_failure(_ctx(accounts=[a]))[0]
    assert x.id == y.id


def test_i6_silent_when_no_accounts_and_no_epoch_zero():
    assert I.detect_i6_refresh_failure(_ctx()) == []


# --- I-6 registry guard (live-prove 2026-09-14: 72% noise without it) --------
def test_i6_skips_registry_expected_carry_forward_account():
    # AgentSync 401k is registry expected_carry_forward — a known frozen feed.
    # Even with a live credential + old timestamp, I-6 must NOT fire (it's expected).
    frozen = _acct(AGENTSYNC_401K, "AgentSync 401k", 581399.26, has_credential=True,
                   disconnected=True, last_updated=OLD)
    assert I.detect_i6_refresh_failure(_ctx(accounts=[frozen])) == []


def test_i6_skips_account_excluded_from_net_worth():
    # A legacy/excluded account (excluded_legacy) distorts no net-worth figure,
    # so a broken feed on it is noise — mirror I-3's include_in_net_worth guard.
    excluded = _acct("169485859965254976", "Alexis 529 (02, old entity)", 33000.0,
                     inw=False, has_credential=True, disconnected=True, last_updated=OLD)
    assert I.detect_i6_refresh_failure(_ctx(accounts=[excluded])) == []


def test_i6_still_fires_for_live_watched_529_update_required():
    # The 5 live 529s (state=watch_carry_forward, IN net worth, NOT expected-CF)
    # are the real catch from the live-prove — update_required while still counted.
    # The registry guard must NOT silence them.
    live_529 = _acct(LEXI_529, "Lexi 529 (02)", 47139.88, inw=True,
                     has_credential=True, update_required=True, last_updated=RECENT)
    out = I.detect_i6_refresh_failure(_ctx(accounts=[live_529]))
    assert len(out) == 1 and out[0].detector == "I-6"
    assert "update_required" in out[0].evidence.figures["reasons"]


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


# The 20 real accounts backfilled into the registry from the live 2026-09-13 I-8
# sweep (Appendix C). Registering them must silence I-8 for every one — the whole
# point of the backfill (see registry.py). Ids captured live via findings_list.
_BACKFILLED_IDS = [
    "169484792332435402",  # 15 Branwood Drive
    "169484426295525198",  # 15 Branwood Mortgage
    "169484752533733308",  # SALESFORCE 401(k)
    "169483278280965580",  # Adams Checking
    "169483407858744830",  # Alexis Savings
    "169483407818898941",  # Braden Savings
    "169483407908027903",  # Colton Savings
    "169483407948922368",  # Dylan Savings
    "169483407379545595",  # Elaina Savings
    "169483407770664444",  # High Yield Savings
    "169486554571357755",  # Coinbase
    "169486005322005863",  # Robinhood
    "169512876984122668",  # Venmo
    "169484885260386277",  # Stock Plan (CRM)
    "169484885021310948",  # Salesforce ESPP
    "171945927021313088",  # Apple Cash
    "171945926972030015",  # Apple Card
    "169483725173571184",  # AT&T Universal Platinum
    "169483620975525442",  # Optima Platinum
    "169483620439703104",  # Blue from Amex
]


def test_i8_silent_for_all_backfilled_accounts():
    from financial_mcp.steward import registry

    accts = [_acct(aid, f"acct {aid}", 1000) for aid in _BACKFILLED_IDS]
    assert all(registry.is_known(aid) for aid in _BACKFILLED_IDS)
    assert I.detect_i8_new_account_entity(_ctx(accounts=accts)) == []


# --- runner --------------------------------------------------------------
def test_run_integrity_concatenates_and_covers_ids():
    lexi = _acct(LEXI_529, "Lexi 529 (02)", 40140.98, cf=True)
    out = I.run_integrity(_ctx(source="csv", accounts=[lexi], previous_carry_forward={LEXI_529: False}))
    detectors = {f.detector for f in out}
    assert "I-1" in detectors  # not live
    assert "I-3" in detectors  # 529 flip
    assert I.INTEGRITY_DETECTOR_IDS == ["I-1", "I-2", "I-3", "I-4", "I-5", "I-6", "I-7", "I-8"]
