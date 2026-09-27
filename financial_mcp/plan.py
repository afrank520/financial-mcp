"""plan.yaml — the financial plan the steward reasons over and Claude re-authors.

Mirrors financial_mcp/steward/policy.py: on first load the in-repo seed is written
to ~/.financial_mcp/plan.yaml (outside the code folder, survives updates) so it can
be edited via conversation; an existing file is NEVER overwritten by the seed path.
The plan holds targets/intent only — actuals always come live from Monarch tools.
"""
from __future__ import annotations

import hashlib
import os
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
        _write_locked(path, _seed_text())
    return Plan(yaml.safe_load(path.read_text()) or {})


def _backup_path() -> str:
    return config.resolve_user_path("plan.yaml.bak")


def plan_token(raw: dict) -> str:
    """Stable content hash: sha1 of canonical (sorted-key) YAML."""
    canonical = yaml.safe_dump(raw, sort_keys=True, default_flow_style=False)
    return hashlib.sha1(canonical.encode()).hexdigest()


def _write_locked(path: Path, text: str) -> None:
    """Atomically write `text` to `path` at mode 0600 (owner-only). The plan
    holds real income/balances, so it gets the same permission posture as the
    credential store: temp file opened 0600, chmod'd, then os.replace'd in."""
    tmp = path.with_suffix(".yaml.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)   # atomic
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_plan(raw: dict) -> None:
    """Write plan.yaml atomically (0600), copying the current file to a single
    backup slot first so plan_undo can restore exactly one level."""
    path = Path(_plan_path())
    config.ensure_parent(str(path))
    if path.exists():
        bak = Path(_backup_path())
        _write_locked(bak, path.read_text())
    _write_locked(path, yaml.safe_dump(raw, sort_keys=False, default_flow_style=False))


def has_backup() -> bool:
    return Path(_backup_path()).exists()


def restore_backup() -> bool:
    """Swap the backup back into place. Returns False if there is no backup."""
    bak = Path(_backup_path())
    if not bak.exists():
        return False
    path = Path(_plan_path())
    _write_locked(path, bak.read_text())
    bak.unlink()
    return True
