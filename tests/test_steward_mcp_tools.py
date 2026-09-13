"""Task 5 — the Steward MCP tools are wired into build_server (spec §11).

build_server registers integrity_check / findings_list / findings_update /
policy_get / policy_validate ONLY when a Steward is passed, so the existing
read/write tools are unaffected when it isn't. These cover the wiring + the
end-to-end path through a real DataService (FakeAdapter) and a real ledger.
"""
from __future__ import annotations

from financial_mcp.mcp_server import build_server
from financial_mcp.steward.ledger import Ledger
from financial_mcp.steward.policy import Policy
from financial_mcp.steward.steward import Steward

from tests.test_mcp_tools import _service, _tool

_STEWARD_TOOLS = {
    "integrity_check", "findings_list", "findings_update", "policy_get", "policy_validate",
}


def _steward(tmp_path):
    svc = _service(tmp_path)
    ledger = Ledger(str(tmp_path / "findings.db"))
    return svc, Steward(svc=svc, ledger=ledger, policy_loader=Policy.from_seed)


def test_steward_tools_absent_without_steward(tmp_path):
    server = build_server(_service(tmp_path))
    assert _STEWARD_TOOLS.isdisjoint(server._tool_manager._tools)


def test_steward_tools_registered_with_steward(tmp_path):
    svc, steward = _steward(tmp_path)
    server = build_server(svc, steward=steward)
    assert _STEWARD_TOOLS.issubset(server._tool_manager._tools)


def test_integrity_check_runs_end_to_end(tmp_path):
    svc, steward = _steward(tmp_path)
    server = build_server(svc, steward=steward)
    out = _tool(server, "integrity_check")()
    # FakeAdapter reports source "fake" -> I-1 fires and trust degrades
    assert out["trust"] == "degraded"
    assert any(f["detector"] == "I-1" for f in out["items"])
    assert set(out) >= {"trust", "items", "needs_input", "backlog_count", "methodology"}


def test_findings_update_dismiss_requires_reason(tmp_path):
    svc, steward = _steward(tmp_path)
    server = build_server(svc, steward=steward)
    _tool(server, "integrity_check")()
    fid = _tool(server, "findings_list")()["findings"][0]["id"]

    err = _tool(server, "findings_update")(fid, "dismissed")
    assert "reason" in err["error"]

    ok = _tool(server, "findings_update")(fid, "dismissed", reason="acknowledged")
    assert ok["finding"]["status"] == "dismissed"


def test_findings_update_unknown_id_is_friendly_error(tmp_path):
    svc, steward = _steward(tmp_path)
    server = build_server(svc, steward=steward)
    out = _tool(server, "findings_update")("nope", "open")
    assert "no finding" in out["error"]


def test_policy_get_and_validate(tmp_path):
    svc, steward = _steward(tmp_path)
    server = build_server(svc, steward=steward)
    assert "thresholds" in _tool(server, "policy_get")()["policy"]
    v = _tool(server, "policy_validate")()
    # the seed ships the required goals unfilled
    assert v["valid"] is False and v["missing_required"]
