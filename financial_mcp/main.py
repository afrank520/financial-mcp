from __future__ import annotations

import logging
import os

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import Config, load_config
from financial_mcp.data_service import DataService
from financial_mcp.mcp_server import build_server
from financial_mcp.monarch import auth
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter
from financial_mcp.utils.logging import configure_logging

logger = logging.getLogger("financial_mcp")


def _csv_or_exit(cfg: Config) -> MonarchAdapter:
    if not cfg.csv_transactions_path or not cfg.csv_balances_path:
        raise SystemExit(
            "No usable data source. Set source=live and run `financial-mcp-login`, "
            "or set csv_transactions_path and csv_balances_path in config.yaml."
        )
    return CsvAdapter(cfg.csv_transactions_path, cfg.csv_balances_path)


def build_adapter(cfg: Config) -> MonarchAdapter:
    if cfg.source == "live":
        token = auth.load_token(cfg.keyring_service_name)
        if token:
            return CommunityAdapter(token, cfg.keyring_service_name)
        logger.warning(
            "no Monarch session — run `financial-mcp-login`; falling back to CSV"
        )
        return _csv_or_exit(cfg)
    return _csv_or_exit(cfg)


def main() -> None:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    configure_logging(cfg)

    adapter = build_adapter(cfg)
    cache = SqliteCache(cfg.db_path, cfg.cache_ttls)
    svc = DataService(adapter, cache)
    logger.info("financial-mcp starting (source=%s)", adapter.source_name())

    server = build_server(svc)
    server.run()


if __name__ == "__main__":
    main()
