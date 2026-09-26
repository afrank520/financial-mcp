"""plan.yaml — the financial plan the steward reasons over and Claude re-authors.

Mirrors financial_mcp/steward/policy.py: on first load the in-repo seed is written
to ~/.financial_mcp/plan.yaml (outside the code folder, survives updates) so it can
be edited via conversation; an existing file is NEVER overwritten by the seed path.
The plan holds targets/intent only — actuals always come live from Monarch tools.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from financial_mcp import config

_SEED_PATH = Path(__file__).parent / "plan_seed.yaml"


def _seed_text() -> str:
    return _SEED_PATH.read_text()


class Plan:
    """Parsed plan.yaml with typed accessors for the bits the loop reads."""

    def __init__(self, raw: dict) -> None:
        self.raw = raw or {}

    @classmethod
    def from_seed(cls) -> "Plan":
        return cls(yaml.safe_load(_seed_text()) or {})

    @property
    def north_star(self) -> str | None:
        return self.raw.get("north_star")

    @property
    def goals(self) -> list[dict]:
        return list(self.raw.get("goals", []) or [])

    @property
    def trajectory(self) -> list[dict]:
        return list(self.raw.get("trajectory", []) or [])

    def validate(self) -> dict:
        """Report unfilled required fields. Unfilled = None or empty container."""
        missing: list[str] = []
        if not self.north_star:
            missing.append("north_star")
        filled_goal = any(
            g.get("target") not in (None, "", [], {})
            and g.get("target_date") not in (None, "", [], {})
            for g in self.goals
        )
        if not filled_goal:
            missing.append("at least one goal with target + target_date")
        if not self.trajectory:
            missing.append("trajectory (at least one checkpoint)")
        return {"valid": not missing, "missing_required": missing}


def _plan_path() -> str:
    return config.resolve_user_path("plan.yaml")


def load_plan() -> Plan:
    """Load ~/.financial_mcp/plan.yaml, seeding it from the template if absent.

    Malformed YAML raises (never silently returns an empty plan — that would read
    as 'no plan' and mask a corrupted file)."""
    path = Path(_plan_path())
    if not path.exists():
        config.ensure_parent(str(path))
        path.write_text(_seed_text())
    return Plan(yaml.safe_load(path.read_text()) or {})
