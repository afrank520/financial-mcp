"""Account registry — the steward's memory of what each Monarch account is.

Seeded from spec Appendix C. Integrity detectors consult it to distinguish a
benign carry-forward (a dead 401k feed we expect) from an alarming one (a live
529 whose balance just silently left net worth), and to spot a brand-new account
id (I-8, usually a provider re-link).

Registry state strings from the spec are normalized into a set of flags per
account so a compound state like "live; watch_carry_forward" is queryable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryEntry:
    account_id: str
    name: str
    states: frozenset[str]


# States that mean "this account carrying forward is expected — do not alarm".
# A closed account (the 8300 HELOC) is inherently expected to carry forward.
_EXPECTED_CF_STATES = {
    "expected_carry_forward",
    "expected_carry_forward_pending_confirm",
    "closed",
}


def _entry(account_id: str, name: str, *states: str) -> tuple[str, RegistryEntry]:
    return account_id, RegistryEntry(account_id, name, frozenset(states))


# Appendix C seed. Compound spec states are split into flags; the two range/slash
# rows are enumerated by their explicit ids.
REGISTRY: dict[str, RegistryEntry] = dict(
    [
        _entry("183541300701576552", "AgentSync 401(k) Plan (…0768)", "expected_carry_forward"),
        _entry("169483278367997389", "15 Branwood HELOC (8300)", "expected_carry_forward", "closed"),
        _entry("169487170068692772", "Syosset 403b (…665Y71, Finicity)", "expected_carry_forward", "never_exclude"),
        _entry("233056643597614366", "Syosset 403b (…5Y71, MX)", "live"),
        _entry("171945926909115454", "Savings (Apple, old entity)", "expected_carry_forward_pending_confirm"),
        _entry("253413397873179968", "Savings (new entity)", "new"),
        _entry("251845388366920938", "Home Equity Line of Credit (…2800)", "live"),
        _entry("233056032900660408", "American Express Gold Card (…1002)", "live", "amex_float"),
        _entry("206920423728627477", "SoFi Student Loan Refinance (…7625)", "live"),
        _entry("169484667006631843", "Tracey Student Loan", "excluded_duplicate"),
        _entry("237313796790435071", "Braden 529 (01)", "live", "watch_carry_forward"),
        _entry("237313797006441728", "Lexi 529 (02)", "live", "watch_carry_forward"),
        _entry("237313797074599169", "Colton 529 (03)", "live", "watch_carry_forward"),
        _entry("237313797134368002", "Dylan 529 (04)", "live", "watch_carry_forward"),
        _entry("237313797209865475", "Elaina 529 (05)", "live", "watch_carry_forward"),
        # 529 old entities (01–05): spec gives the range endpoints; enumerate those.
        _entry("169485859791191359", "529 old entity (range start)", "excluded_legacy"),
        _entry("169485860105764163", "529 old entity (range end)", "excluded_legacy"),
        _entry("169483620903173697", "Corporate Card (…1002)", "excluded"),
        _entry("233056033218378937", "Corporate Card (…1009)", "excluded"),
    ]
)

# The five live 529s on the carry-forward watch list (spec §2.4 / I-3).
WATCH_529_IDS: frozenset[str] = frozenset(
    aid for aid, e in REGISTRY.items() if "watch_carry_forward" in e.states
)


def registry_state(account_id: str) -> RegistryEntry | None:
    return REGISTRY.get(account_id)


def is_known(account_id: str) -> bool:
    return account_id in REGISTRY


def is_expected_carry_forward(account_id: str) -> bool:
    """True when this account carrying forward is expected (no I-3 alarm)."""
    e = REGISTRY.get(account_id)
    return bool(e and (e.states & _EXPECTED_CF_STATES))


def is_watch_carry_forward(account_id: str) -> bool:
    """True for the five live 529s we actively watch for a carry-forward flip."""
    return account_id in WATCH_529_IDS
