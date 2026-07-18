import pytest

from financial_mcp.config import Config
from financial_mcp.main import build_adapter
from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter


def test_csv_source_builds_csv_adapter():
    cfg = Config(source="csv", csv_transactions_path="t.csv", csv_balances_path="b.csv")
    assert isinstance(build_adapter(cfg), CsvAdapter)


def test_csv_source_without_paths_exits():
    cfg = Config(source="csv")
    with pytest.raises(SystemExit):
        build_adapter(cfg)


def test_live_with_token_builds_community_adapter(monkeypatch):
    monkeypatch.setattr("financial_mcp.main.auth.load_token", lambda service: "tok-abc")
    cfg = Config(source="live")
    adapter = build_adapter(cfg)
    assert isinstance(adapter, CommunityAdapter)


def test_live_without_token_falls_back_to_csv(monkeypatch, caplog):
    monkeypatch.setattr("financial_mcp.main.auth.load_token", lambda service: None)
    cfg = Config(source="live", csv_transactions_path="t.csv", csv_balances_path="b.csv")
    import logging

    with caplog.at_level(logging.WARNING, logger="financial_mcp"):
        adapter = build_adapter(cfg)
    assert isinstance(adapter, CsvAdapter)
    assert any("financial-mcp-login" in r.message for r in caplog.records)


def test_live_without_token_and_no_csv_exits(monkeypatch):
    monkeypatch.setattr("financial_mcp.main.auth.load_token", lambda service: None)
    cfg = Config(source="live")
    with pytest.raises(SystemExit):
        build_adapter(cfg)
