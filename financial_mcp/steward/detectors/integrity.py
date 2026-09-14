"""Integrity detectors I-1…I-8 (Steward Phase 2, spec §6.1).

Each detector is a pure function of an `IntegrityContext` — the already-fetched
cache data plus policy, the ledger's prior per-account carry-forward memory, and
the as-of date — returning zero or more Findings. The orchestrator (Task 5)
builds the context, runs `run_integrity`, and reconciles the results into the
ledger; it also enforces the run-level rules (integrity overrides ranking; at
most one needs_input per run).

The headline is I-3: an account flipping is_carry_forward false→true silently
drops its balance out of net worth (the $436k-of-ghosts class of bug), and the
five live 529s are on a special watch list.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from financial_mcp.monarch.models import Account, Transaction
from financial_mcp.steward import registry
from financial_mcp.steward.findings import Finding
from financial_mcp.steward.policy import Policy


@dataclass
class IntegrityContext:
    as_of: datetime.date
    policy: Policy
    source: str = "live"
    accounts: list[Account] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    # net worth: our §2.1 recompute vs Monarch's own reported number
    computed_net_worth: Decimal | None = None
    monarch_net_worth: Decimal | None = None
    budgets_available: bool = True
    refresh_errors: list[str] = field(default_factory=list)
    epoch_zero_types: list[str] = field(default_factory=list)
    # ledger memory: account_id -> is_carry_forward as of the previous run
    previous_carry_forward: dict[str, bool] = field(default_factory=dict)


def _f(**kw) -> Finding:
    return Finding.new(layer="integrity", **kw)


def detect_i1_not_live(ctx: IntegrityContext) -> list[Finding]:
    """Source isn't live (or session missing): every number below is unverified."""
    if ctx.source == "live":
        return []
    return [
        _f(
            detector="I-1",
            subject_key="source",
            title="Not connected live to Monarch",
            summary=f"Effective source is '{ctx.source}'. Figures may be stale; "
            "reconnect with financial-mcp-login before trusting them.",
            dollars_at_stake=0.0,
            distortion=True,
            actions_to_fix=1,
            confidence="high",
        )
    ]


def detect_i2_stale_data(ctx: IntegrityContext) -> list[Finding]:
    """Live-linked, non-expected accounts whose snapshot is older than stale_days."""
    cutoff = ctx.as_of - datetime.timedelta(days=ctx.policy.stale_days)
    out: list[Finding] = []
    for a in ctx.accounts:
        if registry.is_expected_carry_forward(a.account_id) or a.is_carry_forward:
            continue  # expected/ghost staleness is I-3's concern, not I-2
        if a.snapshot_date is not None and a.snapshot_date < cutoff:
            out.append(
                _f(
                    detector="I-2",
                    subject_key=f"acct:{a.account_id}",
                    title=f"Stale balance: {a.name}",
                    summary=f"Last snapshot {a.snapshot_date} is older than "
                    f"{ctx.policy.stale_days}d (as of {ctx.as_of}).",
                    dollars_at_stake=abs(float(a.latest_balance)),
                    distortion=True,
                    actions_to_fix=1,
                    confidence="high",
                    evidence={"account_ids": [a.account_id], "dates": [str(a.snapshot_date)]},
                )
            )
    # whole-feed staleness: newest transaction older than the cutoff
    if ctx.transactions:
        newest = max(t.date for t in ctx.transactions)
        if newest < cutoff:
            out.append(
                _f(
                    detector="I-2",
                    subject_key="transactions",
                    title="Transaction feed is stale",
                    summary=f"Newest transaction {newest} is older than "
                    f"{ctx.policy.stale_days}d (as of {ctx.as_of}).",
                    dollars_at_stake=0.0,
                    distortion=True,
                    actions_to_fix=1,
                    confidence="high",
                    evidence={"dates": [str(newest)]},
                )
            )
    return out


def detect_i3_carry_forward_transition(ctx: IntegrityContext) -> list[Finding]:
    """Account is carry-forward and NOT registry-expected — its balance is
    silently out of net worth (the $436k-of-ghosts class of bug).

    Fires EVERY run while that durable condition holds, not only on the
    false→true transition. This matters: the ledger auto-resolves any finding a
    detector *stops* producing, so a transition-only I-3 would go quiet after one
    run and the ledger would mark a still-live distortion "resolved". Re-asserting
    each run means auto-resolve fires only when the account actually un-flips.

    The prior-run flag and the 529 watch list only shape the wording; the durable
    condition (carry-forward AND not registry-expected) is what fires. A genuinely
    intentional carry-forward the registry doesn't cover is meant to be dismissed
    once with a reason, after which the ledger keeps it suppressed.
    """
    out: list[Finding] = []
    for a in ctx.accounts:
        if not a.is_carry_forward or registry.is_expected_carry_forward(a.account_id):
            continue
        # An account already excluded from net worth can't distort it by carrying
        # forward — nothing "left" (removed would be $0), so firing high here just
        # cries wolf and flips trust to "degraded" on noise. A carry-forward
        # excluded account is the registry's / I-8's concern, not I-3's.
        # (Live-prove 2026-09-13: without this, the excluded legacy-529 range plus
        # the excluded Corporate Card / Tracey loan fired 7 high I-3s at $0.00 and
        # falsely degraded trust.)
        if not a.include_in_net_worth:
            continue
        was_cf = ctx.previous_carry_forward.get(a.account_id)
        transitioned = was_cf is False  # explicitly known-not-carry-forward before
        watched = registry.is_watch_carry_forward(a.account_id)
        removed = abs(float(a.latest_balance))
        if watched:
            prefix, verb = "529 flipped to carry-forward", "silently left"
        elif transitioned:
            prefix, verb = "Account flipped to carry-forward", "silently left"
        else:
            prefix, verb = "Account is carry-forward", "is out of"
        out.append(
            _f(
                detector="I-3",
                subject_key=f"acct:{a.account_id}",
                title=f"{prefix}: {a.name}",
                summary=f"{a.name} is carry-forward; ${removed:,.2f} {verb} "
                "net worth. Reconnect the feed or confirm the account state.",
                dollars_at_stake=removed,
                distortion=True,
                actions_to_fix=1,
                confidence="high",
                evidence={"account_ids": [a.account_id], "figures": {"removed_from_net_worth": removed}},
            )
        )
    return out


def detect_i4_net_worth_mismatch(ctx: IntegrityContext) -> list[Finding]:
    """Our §2.1 recompute disagrees with Monarch's reported net worth (tol $1)."""
    if ctx.computed_net_worth is None or ctx.monarch_net_worth is None:
        return []
    residual = ctx.computed_net_worth - ctx.monarch_net_worth
    if abs(residual) <= Decimal("1"):
        return []
    return [
        _f(
            detector="I-4",
            subject_key="networth",
            title="Net worth recompute disagrees with Monarch",
            summary=f"§2.1 recompute {ctx.computed_net_worth} vs Monarch "
            f"{ctx.monarch_net_worth}; residual {residual} exceeds $1 tolerance.",
            dollars_at_stake=abs(float(residual)),
            distortion=True,
            actions_to_fix=1,
            confidence="high",
            evidence={"figures": {
                "computed": float(ctx.computed_net_worth),
                "monarch": float(ctx.monarch_net_worth),
                "residual": float(residual),
            }},
        )
    ]


def detect_i5_budgets_missing(ctx: IntegrityContext) -> list[Finding]:
    """budget_status empty/unavailable (e.g. CSV mode drops budgets)."""
    if ctx.budgets_available:
        return []
    return [
        _f(
            detector="I-5",
            subject_key="budgets",
            title="Budgets unavailable",
            summary="budget_status returned nothing — budget checks can't run.",
            dollars_at_stake=0.0,
            distortion=False,
            actions_to_fix=1,
            confidence="high",
        )
    ]


def _i6_account_reasons(a: Account, ctx: IntegrityContext) -> list[str]:
    """Which refresh problems (if any) a live-credentialed account has.

    Accounts with no credential (manual entries, Zestimate-backed valuations like
    Branwood) are *supposed* never to sync, so they're exempt — returning [] keeps
    an old last_updated from crying wolf. Only accounts Monarch links to a data
    provider are held to the "should be fresh" standard.
    """
    if not a.has_credential:
        return []
    # Registry guard (live-prove 2026-09-14: without it, 13 of 18 findings were
    # registry-known-dead accounts — a 72% noise rate on the daily gate). Mirror
    # I-3's two exemptions: a feed problem on an account whose carry-forward is
    # *expected* (a known frozen 401k/HELOC) or that is *excluded from net worth*
    # (legacy 529s, excluded cards) distorts no figure Adam reads, so it's noise.
    # The five live 529s survive this: they're watch_carry_forward (not expected)
    # and included in net worth, so their update_required still fires.
    if registry.is_expected_carry_forward(a.account_id) or not a.include_in_net_worth:
        return []
    reasons: list[str] = []
    if a.credential_disconnected:
        reasons.append("disconnected")
    if a.credential_update_required:
        reasons.append("update_required")
    if a.sync_disabled:
        reasons.append("sync_disabled")
    if a.is_deactivated:
        reasons.append("deactivated")
    # Stale: a live feed whose last real update is older than the I-6 threshold.
    # last_updated is None when it never refreshed (or was epoch-0, normalized to
    # None by the adapter) — that's a frozen feed too.
    cutoff_dt = datetime.datetime.combine(
        ctx.as_of - datetime.timedelta(days=ctx.policy.i6_stale_days),
        datetime.time.min,
        tzinfo=datetime.timezone.utc,
    )
    lu = a.last_updated
    if lu is not None and lu.tzinfo is None:
        lu = lu.replace(tzinfo=datetime.timezone.utc)
    if lu is None or lu < cutoff_dt:
        reasons.append("stale")
    return reasons


_I6_REASON_TEXT = {
    "disconnected": "the bank/provider link is disconnected",
    "update_required": "the connection needs re-authentication",
    "sync_disabled": "syncing is turned off",
    "deactivated": "the account is deactivated",
    "stale": "it hasn't refreshed within the freshness window",
}


def detect_i6_refresh_failure(ctx: IntegrityContext) -> list[Finding]:
    """Refresh integrity: per-account feed problems, plus type-level epoch-0.

    One finding PER affected account (stable id keyed by account_id) so each
    resolves independently as its feed recovers; the reasons list carries every
    applicable signal. A separate refresh-level finding covers data types that
    read as never-refreshed / epoch-0 (the D-2/D-3 class).
    """
    out: list[Finding] = []
    for a in ctx.accounts:
        reasons = _i6_account_reasons(a, ctx)
        if not reasons:
            continue
        why = "; ".join(_I6_REASON_TEXT[r] for r in reasons)
        removed = abs(float(a.latest_balance))
        out.append(
            _f(
                detector="I-6",
                subject_key=f"acct:{a.account_id}",
                title=f"Feed problem: {a.name}",
                summary=f"{a.name} looks like a frozen/broken feed — {why}. Its "
                f"${removed:,.2f} balance may be stale; reconnect or confirm the account.",
                dollars_at_stake=removed,
                distortion=True,
                actions_to_fix=1,
                confidence="high",
                evidence={
                    "account_ids": [a.account_id],
                    "figures": {"reasons": reasons, "balance": removed},
                },
            )
        )
    if ctx.refresh_errors or ctx.epoch_zero_types:
        out.append(
            _f(
                detector="I-6",
                subject_key="refresh",
                title="Cache refresh problem",
                summary="One or more data types failed to refresh or carry an "
                "epoch-0 timestamp; their figures may be stale.",
                dollars_at_stake=0.0,
                distortion=True,
                actions_to_fix=1,
                confidence="high",
                evidence={"figures": {
                    "refresh_errors": ctx.refresh_errors,
                    "epoch_zero_types": ctx.epoch_zero_types,
                }},
            )
        )
    return out


def detect_i7_duplicate_txns(ctx: IntegrityContext) -> list[Finding]:
    """Repeated transaction ids in the cache (double-counting)."""
    seen: set[str] = set()
    dupes: list[str] = []
    for t in ctx.transactions:
        if t.id in seen:
            dupes.append(t.id)
        seen.add(t.id)
    if not dupes:
        return []
    return [
        _f(
            detector="I-7",
            subject_key="txn-dupes",
            title=f"{len(dupes)} duplicate transaction id(s) in cache",
            summary="Repeated transaction ids inflate totals; the cache should dedupe.",
            dollars_at_stake=0.0,
            distortion=True,
            actions_to_fix=1,
            confidence="high",
            evidence={"txn_ids": sorted(set(dupes))},
        )
    ]


def detect_i8_new_account_entity(ctx: IntegrityContext) -> list[Finding]:
    """An account id absent from the registry appeared (often a provider re-link)."""
    out: list[Finding] = []
    for a in ctx.accounts:
        if a.account_id and not registry.is_known(a.account_id):
            out.append(
                Finding.new(
                    detector="I-8",
                    subject_key=f"acct:{a.account_id}",
                    layer="integrity",
                    title=f"New account entity: {a.name}",
                    summary=f"Account id {a.account_id} isn't in the registry — "
                    "likely a provider re-link.",
                    dollars_at_stake=abs(float(a.latest_balance)),
                    distortion=False,
                    actions_to_fix=1,
                    confidence="needs_input",
                    question=f"Is '{a.name}' the same account as an existing one, or new?",
                    status="needs_input",
                    evidence={"account_ids": [a.account_id]},
                )
            )
    return out


_DETECTORS = [
    detect_i1_not_live,
    detect_i2_stale_data,
    detect_i3_carry_forward_transition,
    detect_i4_net_worth_mismatch,
    detect_i5_budgets_missing,
    detect_i6_refresh_failure,
    detect_i7_duplicate_txns,
    detect_i8_new_account_entity,
]

# the detector ids this module runs, for the ledger's auto-resolve scoping
INTEGRITY_DETECTOR_IDS = ["I-1", "I-2", "I-3", "I-4", "I-5", "I-6", "I-7", "I-8"]


def run_integrity(ctx: IntegrityContext) -> list[Finding]:
    """Run every integrity detector and concatenate their findings."""
    out: list[Finding] = []
    for det in _DETECTORS:
        out.extend(det(ctx))
    return out
