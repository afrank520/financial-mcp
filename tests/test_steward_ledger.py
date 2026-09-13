"""Task 3 — the findings ledger (Steward Phase 2, spec §5.2).

The ledger is SQLite at ~/.financial_mcp/findings.db. It gives findings memory
across runs: a re-detected issue upserts the same row (stable id) rather than
piling up; a dismissed-with-reason finding stays suppressed until its dollars
materially change; a snoozed one reappears after its date; a finding a detector
stops producing auto-resolves; and anything surfaced 3x without action drops to
backlog. It also remembers each account's carry-forward flag so I-3 can spot a
transition since the last run.
"""
from __future__ import annotations

import datetime

from financial_mcp.steward.findings import Finding
from financial_mcp.steward.ledger import Ledger


class Clock:
    def __init__(self, t: datetime.datetime):
        self.t = t

    def __call__(self) -> datetime.datetime:
        return self.t


def _utc(y, m, d):
    return datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)


def _f(detector="I-3", subject="acct:1", dollars=1000.0, distortion=True, actions=1):
    return Finding.new(
        detector=detector,
        subject_key=subject,
        layer="integrity",
        title="t",
        dollars_at_stake=dollars,
        distortion=distortion,
        actions_to_fix=actions,
        confidence="high",
    )


def _ledger(tmp_path, clock):
    return Ledger(str(tmp_path / "findings.db"), now=clock)


def test_first_reconcile_inserts_new_and_surfaces(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    f = _f()
    res = led.reconcile([f], detectors_run=["I-3"])
    assert [r["id"] for r in res["surfaced"]] == [f.id]
    row = led.get(f.id)
    assert row["status"] == "new"
    assert row["times_shown"] == 1


def test_reruns_upsert_same_row_and_bump_times_shown(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    f = _f(dollars=1000.0)
    led.reconcile([f], detectors_run=["I-3"])
    first_seen = led.get(f.id)["first_seen"]

    clock.t = _utc(2026, 9, 12)
    f2 = _f(dollars=1250.0)  # same id, updated dollars
    led.reconcile([f2], detectors_run=["I-3"])
    row = led.get(f.id)
    assert row["times_shown"] == 2
    assert row["first_seen"] == first_seen  # preserved
    assert row["last_seen"] != first_seen  # advanced
    assert row["dollars_at_stake"] == 1250.0  # refreshed from new detection


def test_dismiss_requires_reason_and_suppresses_next_run(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    f = _f(dollars=1000.0)
    led.reconcile([f], detectors_run=["I-3"])

    try:
        led.update(f.id, "dismissed")  # no reason
        raise AssertionError("expected dismissal without reason to fail")
    except ValueError:
        pass

    led.update(f.id, "dismissed", reason="known — 401k feed intentionally frozen")
    # next run: same finding re-detected, unchanged dollars -> stays suppressed
    clock.t = _utc(2026, 9, 12)
    res = led.reconcile([_f(dollars=1000.0)], detectors_run=["I-3"])
    assert res["surfaced"] == []
    assert f.id in [r["id"] for r in res["suppressed"]]
    assert led.get(f.id)["status"] == "dismissed"


def test_dismissed_resurfaces_when_dollars_materially_increase(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    led.reconcile([_f(dollars=1000.0)], detectors_run=["I-3"])
    fid = _f(dollars=1000.0).id
    led.update(fid, "dismissed", reason="acknowledged")

    # +50% and >= $250 absolute -> suppression lifts, re-opens
    clock.t = _utc(2026, 9, 20)
    res = led.reconcile([_f(dollars=1600.0)], detectors_run=["I-3"])
    assert fid in [r["id"] for r in res["surfaced"]]
    assert led.get(fid)["status"] == "open"


def test_dismissed_stays_suppressed_on_small_dollar_change(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    led.reconcile([_f(dollars=1000.0)], detectors_run=["I-3"])
    fid = _f(dollars=1000.0).id
    led.update(fid, "dismissed", reason="acknowledged")

    clock.t = _utc(2026, 9, 20)
    res = led.reconcile([_f(dollars=1100.0)], detectors_run=["I-3"])  # +10%
    assert res["surfaced"] == []
    assert led.get(fid)["status"] == "dismissed"


def test_snooze_hides_until_date_then_reappears(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    led.reconcile([_f()], detectors_run=["I-3"])
    fid = _f().id
    led.update(fid, "snoozed", snooze_until="2026-10-01")

    clock.t = _utc(2026, 9, 20)  # before snooze_until
    assert led.reconcile([_f()], detectors_run=["I-3"])["surfaced"] == []

    clock.t = _utc(2026, 10, 2)  # after snooze_until
    res = led.reconcile([_f()], detectors_run=["I-3"])
    assert fid in [r["id"] for r in res["surfaced"]]


def test_auto_resolve_when_detector_stops_producing(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    f = _f()
    led.reconcile([f], detectors_run=["I-3"])

    clock.t = _utc(2026, 9, 12)
    res = led.reconcile([], detectors_run=["I-3"])  # I-3 ran, produced nothing
    row = led.get(f.id)
    assert row["status"] == "resolved"
    assert row["resolved_by"] == "data"
    assert f.id in [r["id"] for r in res["auto_resolved"]]


def test_auto_resolve_only_for_detectors_that_ran(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    f = _f(detector="I-3", subject="acct:1")
    led.reconcile([f], detectors_run=["I-3"])

    # a later run that did NOT run I-3 must not touch its findings
    clock.t = _utc(2026, 9, 12)
    led.reconcile([], detectors_run=["I-2"])
    assert led.get(f.id)["status"] == "new"


def test_backlog_demotion_after_three_unactioned_showings(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    fid = _f().id
    for day in (11, 12, 13):
        clock.t = _utc(2026, 9, day)
        res = led.reconcile([_f()], detectors_run=["I-3"])
    assert led.get(fid)["times_shown"] == 3
    assert led.get(fid)["is_backlog"] is True
    # backlog items are surfaced separately, not in the main surfaced list
    assert fid not in [r["id"] for r in res["surfaced"]]
    assert fid in [r["id"] for r in res["backlog"]]


def test_list_filters_by_status_and_detector(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    led.reconcile([_f(detector="I-3", subject="a"), _f(detector="I-2", subject="b")],
                  detectors_run=["I-3", "I-2"])
    assert len(led.list()) == 2
    assert len(led.list(detector="I-3")) == 1
    assert len(led.list(status="new")) == 2
    assert len(led.list(status="dismissed")) == 0
    assert len(led.list(limit=1)) == 1


def _needs_input(subject="acct:9"):
    return Finding.new(
        detector="I-8",
        subject_key=subject,
        layer="integrity",
        title="new account",
        dollars_at_stake=1234.0,
        distortion=False,
        actions_to_fix=1,
        confidence="needs_input",
        status="needs_input",
    )


def test_insert_preserves_detector_status_and_exempts_needs_input_from_backlog(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    f = _needs_input()
    # surfaced across 3+ runs — a needs_input question must NOT drop to backlog
    for day in (11, 12, 13, 14):
        clock.t = _utc(2026, 9, day)
        led.reconcile([_needs_input()], detectors_run=["I-8"])
    row = led.get(f.id)
    assert row["status"] == "needs_input"
    assert row["is_backlog"] is False
    # and it's findable by its real status, not miscategorized as "new"
    assert f.id in [r["id"] for r in led.list(status="needs_input")]
    assert led.list(status="new") == []


def test_update_rejects_unknown_status(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    f = _f()
    led.reconcile([f], detectors_run=["I-3"])
    try:
        led.update(f.id, "resolvd")  # typo
        raise AssertionError("expected unknown status to be rejected")
    except ValueError as exc:
        assert "unknown status" in str(exc)
    assert led.get(f.id)["status"] == "new"  # unchanged


def test_resurfaced_finding_is_not_backlog_demoted_on_stale_count(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    led = _ledger(tmp_path, clock)
    # accrue times_shown to the backlog threshold, then dismiss
    for day in (11, 12, 13):
        clock.t = _utc(2026, 9, day)
        led.reconcile([_f(dollars=1000.0)], detectors_run=["I-3"])
    fid = _f(dollars=1000.0).id
    led.update(fid, "dismissed", reason="ack")

    # dollars jump materially -> suppression lifts; must SURFACE, not hide in backlog
    clock.t = _utc(2026, 9, 20)
    res = led.reconcile([_f(dollars=2000.0)], detectors_run=["I-3"])
    assert fid in [r["id"] for r in res["surfaced"]]
    assert fid not in [r["id"] for r in res["backlog"]]
    assert led.get(fid)["is_backlog"] is False


def test_account_carry_forward_state_round_trips_across_instances(tmp_path):
    clock = Clock(_utc(2026, 9, 11))
    path = str(tmp_path / "findings.db")
    led = Ledger(path, now=clock)
    assert led.previous_carry_forward() == {}  # first run: nothing known
    led.record_account_state({"acct:1": True, "acct:2": False})

    reopened = Ledger(path, now=clock)
    assert reopened.previous_carry_forward() == {"acct:1": True, "acct:2": False}
