import os

import pytest

from financial_mcp.config import Config
from financial_mcp.main import _startup_message, build_adapter
from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter

_SESSION = {"session_id": "sid-1", "csrftoken": "csrf-1"}


def test_csv_source_builds_csv_adapter():
    cfg = Config(source="csv", csv_transactions_path="t.csv", csv_balances_path="b.csv")
    assert isinstance(build_adapter(cfg), CsvAdapter)


def test_csv_source_without_paths_exits():
    cfg = Config(source="csv")
    with pytest.raises(SystemExit):
        build_adapter(cfg)


def test_live_with_session_builds_community_adapter(monkeypatch):
    class _Store:
        def load_session(self):
            return _SESSION

    monkeypatch.setattr("financial_mcp.main.make_credential_store", lambda cfg: _Store())
    cfg = Config(source="live")
    adapter = build_adapter(cfg)
    assert isinstance(adapter, CommunityAdapter)


def test_live_without_session_falls_back_to_csv(monkeypatch, caplog):
    class _Store:
        def load_session(self):
            return None

    monkeypatch.setattr("financial_mcp.main.make_credential_store", lambda cfg: _Store())
    cfg = Config(source="live", csv_transactions_path="t.csv", csv_balances_path="b.csv")
    import logging

    with caplog.at_level(logging.WARNING, logger="financial_mcp"):
        adapter = build_adapter(cfg)
    assert isinstance(adapter, CsvAdapter)
    assert any("financial-mcp-login" in r.message for r in caplog.records)


def test_startup_message_stamps_pid_so_repeat_lines_are_distinct_processes():
    # D-7: Claude Desktop spawns the stdio server several times during its
    # connect handshake (probe -> intentional shutdown -> re-spawn), so the
    # startup line legitimately appears 2-3x per launch -- one per PROCESS, not
    # an in-process double-init. Stamping the pid makes the repeats read as
    # distinct processes instead of looking like a server bug.
    msg = _startup_message("live", pid=4242)
    assert "pid=4242" in msg
    assert "source=live" in msg


def test_startup_message_defaults_to_current_pid():
    assert f"pid={os.getpid()}" in _startup_message("csv")


def test_live_without_session_and_no_csv_exits(monkeypatch):
    class _Store:
        def load_session(self):
            return None

    monkeypatch.setattr("financial_mcp.main.make_credential_store", lambda cfg: _Store())
    cfg = Config(source="live")
    with pytest.raises(SystemExit):
        build_adapter(cfg)
