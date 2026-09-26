"""Task 1 — Finding model (Steward Phase 2, spec §5.3 / Appendix B).

The Finding is the ledger's row and the detectors' output. Its identity must be
stable across runs (`id = sha1(detector + subject_key)`) so the same issue
re-detected next run upserts the same row, and its priority must follow Adam's
metric (§5.4): distortion × dollars_at_stake ÷ actions_to_fix, zero when the
issue doesn't distort a number Adam reads.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from financial_mcp.steward.findings import (
    Finding,
    compute_priority,
    finding_id,
)


def test_finding_id_is_stable_and_subject_scoped():
    a = finding_id("I-3", "acct:237313797006441728")
    assert a == finding_id("I-3", "acct:237313797006441728")  # deterministic
    assert len(a) == 40 and all(c in "0123456789abcdef" for c in a)  # sha1 hex
    # different detector or subject -> different id
    assert a != finding_id("I-2", "acct:237313797006441728")
    assert a != finding_id("I-3", "acct:237313797074599169")


def test_compute_priority_uses_adams_metric():
    # distortion true: dollars / actions
    assert compute_priority(True, 436557.87, 1) == pytest.approx(436557.87)
    assert compute_priority(True, 1000.0, 4) == pytest.approx(250.0)


def test_compute_priority_zero_when_no_distortion():
    # cosmetic issues (right number, wrong-looking label) go to backlog
    assert compute_priority(False, 999999.0, 1) == 0.0


def test_compute_priority_never_divides_by_zero():
    # actions_to_fix is floored at 1 (schema minimum) so priority is finite
    assert compute_priority(True, 500.0, 0) == pytest.approx(500.0)


def test_finding_new_sets_id_priority_and_defaults():
    f = Finding.new(
        detector="I-3",
        subject_key="acct:237313797006441728",
        layer="integrity",
        title="529 flipped to carry-forward",
        dollars_at_stake=200704.89,
        distortion=True,
        actions_to_fix=1,
        confidence="high",
    )
    assert f.id == finding_id("I-3", "acct:237313797006441728")
    assert f.priority == pytest.approx(200704.89)
    assert f.status == "new"
    assert f.first_seen is not None and f.last_seen is not None


def test_detector_pattern_is_enforced():
    for bad in ["X-1", "I1", "i-3", "I-", "M3a"]:
        with pytest.raises(ValidationError):
            Finding.new(
                detector=bad,
                subject_key="s",
                layer="integrity",
                title="t",
                dollars_at_stake=0.0,
                distortion=False,
                actions_to_fix=1,
                confidence="high",
            )
    # valid forms incl. the optional letter suffix (e.g. M-5b)
    for good in ["I-3", "M-11", "P-1", "A-6", "M-5b"]:
        Finding.new(
            detector=good,
            subject_key="s",
            layer="fix",
            title="t",
            dollars_at_stake=0.0,
            distortion=False,
            actions_to_fix=1,
            confidence="high",
        )


def test_enums_and_actions_floor_are_enforced():
    base = dict(
        detector="I-3",
        subject_key="s",
        layer="integrity",
        title="t",
        dollars_at_stake=0.0,
        distortion=False,
        confidence="high",
    )
    with pytest.raises(ValidationError):
        Finding.new(**base, actions_to_fix=0)  # below minimum 1
    with pytest.raises(ValidationError):
        Finding.new(**{**base, "layer": "bogus"}, actions_to_fix=1)
    with pytest.raises(ValidationError):
        Finding.new(**{**base, "confidence": "maybe"}, actions_to_fix=1)


def test_proposed_action_requires_dry_run_first_true():
    ok = Finding.new(
        detector="M-2",
        subject_key="merch:lipa",
        layer="fix",
        title="split categorization",
        dollars_at_stake=100.0,
        distortion=True,
        actions_to_fix=2,
        confidence="high",
        proposed_action={
            "tool": "recategorize_matching",
            "args": {"merchant": "LIPA", "category": "Electric"},
            "dry_run_first": True,
            "reversible": True,
        },
    )
    assert ok.proposed_action.dry_run_first is True
    with pytest.raises(ValidationError):
        Finding.new(
            detector="M-2",
            subject_key="merch:lipa",
            layer="fix",
            title="t",
            dollars_at_stake=100.0,
            distortion=True,
            actions_to_fix=2,
            confidence="high",
            proposed_action={
                "tool": "recategorize_matching",
                "args": {},
                "dry_run_first": False,  # must be True
                "reversible": True,
            },
        )


def test_finding_round_trips_through_model_dump():
    f = Finding.new(
        detector="I-3",
        subject_key="acct:1",
        layer="integrity",
        title="t",
        summary="s",
        dollars_at_stake=1.0,
        distortion=True,
        actions_to_fix=1,
        confidence="high",
        evidence={"account_ids": ["1"], "figures": {"removed": 1.0}},
    )
    d = f.model_dump(mode="json")
    again = Finding(**d)
    assert again.id == f.id
    assert again.evidence.account_ids == ["1"]


def test_dollars_are_the_distortion_defaults_false_and_round_trips():
    # #8: an opt-in flag marking findings where the dollar amount IS the measure
    # of the distortion (I-9 double-count) — so a $0 instance is materially inert.
    # Defaults False so no existing finding changes behavior; must survive dump.
    f = Finding.new(
        detector="I-1",
        subject_key="source",
        layer="integrity",
        title="Not connected live",
        dollars_at_stake=0.0,
        distortion=True,
        actions_to_fix=1,
        confidence="high",
    )
    assert f.dollars_are_the_distortion is False  # default: opt-in only

    g = Finding.new(
        detector="I-9",
        subject_key="dupe:a:b",
        layer="integrity",
        title="Same holding counted twice",
        dollars_at_stake=0.0,
        distortion=True,
        actions_to_fix=1,
        confidence="high",
        dollars_are_the_distortion=True,
    )
    assert g.dollars_are_the_distortion is True
    again = Finding(**g.model_dump(mode="json"))
    assert again.dollars_are_the_distortion is True
