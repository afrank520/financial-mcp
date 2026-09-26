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
