"""Steward orchestrator — ties cache + policy + ledger + detectors together.

`integrity_check()` builds an IntegrityContext from the DataService, runs the
I-* detectors, reconciles the results into the findings ledger, remembers each
account's carry-forward flag for the next run's transition detection, and shapes
the run-level output: integrity items high-first, at most one needs_input per
run, a backlog count, and a trust flag that goes "degraded" whenever any
high-confidence integrity finding surfaces (spec §5.4 — the numbers below such a
finding are unverified).
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.analytics import balances, networth
from financial_mcp.steward.detectors.integrity import (
    INTEGRITY_DETECTOR_IDS,
    IntegrityContext,
    run_integrity,
)
from financial_mcp.steward.ledger import Ledger
from financial_mcp.steward.policy import Policy, load_policy


class Steward:
    def __init__(self, svc, ledger: Ledger, policy_loader=load_policy) -> None:
        self.svc = svc
        self.ledger = ledger
        self._load_policy = policy_loader
        self._policy: Policy | None = None

    def policy(self) -> Policy:
        if self._policy is None:
            self._policy = self._load_policy()
        return self._policy

    # --- context ----------------------------------------------------------
    def _budgets_available(self, as_of: datetime.date) -> bool:
        start = as_of.replace(day=1)
        try:
            return bool(self.svc.budgets(start, as_of))
        except Exception:
            return False

    def _build_context(self, as_of: datetime.date) -> IntegrityContext:
        svc = self.svc
        accounts = balances.merge_carry_forward(svc.accounts(), svc.balance_history())
        snaps = svc.balance_history()
        latest = balances.latest_snapshot_date(snaps)

        computed_nw: Decimal | None = None
        monarch_nw: Decimal | None = None
        if latest is not None:
            computed_nw = networth.net_worth_as_of(snaps, latest)["net_worth"]
            # Compare only against Monarch's reported number on the SAME date, so
            # a date-alignment gap can't masquerade as an I-4 mismatch.
            by_date = {s.date: s.net_worth for s in svc.networth_history()}
            monarch_nw = by_date.get(latest)

        # epoch-0 leftover: a type that still has payload but reads never-refreshed
        epoch_zero = [
            r["data_type"]
            for r in svc.cache_status()
            if r["count"] > 0 and r["age_seconds"] is None and not r["fresh"]
        ]

        return IntegrityContext(
            as_of=as_of,
            policy=self.policy(),
            source=svc.source_name(),
            accounts=accounts,
            transactions=svc.transactions(),
            computed_net_worth=computed_nw,
            monarch_net_worth=monarch_nw,
            budgets_available=self._budgets_available(as_of),
            refresh_errors=[],
            epoch_zero_types=epoch_zero,
            previous_carry_forward=self.ledger.previous_carry_forward(),
        )

    # --- run --------------------------------------------------------------
    def check_with_context(self, ctx: IntegrityContext) -> dict:
        """Core run: detect → reconcile → remember state → shape. Pure of I/O
        beyond the ledger, so it's directly testable with a hand-built context."""
        findings = run_integrity(ctx)
        result = self.ledger.reconcile(findings, detectors_run=INTEGRITY_DETECTOR_IDS)
        # Remember carry-forward flags AFTER detectors read the prior run's, so the
        # next run can spot a transition.
        self.ledger.record_account_state(
            {a.account_id: a.is_carry_forward for a in ctx.accounts if a.account_id}
        )
        return self._shape(result)

    def integrity_check(self, as_of: datetime.date | None = None) -> dict:
        ctx = self._build_context(as_of or datetime.date.today())
        return self.check_with_context(ctx)

    def _shape(self, result: dict) -> dict:
        surfaced = result["surfaced"]
        needs_input = [f for f in surfaced if f.get("confidence") == "needs_input"]
        items = [f for f in surfaced if f.get("confidence") != "needs_input"]
        # high-confidence first, then largest dollars — integrity leads the brief
        items.sort(key=lambda f: (0 if f["confidence"] == "high" else 1, -f["dollars_at_stake"]))
        needs_input.sort(key=lambda f: -f["dollars_at_stake"])
        trust = "degraded" if any(f["confidence"] == "high" for f in items) else "ok"
        return {
            "trust": trust,
            "items": items,
            "needs_input": needs_input[:1],  # spec §5.4: at most one per run
            "backlog_count": len(result["backlog"]),
            "auto_resolved": [f["id"] for f in result["auto_resolved"]],
            "methodology": (
                "Integrity detectors I-1..I-8 over the live cache, reconciled into "
                "findings.db; items ranked integrity-first (high confidence, then "
                "dollars). trust is 'degraded' whenever any high integrity finding "
                "surfaces — numbers elsewhere are then unverified."
            ),
        }
