from __future__ import annotations

import logging
import os

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.cache.undo_journal import UndoJournal
from financial_mcp.config import Config, load_config, resolve_user_path
from financial_mcp.data_service import DataService
from financial_mcp.credentials import make_credential_store
from financial_mcp.mcp_server import build_server
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter
from financial_mcp.steward.ledger import Ledger
from financial_mcp.steward.steward import Steward
from financial_mcp.utils.logging import configure_logging

logger = logging.getLogger("financial_mcp")


def _startup_message(source: str, pid: int | None = None) -> str:
    """The one startup log line, stamped with the process id.

    Claude Desktop spawns the stdio server several times during its connect
    handshake (probe -> intentional shutdown -> re-spawn); each is a fresh,
    short-lived process that reaches main() once. The pid makes those repeats
    read as distinct processes rather than an in-process double-init (D-7).
    """
    return f"financial-mcp starting (pid={os.getpid() if pid is None else pid} source={source})"


def _csv_or_exit(cfg: Config) -> MonarchAdapter:
    if not cfg.csv_transactions_path or not cfg.csv_balances_path:
        raise SystemExit(
            "No usable data source. Set source=live and run `financial-mcp-login`, "
            "or set csv_transactions_path and csv_balances_path in config.yaml."
        )
    return CsvAdapter(cfg.csv_transactions_path, cfg.csv_balances_path)


def build_adapter(cfg: Config) -> MonarchAdapter:
    if cfg.source == "live":
        session = make_credential_store(cfg).load_session()
        if session:
            return CommunityAdapter(session)
        logger.warning("no Monarch session — run `financial-mcp-login`; falling back to CSV")
        return _csv_or_exit(cfg)
    return _csv_or_exit(cfg)


def main() -> None:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    configure_logging(cfg)

    adapter = build_adapter(cfg)
    cache = SqliteCache(cfg.db_path, cfg.cache_ttls)
    svc = DataService(adapter, cache)
    logger.info("%s", _startup_message(adapter.source_name()))

    journal = UndoJournal(cfg.db_path)
    # findings.db lives beside the session/policy under ~/.financial_mcp (spec §5.2)
    steward = Steward(svc, Ledger(resolve_user_path("findings.db")))
    server = build_server(
        svc,
        write_mode=cfg.write_mode,
        batch_cap=cfg.batch_cap,
        journal=journal,
        steward=steward,
    )
    server.run()


if __name__ == "__main__":
    main()
