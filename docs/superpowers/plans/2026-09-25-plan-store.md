# Plan Store (`plan.yaml`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the financial plan a durable, conversationally-authored home in the MCP so it survives the chat and travels to any client.

**Architecture:** A new `financial_mcp/plan.py` module mirrors the existing `financial_mcp/steward/policy.py` (seed-if-absent, never-clobber, `Plan` class with typed accessors + `validate()`), plus atomic save with a single-slot backup. Two new MCP tools registered unconditionally in `build_server`: `plan_get` (read) and `plan_update` (gated write with a content-hash dry-run→confirm handshake) and `plan_undo` (restore the one backup). The plan holds targets/intent only; actuals always come live from existing Monarch tools.

**Tech Stack:** Python 3.14, FastMCP, PyYAML, pydantic (already in use). Tests: pytest.

**Spec:** `docs/specs/2026-09-25-monarch-mcp-plan-store-design.md`

## Global Constraints

- **Never print to stdout** in server/module code — corrupts MCP stdio. Return values only.
- **Never overwrite** `~/.financial_mcp/plan.yaml` from the seed path; only a confirmed `plan_update` writes it.
- **User files live outside the repo** under `config.USER_DIR` — use `config.resolve_user_path("plan.yaml")` and `config.ensure_parent(...)`.
- **`plan_update` / `plan_get` / `plan_undo` are NOT behind `write_mode`** — they register unconditionally (like core read tools), NOT inside the `if steward is not None:` block, and do not call `_writes_enabled()`.
- **The committed seed carries placeholder numbers only** — never real balances/dates. Shape-accurate worked example.
- **TDD:** failing test first, minimal impl, green, commit. One logical change per commit.
- **Tests point `config.USER_DIR` at `tmp_path`** via `monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)` so nothing touches the real `~/.financial_mcp`.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

- **Create** `financial_mcp/plan_seed.yaml` — the 8-section seed template (placeholder numbers).
- **Create** `financial_mcp/plan.py` — `Plan` class, `load_plan()`, `save_plan()`, `restore_backup()`, `validate()`, content-hash `plan_token()`.
- **Create** `tests/test_plan.py` — module-level tests (seed/no-overwrite/accessors/validate/save/backup/malformed).
- **Modify** `financial_mcp/mcp_server.py` — register `plan_get`, `plan_update`, `plan_undo` unconditionally.
- **Modify** `tests/test_mcp_tools.py` — tool-level tests (get/update dry-run/commit/undo/ungated).

---

## Task 1: The seed template + `Plan` load/accessors/validate

**Files:**
- Create: `financial_mcp/plan_seed.yaml`
- Create: `financial_mcp/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `financial_mcp.config` (`resolve_user_path`, `ensure_parent`, module-level `USER_DIR`).
- Produces:
  - `Plan(raw: dict)` with `.raw`, `.north_star -> str | None`, `.goals -> list[dict]`, `.trajectory -> list[dict]`, `.validate() -> dict`.
  - `Plan.from_seed() -> Plan`
  - `load_plan() -> Plan` (seed-if-absent, else read; malformed YAML raises `yaml.YAMLError`)

- [ ] **Step 1: Write `financial_mcp/plan_seed.yaml`** (placeholder numbers only — no real figures)

```yaml
version: 1
north_star: "Pay down as much HELOC as possible without drawing more."
baseline:
  steady_monthly_income: null      # fill via conversation (plan_update)
  steady_monthly_expense: null
  steady_monthly_margin: null      # income - expense
goals:
  - id: heloc-paydown
    metric: heloc_balance
    tracks: "HELOC account"
    target: null                   # e.g. a dollar balance to reach
    target_date: null              # e.g. 2027-06
  - id: checking-floor
    metric: checking_balance
    tracks: "primary checking"
    target: null                   # the floor to stay above
    target_date: null
guardrails:
  - "Checking below floor = pause and look together."   # policy.yaml holds the live threshold
  - "Every HELOC draw must be named."                   # policy.yaml: debt.heloc.draws_must_be_named is source of truth
  - "Any surprise over the large-expense tripwire is a decision, not autopilot."
levers:
  - name: "W-4 calibration"
    effect: "adjust withholding to change take-home margin"
  - name: "retirement contribution pauses"
    effect: "401k/403b/529 pauses free monthly cashflow"
  - name: "HELOC refi + limit increase"
    effect: "restructures the debt and headroom"
  - name: "credit-card payment cadence"
    effect: "timing sweeps to smooth cashflow"
trajectory:
  # dated checkpoints the metric is expected to hit — the glide-path.
  - date: null                     # e.g. 2026-10
    metric: heloc_balance
    expected: null                 # e.g. expected balance that month
narrative: >
  Transactions are noise; checkpoints are signal. Every dollar off-glide is named.
  (Replace this with the plan's real framing via conversation.)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_plan.py
"""Plan store — plan.yaml loader/validator (see docs/specs/2026-09-25-monarch-mcp-plan-store-design.md).

Mirrors the policy loader: seed-if-absent, never overwrite an existing user file,
typed accessors, and validate() reporting unfilled goals.
"""
from __future__ import annotations

import pytest

from financial_mcp import plan as plan_mod


def test_load_seeds_file_on_first_run_then_reads_it(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    target = tmp_path / "plan.yaml"
    assert not target.exists()

    p = plan_mod.load_plan()
    assert target.exists()  # seeded
    assert p.raw["version"] == 1
    assert p.north_star.startswith("Pay down")


def test_load_does_not_overwrite_an_existing_user_file(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    target = tmp_path / "plan.yaml"
    target.write_text("version: 1\nnorth_star: mine\ncustom: kept\n")

    p = plan_mod.load_plan()
    assert p.north_star == "mine"       # user's file, untouched
    assert p.raw["custom"] == "kept"


def test_accessors_read_seeded_values():
    p = plan_mod.Plan.from_seed()
    assert isinstance(p.goals, list) and p.goals
    assert isinstance(p.trajectory, list)
    assert p.goals[0]["id"] == "heloc-paydown"


def test_validate_reports_unfilled_goals():
    p = plan_mod.Plan.from_seed()   # seed ships targets as null
    v = p.validate()
    assert v["valid"] is False
    assert any("target" in m for m in v["missing_required"])


def test_validate_clean_when_filled():
    p = plan_mod.Plan({
        "north_star": "x",
        "goals": [{"id": "g", "metric": "m", "target": 1, "target_date": "2027-06"}],
        "trajectory": [{"date": "2026-10", "metric": "m", "expected": 1}],
    })
    assert p.validate()["valid"] is True


def test_malformed_yaml_raises_not_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    (tmp_path / "plan.yaml").write_text("version: 1\n  bad: : :\n")
    with pytest.raises(Exception):
        plan_mod.load_plan()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_plan.py -q`
Expected: FAIL — `ModuleNotFoundError: financial_mcp.plan` / attributes undefined.

- [ ] **Step 4: Write `financial_mcp/plan.py`** (load/accessors/validate only; save comes in Task 2)

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_plan.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/plan_seed.yaml financial_mcp/plan.py tests/test_plan.py
git commit -m "feat(plan): seed template + Plan loader/accessors/validate

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Atomic save + single-slot backup + content-hash token

**Files:**
- Modify: `financial_mcp/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `Plan`, `load_plan`, `_plan_path` from Task 1.
- Produces:
  - `plan_token(raw: dict) -> str` — sha1 of canonical (sorted-key) YAML; stable for equal dicts.
  - `save_plan(raw: dict) -> None` — atomic write (temp + `os.replace`); copies the current file to a single `.plan.bak` slot first.
  - `restore_backup() -> bool` — swap `.plan.bak` back to `plan.yaml`; returns False if no backup.
  - `has_backup() -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_plan.py

def test_plan_token_stable_and_order_independent():
    a = plan_mod.plan_token({"north_star": "x", "goals": []})
    b = plan_mod.plan_token({"goals": [], "north_star": "x"})   # key order differs
    assert a == b and len(a) == 40
    assert a != plan_mod.plan_token({"north_star": "y", "goals": []})


def test_save_plan_writes_atomically_and_reads_back(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    plan_mod.save_plan({"version": 1, "north_star": "saved"})
    assert plan_mod.load_plan().north_star == "saved"


def test_save_plan_keeps_one_prior_version_and_restores(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    plan_mod.save_plan({"version": 1, "north_star": "first"})
    plan_mod.save_plan({"version": 1, "north_star": "second"})
    assert plan_mod.load_plan().north_star == "second"
    assert plan_mod.has_backup() is True

    assert plan_mod.restore_backup() is True
    assert plan_mod.load_plan().north_star == "first"   # rolled back one level


def test_restore_backup_false_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    plan_mod.save_plan({"version": 1, "north_star": "only"})
    assert plan_mod.restore_backup() is False   # nothing backed up yet
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_plan.py -q`
Expected: FAIL — `plan_token` / `save_plan` / `restore_backup` / `has_backup` undefined.

- [ ] **Step 3: Add save/backup/token to `financial_mcp/plan.py`**

```python
# add imports at top
import hashlib
import os

# add after load_plan()

def _backup_path() -> str:
    return config.resolve_user_path("plan.yaml.bak")


def plan_token(raw: dict) -> str:
    """Stable content hash: sha1 of canonical (sorted-key) YAML."""
    canonical = yaml.safe_dump(raw, sort_keys=True, default_flow_style=False)
    return hashlib.sha1(canonical.encode()).hexdigest()


def save_plan(raw: dict) -> None:
    """Write plan.yaml atomically, copying the current file to a single backup
    slot first so plan_undo can restore exactly one level."""
    path = Path(_plan_path())
    config.ensure_parent(str(path))
    if path.exists():
        Path(_backup_path()).write_text(path.read_text())
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False, default_flow_style=False))
    os.replace(tmp, path)   # atomic


def has_backup() -> bool:
    return Path(_backup_path()).exists()


def restore_backup() -> bool:
    """Swap the backup back into place. Returns False if there is no backup."""
    bak = Path(_backup_path())
    if not bak.exists():
        return False
    path = Path(_plan_path())
    path.write_text(bak.read_text())
    bak.unlink()
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_plan.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/plan.py tests/test_plan.py
git commit -m "feat(plan): atomic save + single-slot backup + content-hash token

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `plan_get` tool (read, ungated)

**Files:**
- Modify: `financial_mcp/mcp_server.py` (register near the other unconditional read tools, NOT inside `if steward is not None:`)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `financial_mcp.plan.load_plan`; server helper `_tool(server, name)` and `_service(tmp_path)` from the test module.
- Produces: MCP tool `plan_get() -> dict` returning `{"plan": <raw>, "validation": <validate()>}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_mcp_tools.py

def test_plan_get_returns_plan_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path))   # no steward, write_mode off
    out = _tool(server, "plan_get")()
    assert "plan" in out and "validation" in out
    assert out["plan"]["north_star"].startswith("Pay down")
    assert out["validation"]["valid"] is False   # seed goals unfilled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py::test_plan_get_returns_plan_and_validation -q`
Expected: FAIL — `KeyError: 'plan_get'` (tool not registered).

- [ ] **Step 3: Register `plan_get` in `build_server`**

Add `from financial_mcp import plan as plan_mod` to the imports at the top of `financial_mcp/mcp_server.py`. Then, alongside the other unconditionally-registered tools (BEFORE the `if steward is not None:` block near line 1278), add:

```python
    @server.tool()
    def plan_get() -> dict:
        """Return the full financial plan (north_star, baseline, goals, guardrails,
        levers, trajectory, watchlist, narrative) plus which goals are still unfilled.
        Read this at the START of any planning or steward conversation to re-ground
        in the plan before reasoning against live data."""
        return _guard(lambda: {
            "plan": plan_mod.load_plan().raw,
            "validation": plan_mod.load_plan().validate(),
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py::test_plan_get_returns_plan_and_validation -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat(plan): plan_get read tool (ungated, always registered)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `plan_update` tool (gated write, dry-run→confirm)

**Files:**
- Modify: `financial_mcp/mcp_server.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `plan_mod.load_plan`, `plan_mod.save_plan`, `plan_mod.plan_token` (Task 2); `_guard`.
- Produces: MCP tool `plan_update(plan: dict, confirm_token: str | None = None) -> dict`.
  - dry-run (no/again wrong token): `{"dry_run": True, "plan": <normalized>, "confirm_token": <hash>, "note": "nothing written yet"}`
  - commit (token matches): `{"saved": True, "undo_hint": "call plan_undo to restore the previous plan"}`
  - invalid shape: `{"error": <msg>}` (nothing written)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_mcp_tools.py

_KNOWN_PLAN_KEYS = {
    "version", "north_star", "baseline", "goals", "guardrails",
    "levers", "trajectory", "watchlist", "narrative",
}


def test_plan_update_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path))
    doc = {"version": 1, "north_star": "new plan", "goals": []}
    out = _tool(server, "plan_update")(plan=doc)
    assert out["dry_run"] is True and "confirm_token" in out
    # file not created by a dry run
    assert not (tmp_path / "plan.yaml").exists()


def test_plan_update_commits_with_matching_token(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path))
    doc = {"version": 1, "north_star": "committed", "goals": []}
    token = _tool(server, "plan_update")(plan=doc)["confirm_token"]
    out = _tool(server, "plan_update")(plan=doc, confirm_token=token)
    assert out["saved"] is True
    assert _tool(server, "plan_get")()["plan"]["north_star"] == "committed"


def test_plan_update_wrong_token_stays_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path))
    doc = {"version": 1, "north_star": "x", "goals": []}
    out = _tool(server, "plan_update")(plan=doc, confirm_token="bogus")
    assert out["dry_run"] is True
    assert not (tmp_path / "plan.yaml").exists()


def test_plan_update_rejects_unknown_key(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path))
    out = _tool(server, "plan_update")(plan={"north_star": "x", "typoo": 1})
    assert "error" in out
    assert not (tmp_path / "plan.yaml").exists()


def test_plan_update_rejects_non_dict(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path))
    out = _tool(server, "plan_update")(plan=["not", "a", "dict"])
    assert "error" in out


def test_plan_update_works_with_write_mode_off(tmp_path, monkeypatch):
    # proves plan_update is NOT gated by write_mode (default is "off")
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path), write_mode="off")
    doc = {"version": 1, "north_star": "ungated", "goals": []}
    token = _tool(server, "plan_update")(plan=doc)["confirm_token"]
    assert _tool(server, "plan_update")(plan=doc, confirm_token=token)["saved"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k plan_update -q`
Expected: FAIL — `KeyError: 'plan_update'`.

- [ ] **Step 3: Register `plan_update`** (immediately after `plan_get`, still outside the steward block)

```python
    _PLAN_KEYS = {
        "version", "north_star", "baseline", "goals", "guardrails",
        "levers", "trajectory", "watchlist", "narrative",
    }

    @server.tool()
    def plan_update(plan: dict, confirm_token: str | None = None) -> dict:
        """Create or replace the financial plan. Full-replace: send the ENTIRE plan
        document (after plan_get you already hold the current one, so editing =
        re-send the whole doc with your change). Two-phase: call without confirm_token
        to preview (writes NOTHING, returns the proposed plan + a confirm_token); call
        again with the matching token to write. Reversible via plan_undo (restores the
        previous version). Not gated by write_mode — the plan is your own local intent,
        not a change to your Monarch records."""

        def _run():
            if not isinstance(plan, dict):
                return {"error": "plan must be a mapping of the plan sections"}
            unknown = set(plan) - _PLAN_KEYS
            if unknown:
                return {"error": f"unknown plan section(s): {sorted(unknown)}. "
                        f"Allowed: {sorted(_PLAN_KEYS)}"}
            token = plan_mod.plan_token(plan)
            if confirm_token is None or confirm_token != token:
                return {
                    "dry_run": True,
                    "plan": plan,
                    "confirm_token": token,
                    "note": "nothing written yet — call again with confirm_token to save",
                }
            plan_mod.save_plan(plan)
            return {"saved": True,
                    "undo_hint": "call plan_undo to restore the previous plan"}

        return _guard(_run)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k plan_update -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat(plan): plan_update gated write (dry-run->confirm, ungated by write_mode)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `plan_undo` tool (restore the one backup)

**Files:**
- Modify: `financial_mcp/mcp_server.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `plan_mod.restore_backup`, `plan_mod.has_backup` (Task 2); `plan_update`/`plan_get` tools (Tasks 3–4).
- Produces: MCP tool `plan_undo() -> dict` — `{"restored": True}` or `{"restored": False, "message": "nothing to undo"}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_mcp_tools.py

def test_plan_undo_restores_previous_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path))
    up = _tool(server, "plan_update")
    for text in ("first", "second"):
        doc = {"version": 1, "north_star": text, "goals": []}
        up(plan=doc, confirm_token=up(plan=doc)["confirm_token"])
    assert _tool(server, "plan_get")()["plan"]["north_star"] == "second"

    out = _tool(server, "plan_undo")()
    assert out["restored"] is True
    assert _tool(server, "plan_get")()["plan"]["north_star"] == "first"


def test_plan_undo_nothing_to_undo(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    server = build_server(_service(tmp_path))
    doc = {"version": 1, "north_star": "only", "goals": []}
    up = _tool(server, "plan_update")
    up(plan=doc, confirm_token=up(plan=doc)["confirm_token"])   # first save, no backup yet
    out = _tool(server, "plan_undo")()
    assert out["restored"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k plan_undo -q`
Expected: FAIL — `KeyError: 'plan_undo'`.

- [ ] **Step 3: Register `plan_undo`** (after `plan_update`)

```python
    @server.tool()
    def plan_undo() -> dict:
        """Restore the previous version of the plan (one level of undo). Reverses the
        most recent plan_update. Returns restored: False if there is no prior version."""

        def _run():
            if plan_mod.restore_backup():
                return {"restored": True,
                        "message": "restored the previous plan"}
            return {"restored": False, "message": "nothing to undo"}

        return _guard(_run)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k plan_undo -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat(plan): plan_undo restores the previous plan (one level)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Full-suite regression + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:** none (documentation + verification).

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — prior 555 + new (~18) tests, 0 failures.

- [ ] **Step 2: Add a CHANGELOG entry under `[Unreleased]`**

Add under the existing `[Unreleased]` section:

```markdown
### Added
- **Plan store (`plan.yaml`).** The financial plan now has a durable home the
  MCP persists on your Mac and reads back every session, so it survives the chat
  and travels to any client. `plan_get` returns the plan (north_star, baseline,
  goals, guardrails, levers, trajectory, watchlist, narrative) plus which goals
  are still unfilled; `plan_update` creates or replaces it entirely through
  conversation (dry-run → confirm, one-level undo via `plan_undo`) — you never
  edit a file by hand. Independent of `write_mode`: the plan is your own local
  intent, not a change to your Monarch records.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(plan): changelog entry for the plan store

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- §3 lifecycle (create/load/edit in conversation) → Tasks 3 (get), 4 (update), 5 (undo). ✅
- §4 architecture (seed-if-absent, never-clobber, outside code folder, intent-only) → Task 1. ✅
- §5 `Plan` module (accessors, validate) → Task 1. ✅
- §6 8-section schema seeded w/ placeholder numbers → Task 1 seed. ✅ (§6.1 overlap comments present in seed.)
- §7.1 `plan_get` → Task 3. ✅ §7.2 `plan_update` full-replace + token → Task 4. ✅ §7.3 undo via dedicated single-slot backup (chosen over journal because `undo_last` is Monarch-coupled and refuses in write_mode off) → Tasks 2 + 5. ✅
- §8 not behind write_mode, registered unconditionally → Tasks 3–5 (test `test_plan_update_works_with_write_mode_off`). ✅
- §9 error posture (malformed raises, atomic write) → Tasks 1–2. ✅
- §10 testing mirror → all tasks. ✅
- §11 boundaries (no adherence, no lens, no scheduler) → nothing in plan exceeds scope. ✅
- §12 open items resolved: (1) validate() set defined in Task 1; (2) undo = single-slot backup (Tasks 2/5); (3) dry-run returns full proposed plan (section-level diff deferred — not built, no placeholder left dangling). ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"write tests for the above" — every test and impl block is concrete. ✅

**3. Type consistency:** `plan_token`/`save_plan`/`restore_backup`/`has_backup` defined in Task 2 and consumed with matching names/signatures in Tasks 4–5. `load_plan`/`Plan.validate`/`.raw` from Task 1 used consistently. Tool names `plan_get`/`plan_update`/`plan_undo` consistent across tasks and CHANGELOG. ✅
