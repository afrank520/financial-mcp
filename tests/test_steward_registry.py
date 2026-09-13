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
