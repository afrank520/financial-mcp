"""Task 2 — policy loader/validator (Steward Phase 2, spec §5.1 / Appendix A).

policy.yaml holds the household rules, thresholds, and goals the steward reasons
over. On first run the Appendix A seed is written to ~/.financial_mcp/policy.yaml
(the one new user file this spec creates besides findings.db) so Adam can edit it.
The validator reports the required-but-unfilled goals (cash_floor, HELOC paydown
target/apr, RSU vest dates) so a detector that needs one can emit a single
needs_input instead of guessing.
"""
from __future__ import annotations

from financial_mcp.steward import policy as policy_mod


def test_load_seeds_file_on_first_run_then_reads_it(tmp_path, monkeypatch):
    # point USER_DIR at a temp dir so nothing touches the real ~/.financial_mcp
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    target = tmp_path / "policy.yaml"
    assert not target.exists()

    pol = policy_mod.load_policy()
    assert target.exists()  # seeded
    assert pol.raw["version"] == 1
    assert pol.raw["household"]["state"] == "NY"


def test_load_does_not_overwrite_an_existing_user_file(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_mcp.config.USER_DIR", tmp_path)
    target = tmp_path / "policy.yaml"
    target.write_text("version: 1\nhousehold: {state: CA}\ncustom: kept\n")

    pol = policy_mod.load_policy()
    assert pol.raw["household"]["state"] == "CA"  # user's file, untouched
    assert pol.raw["custom"] == "kept"


def test_thresholds_and_categorization_accessors():
    pol = policy_mod.Policy.from_seed()
    assert pol.stale_days == 3
    assert "New York" in pol.normalization_stoplist
    assert "Citi" in pol.normalization_stoplist
    assert "Miss Sue" in pol.do_not_merge_families
    assert "Capital One" in pol.do_not_merge_families


def test_validate_reports_required_unfilled_goals():
    pol = policy_mod.Policy.from_seed()
    result = pol.validate()
    assert result["valid"] is False
    missing = " ".join(result["missing_required"])
    # the four unfilled required goals in the Appendix A seed
    assert "cash_floor" in missing
    assert "paydown_target" in missing
    assert "apr" in missing
    assert "vest_dates" in missing


def test_validate_passes_once_goals_are_filled():
    pol = policy_mod.Policy.from_seed()
    pol.raw["cash"]["cash_floor"] = 25000
    pol.raw["debt"]["heloc"]["paydown_target"] = {"amount": 250000, "date": "2027-12-31"}
    pol.raw["debt"]["heloc"]["apr"] = 0.089
    pol.raw["income"]["rsu"]["vest_dates"] = ["2026-11-15"]
    result = pol.validate()
    assert result["valid"] is True
    assert result["missing_required"] == []
