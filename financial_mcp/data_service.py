from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, Transaction

T = TypeVar("T", Account, Balance, Transaction)

logger = logging.getLogger("financial_mcp")


class DataService:
    def __init__(self, adapter: MonarchAdapter, cache: SqliteCache) -> None:
        self._adapter = adapter
        self._cache = cache

    def _get(self, data_type: str, fetch: Callable[[], list[T]], model: type[T]) -> list[T]:
        if self._cache.is_fresh(data_type):
            return [model(**d) for d in self._cache.load(data_type)]
        try:
            items = fetch()
        except Exception:
            cached = self._cache.load(data_type)
            if cached:
                logger.warning("adapter failed for %s; serving stale cache", data_type)
                return [model(**d) for d in cached]
            raise
        self._cache.store(
            data_type,
            [m.model_dump(mode="json") for m in items],
            source=self._adapter.source_name(),
        )
        return items

    def transactions(self) -> list[Transaction]:
        return self._get("transactions", self._adapter.get_transactions, Transaction)

    def balances(self) -> list[Balance]:
        return self._get("balances", self._adapter.get_balances, Balance)

    def accounts(self) -> list[Account]:
        return self._get("accounts", self._adapter.get_accounts, Account)

    def refresh(self, data_type: str | None = None) -> None:
        types = [data_type] if data_type else ["transactions", "balances", "accounts"]
        for t in types:
            self._cache.invalidate(t)
        for t in types:
            if t == "transactions":
                self.transactions()
            elif t == "balances":
                self.balances()
            elif t == "accounts":
                self.accounts()

    def freshness(self, data_type: str) -> dict:
        """Freshness metadata (as_of, age_seconds, fresh) for one data type."""
        return self._cache.freshness(data_type)

    def cache_status(self) -> list[dict]:
        return self._cache.status()
