from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from typing import TypeVar

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import (
    Account,
    Balance,
    BalanceSnapshot,
    BudgetItem,
    Cashflow,
    Category,
    Holding,
    NetWorthSnapshot,
    RecurringItem,
    Transaction,
)

T = TypeVar("T", Account, Balance, BalanceSnapshot, Transaction, NetWorthSnapshot, Category, Holding)

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

    def balance_history(self) -> list[BalanceSnapshot]:
        return self._get("balance_history", self._adapter.get_balance_history, BalanceSnapshot)

    def networth_history(self) -> list[NetWorthSnapshot]:
        return self._get("networth", self._adapter.get_networth_history, NetWorthSnapshot)

    def categories(self) -> list[Category]:
        return self._get("categories", self._adapter.get_categories, Category)

    def holdings(self) -> list[Holding]:
        return self._get("holdings", self._adapter.get_holdings, Holding)

    def cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow:
        # Never cached — one lightweight window-parameterized live call.
        return self._adapter.get_cashflow(start, end)

    def budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]:
        # Never cached — window-parameterized, mirrors cashflow.
        return self._adapter.get_budgets(start, end)

    def recurring(self, start: datetime.date, end: datetime.date) -> list[RecurringItem]:
        # Never cached — window-parameterized, mirrors cashflow/budgets.
        return self._adapter.get_recurring(start, end)

    def refresh(self, data_type: str | None = None) -> None:
        types = [data_type] if data_type else [
            "transactions", "balances", "accounts", "balance_history", "networth", "categories",
            "holdings",
        ]
        for t in types:
            self._cache.invalidate(t)
        for t in types:
            if t == "transactions":
                self.transactions()
            elif t == "balances":
                self.balances()
            elif t == "accounts":
                self.accounts()
            elif t == "balance_history":
                self.balance_history()
            elif t == "networth":
                self.networth_history()
            elif t == "categories":
                self.categories()
            elif t == "holdings":
                self.holdings()

    def freshness(self, data_type: str) -> dict:
        """Freshness metadata (as_of, age_seconds, fresh) for one data type."""
        return self._cache.freshness(data_type)

    def cache_status(self) -> list[dict]:
        return self._cache.status()
