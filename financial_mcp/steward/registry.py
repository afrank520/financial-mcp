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
        # 529 old entities (01–05) from the March platform migration — duplicate
        # display names of the live 529s, all excluded from net worth. Enumerated
        # from the live 2026-09-13 ids so I-8 doesn't nag about the middle three.
        _entry("169485859791191359", "Braden 529 (01, old entity)", "excluded_legacy"),
        _entry("169485859965254976", "Alexis 529 (02, old entity)", "excluded_legacy"),
        _entry("169485860012440897", "Colton 529 (03, old entity)", "excluded_legacy"),
        _entry("169485860059626818", "Dylan 529 (04, old entity)", "excluded_legacy"),
        _entry("169485860105764163", "Elaina 529 (05, old entity)", "excluded_legacy"),
        _entry("169483620903173697", "Corporate Card (…1002)", "excluded"),
        _entry("233056033218378937", "Corporate Card (…1009)", "excluded"),
        # Appendix C backfill — the remaining real accounts, enumerated from the
        # live 2026-09-13 I-8 account_ids so I-8 stops flagging them as "new". All
        # are active accounts marked "live": registering them silences only the
        # new-entity nag, leaving them under I-2 (staleness) and I-3 (carry-forward)
        # watch. (Deliberately NOT expected_carry_forward — that would wrongly
        # exempt a live account from those integrity checks.)
        _entry("169484792332435402", "15 Branwood Drive (real estate)", "live"),
        _entry("169484426295525198", "15 Branwood Mortgage", "live"),
        # SALESFORCE 401(k) (…8797): live feed today (came through I-8, not I-3). If
        # the feed ever freezes it may trip I-3 — watch, don't pre-exempt.
        _entry("169484752533733308", "SALESFORCE 401(K) Plan (…8797)", "live"),
        _entry("169483278280965580", "Adams Checking (…4187)", "live"),
        _entry("169483407858744830", "Alexis Savings (9062)", "live"),
        _entry("169483407818898941", "Braden Savings (8533)", "live"),
        _entry("169483407908027903", "Colton Savings (9437)", "live"),
        _entry("169483407948922368", "Dylan Savings (9697)", "live"),
        _entry("169483407379545595", "Elaina Savings (2971)", "live"),
        _entry("169483407770664444", "High Yield Savings (5040)", "live"),
        _entry("169486554571357755", "Coinbase", "live"),
        _entry("169486005322005863", "Robinhood individual (…2654)", "live"),
        _entry("169512876984122668", "Adam's Venmo", "live"),
        _entry("169484885260386277", "Stock Plan (CRM) (…5064)", "live"),
        _entry("169484885021310948", "Salesforce ESPP (…5064)", "live"),
        _entry("171945927021313088", "Apple Cash", "live"),
        _entry("171945926972030015", "Apple Card", "live"),
        _entry("169483725173571184", "AT&T Universal Platinum Card (…8459)", "live"),
        _entry("169483620975525442", "Optima Platinum Card (…6007)", "live"),
        _entry("169483620439703104", "Blue from American Express (…1001)", "live"),
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
