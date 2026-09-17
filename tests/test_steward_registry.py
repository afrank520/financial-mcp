"""Task 2 — account registry (Steward Phase 2, spec Appendix C).

The registry is the steward's memory of what each Monarch account *is*, so the
integrity detectors can tell a benign carry-forward (a dead 401k feed we expect)
from an alarming one (a live 529 that just flipped and silently dropped its
balance out of net worth). Membership is by Monarch account id.
"""
from __future__ import annotations

from financial_mcp.steward import registry


def test_five_529s_are_the_watch_carry_forward_set():
    assert registry.WATCH_529_IDS == {
        "237313796790435071",  # Braden 529 (01)
        "237313797006441728",  # Lexi 529 (02)
        "237313797074599169",  # Colton 529 (03)
        "237313797134368002",  # Dylan 529 (04)
        "237313797209865475",  # Elaina 529 (05)
    }
    for aid in registry.WATCH_529_IDS:
        assert registry.is_watch_carry_forward(aid) is True
        # a 529 is live-and-watched, NOT registry-expected to carry forward:
        # if it flips, that's the I-3 alarm, not a benign expectation.
        assert registry.is_expected_carry_forward(aid) is False


def test_expected_carry_forward_accounts():
    # AgentSync 401k, the closed 8300 HELOC, and the Finicity 403b are all
    # expected to carry forward — flipping is normal, no I-3.
    assert registry.is_expected_carry_forward("183541300701576552") is True  # AgentSync
    assert registry.is_expected_carry_forward("169483278367997389") is True  # 8300 HELOC (closed)
    assert registry.is_expected_carry_forward("169487170068692772") is True  # 403b Finicity
    # pending-confirm Apple Savings old entity also counts as expected
    assert registry.is_expected_carry_forward("171945926909115454") is True


def test_live_accounts_are_not_expected_to_carry_forward():
    assert registry.is_expected_carry_forward("251845388366920938") is False  # 2800 HELOC live
    assert registry.is_expected_carry_forward("233056032900660408") is False  # Amex Gold live


def test_registry_state_returns_name_and_flags():
    e = registry.registry_state("233056032900660408")
    assert e is not None
    assert "American Express" in e.name
    assert "amex_float" in e.states
    assert "live" in e.states


def test_unknown_account_is_not_known():
    # An id absent from the registry is the I-8 trigger (new entity / re-link).
    assert registry.is_known("999999999999999999") is False
    assert registry.registry_state("999999999999999999") is None
    # every seeded account is known
    assert registry.is_known("237313797006441728") is True


def test_new_entity_savings_is_flagged_new():
    e = registry.registry_state("253413397873179968")
    assert e is not None
    assert "new" in e.states


def test_five_live_529s_supersede_their_old_entities():
    # Lineage (I-9): each re-authed live 529 replaces its March-migration old
    # entity. The names differ across the pair (Lexi 529 (02) vs Alexis 529 (02,
    # old entity)), so only this explicit link ties them to the same holding.
    expected = {
        "237313796790435071": "169485859791191359",  # Braden 529 (01)
        "237313797006441728": "169485859965254976",  # Lexi 529 (02) → Alexis old
        "237313797074599169": "169485860012440897",  # Colton 529 (03)
        "237313797134368002": "169485860059626818",  # Dylan 529 (04)
        "237313797209865475": "169485860105764163",  # Elaina 529 (05)
    }
    for live_id, old_id in expected.items():
        assert registry.supersedes_id(live_id) == old_id
        # the old entity is registered too, so the pair is fully known
        assert registry.is_known(old_id) is True


def test_supersedes_id_is_none_for_non_lineage_accounts():
    # A normal live account supersedes nothing.
    assert registry.supersedes_id("169483278280965580") is None  # Adams Checking
    # An unknown id likewise.
    assert registry.supersedes_id("999999999999999999") is None


def test_supersedes_values_are_unique():
    # The reverse lineage map (_SUPERSEDED_BY) would silently drop a collision if
    # two live entries ever declared the same old id. Guard that invariant.
    olds = [e.supersedes for e in registry.REGISTRY.values() if e.supersedes]
    assert len(olds) == len(set(olds))
    # every superseded old id is itself a registered account
    for old in olds:
        assert registry.is_known(old)


def test_same_holding_links_both_directions():
    # same_holding pairs a live account with the old entity it replaces, in
    # either lookup direction, so the I-9 detector can group them.
    live, old = "237313796790435071", "169485859791191359"
    assert registry.same_holding(live) == old
    assert registry.same_holding(old) == live
    assert registry.same_holding("169483278280965580") is None
