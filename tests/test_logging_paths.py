import financial_mcp.config as cfgmod
from financial_mcp.config import Config
from financial_mcp.utils.logging import configure_logging


def test_relative_log_file_writes_regardless_of_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    monkeypatch.chdir(tmp_path)
    cfg = Config(log_file="financial_mcp.log")  # resolves under USER_DIR
    logger = configure_logging(cfg)             # must not raise; must create the dir
    logger.info("hello")
    assert (tmp_path / ".financial_mcp" / "financial_mcp.log").exists()
