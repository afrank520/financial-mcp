from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, model_validator

HOUR = 3600
DAY = 24 * HOUR

USER_DIR = Path("~/.financial_mcp").expanduser()


def resolve_user_path(value: str) -> str:
    """Expand ~ and anchor a relative path under USER_DIR. Absolute paths
    (after ~ expansion) are returned unchanged. Uses the module-level USER_DIR
    at call time so tests can monkeypatch it."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = USER_DIR / p
    return str(p)


def ensure_parent(path: str) -> None:
    """Create the parent directory of `path` (0700) if missing. Best-effort."""
    parent = Path(path).expanduser().parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass


class CacheTTLs(BaseModel):
    transactions: int = 12 * HOUR
    balances: int = 12 * HOUR
    investments: int = 12 * HOUR
    holdings: int = 1 * HOUR
    budgets: int = DAY
    networth: int = DAY
    cashflow: int = DAY
    categories: int = 7 * DAY
    merchants: int = 7 * DAY
    balance_history: int = DAY


class Config(BaseModel):
    db_path: str = "cache.db"
    log_level: str = "INFO"
    log_file: str | None = None
    write_mode: Literal["off", "safe", "full"] = "off"
    batch_cap: int = 200
    cache_ttls: CacheTTLs = CacheTTLs()
    csv_transactions_path: str | None = None
    csv_balances_path: str | None = None
    source: Literal["csv", "live"] = "csv"
    keyring_service_name: str = "financial_mcp_monarch"
    credential_store: Literal["file", "keyring"] = "file"
    credential_file: str = "~/.financial_mcp/session.json"

    @model_validator(mode="after")
    def _resolve_paths(self):
        self.db_path = resolve_user_path(self.db_path)
        self.credential_file = resolve_user_path(self.credential_file)
        if self.log_file:
            self.log_file = resolve_user_path(self.log_file)
        return self


def load_config(path: str | None = None) -> Config:
    raw: dict = {}
    if path:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    try:
        return Config(**raw)
    except ValidationError as exc:
        raise ValueError(f"invalid config: {exc}") from exc
