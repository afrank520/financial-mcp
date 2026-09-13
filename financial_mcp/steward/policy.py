"""policy.yaml — the household rules, thresholds, and goals the steward reasons over.

Seeded from spec Appendix A. On first load the seed is written to
~/.financial_mcp/policy.yaml (one of the two new user files this spec is allowed
to create, alongside findings.db) so Adam can edit it in place; an existing file
is never overwritten.

The validator reports the required-but-unfilled goals — cash_floor, the HELOC
paydown target and apr, and the RSU vest dates — so a detector that depends on
one can emit a single needs_input rather than guessing (spec §6.4).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from financial_mcp import config

_SEED_PATH = Path(__file__).parent / "policy_seed.yaml"

# (dotted path, human hint) for each goal the seed ships unfilled but that
# downstream advice detectors require. "unfilled" = None or empty container.
_REQUIRED_GOALS: list[tuple[tuple[str, ...], str]] = [
    (("cash", "cash_floor"), "cash.cash_floor (required for A-3/A-10)"),
    (("debt", "heloc", "paydown_target"), "debt.heloc.paydown_target (required for A-10)"),
    (("debt", "heloc", "apr"), "debt.heloc.apr (required for HELOC cash-drag math)"),
    (("income", "rsu", "vest_dates"), "income.rsu.vest_dates (required for RSU planning)"),
]


def _seed_text() -> str:
    return _SEED_PATH.read_text()


def _dig(raw: dict, path: tuple[str, ...]):
    cur = raw
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


class Policy:
    """Parsed policy.yaml with typed accessors for the bits detectors need.

    The full document stays available as `.raw`; the accessors below cover the
    fields the Phase 2/3 detectors read so callers don't reach into nested dicts.
    """

    def __init__(self, raw: dict) -> None:
        self.raw = raw or {}

    @classmethod
    def from_seed(cls) -> "Policy":
        return cls(yaml.safe_load(_seed_text()) or {})

    # --- thresholds -------------------------------------------------------
    @property
    def thresholds(self) -> dict:
        return self.raw.get("thresholds", {}) or {}

    @property
    def stale_days(self) -> int:
        return int(self.thresholds.get("stale_days", 3))

    # --- categorization ---------------------------------------------------
    @property
    def _categorization(self) -> dict:
        return self.raw.get("categorization", {}) or {}

    @property
    def normalization_stoplist(self) -> list[str]:
        return list(self._categorization.get("normalization_stoplist", []) or [])

    @property
    def do_not_merge_families(self) -> list[str]:
        return list(self._categorization.get("do_not_merge_merchant_families", []) or [])

    # --- validation -------------------------------------------------------
    def validate(self) -> dict:
        """Report required-but-unfilled goals. Missing = None or empty container."""
        missing: list[str] = []
        for path, hint in _REQUIRED_GOALS:
            value = _dig(self.raw, path)
            if value is None or value == [] or value == {} or value == "":
                missing.append(hint)
        return {"valid": not missing, "missing_required": missing}


def _policy_path() -> str:
    return config.resolve_user_path("policy.yaml")


def load_policy() -> Policy:
    """Load ~/.financial_mcp/policy.yaml, seeding it from Appendix A if absent."""
    path = Path(_policy_path())
    if not path.exists():
        config.ensure_parent(str(path))
        path.write_text(_seed_text())
    return Policy(yaml.safe_load(path.read_text()) or {})
