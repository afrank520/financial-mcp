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
    is_hidden=False,
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
        is_hidden=is_hidden,
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


# --- I-9 (duplicate / superseded holding double-count) -------------------
BRADEN_529_LIVE = "237313796790435071"
BRADEN_529_OLD = "169485859791191359"


def test_i9_silent_when_old_entity_excluded_from_net_worth():
    # Today's real state: live 529 counts, old entity excluded. No double-count.
    live = _acct(BRADEN_529_LIVE, "Braden 529 (01)", 40000, inw=True)
    old = _acct(BRADEN_529_OLD, "Braden 529 (01, old entity)", 39500, inw=False)
    assert I.detect_i9_duplicate_holding(_ctx(accounts=[live, old])) == []


def test_i9_silent_when_old_entity_is_carry_forward():
    # Old entity counts in NW on paper but is carry-forward (a ghost) — its
    # balance isn't actually summing, so no active double-count.
    live = _acct(BRADEN_529_LIVE, "Braden 529 (01)", 40000, inw=True)
    old = _acct(BRADEN_529_OLD, "Braden 529 (01, old entity)", 39500, inw=True, cf=True)
    assert I.detect_i9_duplicate_holding(_ctx(accounts=[live, old])) == []


def test_i9_fires_high_when_lineage_pair_both_count():
    # The nightmare: the old entity re-enters net worth beside its live twin.
    # ~$39.5k double-counted; fire HIGH with the redundant (smaller) balance.
    live = _acct(BRADEN_529_LIVE, "Braden 529 (01)", 40000, inw=True)
    old = _acct(BRADEN_529_OLD, "Braden 529 (01, old entity)", 39500, inw=True)
    out = I.detect_i9_duplicate_holding(_ctx(accounts=[live, old]))
    assert len(out) == 1
    f = out[0]
    assert f.detector == "I-9"
    assert f.confidence == "high"
    assert f.distortion is True
    # dollars_at_stake = the redundant balance (smaller of the two)
    assert f.dollars_at_stake == 39500.0
    assert set(f.evidence.account_ids) == {BRADEN_529_LIVE, BRADEN_529_OLD}


def test_i9_fires_on_normalized_name_collision_without_registry_link():
    # Two unregistered-lineage accounts whose names normalize to the same holding
    # and both count — caught by the name-collision safety net.
    a = _acct("new-a", "Fidelity Brokerage", 10000, inw=True)
    b = _acct("new-b", "Fidelity Brokerage (old entity)", 9000, inw=True)
    out = I.detect_i9_duplicate_holding(_ctx(accounts=[a, b]))
    assert len(out) == 1
    assert out[0].detector == "I-9"
    assert out[0].dollars_at_stake == 9000.0


def test_i9_unregistered_third_copy_joins_the_lineage_group():
    # A, B are a registered live/old lineage pair; C is an UNREGISTERED third
    # copy whose name normalizes to the same holding. Union-find must fold all
    # three into one group so C's balance isn't split off and missed.
    live = _acct(BRADEN_529_LIVE, "Braden 529 (01)", 40000, inw=True)
    old = _acct(BRADEN_529_OLD, "Braden 529 (01, old entity)", 39500, inw=True)
    third = _acct("dupe-3", "Braden 529 (01) MX Old", 38000, inw=True)
    out = I.detect_i9_duplicate_holding(_ctx(accounts=[live, old, third]))
    assert len(out) == 1
    f = out[0]
    # keep the largest (40000); the other two are redundant
    assert f.dollars_at_stake == 39500.0 + 38000.0
    assert set(f.evidence.account_ids) == {BRADEN_529_LIVE, BRADEN_529_OLD, "dupe-3"}


def test_i9_does_not_collide_distinct_corporate_cards():
    # Conservative normalization must NOT merge two genuinely different accounts
    # that only differ by trailing (…digits) — the two Corporate Cards.
    a = _acct("cc-1", "Corporate Card (…1002)", 500, inw=True)
    b = _acct("cc-2", "Corporate Card (…1009)", 600, inw=True)
    assert I.detect_i9_duplicate_holding(_ctx(accounts=[a, b])) == []


def test_i9_balance_shadow_flags_unregistered_new_dup_of_registered_account():
    # The REAL re-auth case: a re-auth created a brand-new account with a totally
    # different name (not registered, no lineage) whose balance shadows an
    # existing registered account that also counts. Both sum → ~2x double-count.
    # BRADEN_529_LIVE is registered; the new "R Frank" copy is unregistered and
    # within ~2% of it. I-9 must fire even though name + lineage both miss.
    registered = _acct(BRADEN_529_LIVE, "Braden 529 (01) MX Old", 45727.53, inw=True)
    new_dup = _acct("new-rfrank-01", "Braden R Frank (…2-01)", 44922.47, inw=True)
    out = I.detect_i9_duplicate_holding(_ctx(accounts=[registered, new_dup]))
    assert len(out) == 1
    f = out[0]
    assert f.detector == "I-9" and f.confidence == "high" and f.distortion is True
    assert set(f.evidence.account_ids) == {BRADEN_529_LIVE, "new-rfrank-01"}
    # redundant = the smaller of the shadowed pair
    assert f.dollars_at_stake == 44922.47


def test_i9_balance_shadow_multi_sibling_makes_clean_pairs_not_a_merged_blob():
    # The real 5-kid live case: 5 registered "MX Old" 529s + 5 unregistered new
    # copies, sibling balances close enough that greedy nearest-neighbour crossed
    # children and merged 3 into one group while dropping a 4th. 1:1 global-greedy
    # must instead yield 5 clean 2-member findings, each with its own dollars, and
    # every one of the 10 accounts must appear in exactly one finding.
    reg = [
        _acct("237313796790435071", "Braden 529 (01) MX Old", 45727.53, inw=True),
        _acct("237313797006441728", "Lexi 529 (02) MX Old", 47139.88, inw=True),
        _acct("237313797074599169", "Colton 529 (03) MX Old", 49304.04, inw=True),
        _acct("237313797134368002", "Dylan 529 (04) MX Old", 28594.32, inw=True),
        _acct("237313797209865475", "Elaina 529 (05) MX Old", 29939.12, inw=True),
    ]
    new = [
        _acct("new-01", "Braden R Frank (…2-01)", 44922.47, inw=True),
        _acct("new-02", "Alexis R Frank (…2-02)", 46278.74, inw=True),
        _acct("new-03", "Colton R Frank (…2-03)", 48360.89, inw=True),
        _acct("new-04", "Dylan R Frank (…2-04)", 28040.89, inw=True),
        _acct("new-05", "Elaina R FRANK (…2-05)", 29345.53, inw=True),
    ]
    out = I.detect_i9_duplicate_holding(_ctx(accounts=reg + new))
    assert len(out) == 5
    for f in out:
        assert f.confidence == "high" and f.distortion is True
        assert len(f.evidence.account_ids) == 2  # clean pair, never a merged blob
    # every one of the 10 accounts is accounted for exactly once
    seen = [aid for f in out for aid in f.evidence.account_ids]
    assert sorted(seen) == sorted(a.account_id for a in reg + new)


def test_i9_balance_shadow_ignores_two_registered_accounts_of_similar_size():
    # Both accounts are KNOWN/registered and merely happen to hold similar
    # balances (Braden vs Alexis savings) — NOT a duplicate. The shadow path only
    # considers an UNREGISTERED account as the candidate new copy, so this is silent.
    a = _acct("169483407818898941", "Braden Savings (8533)", 18003.38, inw=True)
    b = _acct("169483407858744830", "Alexis Savings (9062)", 18100.00, inw=True)
    assert I.detect_i9_duplicate_holding(_ctx(accounts=[a, b])) == []


def test_i9_balance_shadow_respects_tolerance():
    # An unregistered account whose balance is far (>tolerance) from every
    # registered account is a genuinely new holding, not a shadow — silent.
    registered = _acct(BRADEN_529_LIVE, "Braden 529 (01) MX Old", 45727.53, inw=True)
    far = _acct("new-unrelated", "Some New Brokerage", 5000.00, inw=True)
    assert I.detect_i9_duplicate_holding(_ctx(accounts=[registered, far])) == []


def test_i9_balance_shadow_has_materiality_floor():
    # Many accounts sit at $0.00; the shadow path must not pair them all up.
    reg_zero = _acct("169483725173571184", "AT&T Card", 0.0, inw=True)  # registered
    new_zero = _acct("new-zero", "Brand New Zero", 0.0, inw=True)        # unregistered
    assert I.detect_i9_duplicate_holding(_ctx(accounts=[reg_zero, new_zero])) == []


def test_i9_flags_hidden_account_still_counting_in_net_worth():
    hidden = _acct("hid-1", "Old Brokerage", 25000, inw=True, is_hidden=True)
    out = I.detect_i9_duplicate_holding(_ctx(accounts=[hidden]))
    assert len(out) == 1
    f = out[0]
    assert f.detector == "I-9"
    assert f.confidence == "high" and f.distortion is True
    assert f.dollars_at_stake == 25000.0
    assert "hid-1" in f.evidence.account_ids


def test_i9_silent_for_hidden_account_excluded_from_net_worth():
    # Hidden AND excluded is the normal, correct state — no distortion.
    hidden = _acct("hid-2", "Archived", 25000, inw=False, is_hidden=True)
    assert I.detect_i9_duplicate_holding(_ctx(accounts=[hidden])) == []


def test_i9_hidden_finding_has_stable_id_across_runs():
    hidden = _acct("hid-1", "Old Brokerage", 25000, inw=True, is_hidden=True)
    a = I.detect_i9_duplicate_holding(_ctx(accounts=[hidden]))[0]
    b = I.detect_i9_duplicate_holding(_ctx(accounts=[hidden]))[0]
    assert a.id == b.id


def test_i9_duplicate_finding_has_stable_id_across_runs():
    live = _acct(BRADEN_529_LIVE, "Braden 529 (01)", 40000, inw=True)
    old = _acct(BRADEN_529_OLD, "Braden 529 (01, old entity)", 39500, inw=True)
    a = I.detect_i9_duplicate_holding(_ctx(accounts=[live, old]))[0]
    b = I.detect_i9_duplicate_holding(_ctx(accounts=[live, old]))[0]
    assert a.id == b.id


# --- runner --------------------------------------------------------------
def test_run_integrity_concatenates_and_covers_ids():
    lexi = _acct(LEXI_529, "Lexi 529 (02)", 40140.98, cf=True)
    out = I.run_integrity(_ctx(source="csv", accounts=[lexi], previous_carry_forward={LEXI_529: False}))
    detectors = {f.detector for f in out}
    assert "I-1" in detectors  # not live
    assert "I-3" in detectors  # 529 flip


def test_run_integrity_covers_i9_id():
    assert "I-9" in I.INTEGRITY_DETECTOR_IDS
    assert I.detect_i9_duplicate_holding in I._DETECTORS
    assert I.INTEGRITY_DETECTOR_IDS == ["I-1", "I-2", "I-3", "I-4", "I-5", "I-6", "I-7", "I-8", "I-9"]
