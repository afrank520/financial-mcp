from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

HOUR = 3600
DAY = 24 * HOUR


class CacheTTLs(BaseModel):
    transactions: int = 12 * HOUR
    balances: int = 12 * HOUR
    investments: int = 12 * HOUR
    holdings: int = 12 * HOUR
    budgets: int = DAY
    networth: int = DAY
    cashflow: int = DAY
    categories: int = 7 * DAY
    merchants: int = 7 * DAY


class Config(BaseModel):
    db_path: str = "cache.db"
    log_level: str = "INFO"
    log_file: str | None = None
    write_mode: Literal["off", "safe", "full"] = "safe"
    batch_cap: int = 200
    cache_ttls: CacheTTLs = CacheTTLs()
    csv_transactions_path: str | None = None
    csv_balances_path: str | None = None
    source: Literal["csv", "live"] = "csv"
    keyring_service_name: str = "financial_mcp_monarch"


def load_config(path: str | None = None) -> Config:
    raw: dict = {}
    if path:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    try:
        return Config(**raw)
    except ValidationError as exc:
        raise ValueError(f"invalid config: {exc}") from exc
