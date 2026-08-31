import os
from pathlib import Path

import financial_mcp.config as cfgmod
from financial_mcp.config import Config, resolve_user_path


def test_relative_path_anchors_under_user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    assert resolve_user_path("cache.db") == str(tmp_path / ".financial_mcp" / "cache.db")


def test_relative_resolution_ignores_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    monkeypatch.chdir(tmp_path)  # cwd must not affect the result
    c = Config(db_path="cache.db", log_file="financial_mcp.log")
    assert c.db_path == str(tmp_path / ".financial_mcp" / "cache.db")
    assert c.log_file == str(tmp_path / ".financial_mcp" / "financial_mcp.log")


def test_absolute_path_passes_through(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    abs_db = str(tmp_path / "somewhere" / "x.db")
    assert Config(db_path=abs_db).db_path == abs_db


def test_log_file_none_stays_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    assert Config().log_file is None


def test_tilde_expands(monkeypatch):
    assert resolve_user_path("~/x.db") == str(Path("~/x.db").expanduser())
