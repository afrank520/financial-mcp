from __future__ import annotations

import os

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import load_config
from financial_mcp.data_service import DataService
from financial_mcp.mcp_server import build_server
from financial_mcp.monarch.csv_adapter import CsvAdapter
from financial_mcp.utils.logging import configure_logging


def main() -> None:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    logger = configure_logging(cfg)

    if not cfg.csv_transactions_path or not cfg.csv_balances_path:
        raise SystemExit(
            "Plan 1 runs on CSV exports. Set csv_transactions_path and "
            "csv_balances_path in your config.yaml."
        )

    adapter = CsvAdapter(cfg.csv_transactions_path, cfg.csv_balances_path)
    cache = SqliteCache(cfg.db_path, cfg.cache_ttls)
    svc = DataService(adapter, cache)
    logger.info("financial-mcp starting (source=%s)", adapter.source_name())

    server = build_server(svc)
    server.run()


if __name__ == "__main__":
    main()
