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

    def _fetchers(self) -> dict[str, tuple[Callable[[], list], type]]:
        """Map each cached data type to its adapter fetch call and model.

        Used by refresh() to pull fresh and re-store per type, bypassing _get's
        stale-fallback so a genuine fetch failure is visible (not swallowed)."""
        return {
            "transactions": (self._adapter.get_transactions, Transaction),
            "balances": (self._adapter.get_balances, Balance),
            "accounts": (self._adapter.get_accounts, Account),
            "balance_history": (self._adapter.get_balance_history, BalanceSnapshot),
            "networth": (self._adapter.get_networth_history, NetWorthSnapshot),
            "categories": (self._adapter.get_categories, Category),
            "holdings": (self._adapter.get_holdings, Holding),
        }

    def refresh(self, data_type: str | None = None) -> dict:
        """Force a fresh pull per type and report an honest per-type outcome.

        Returns {refreshed: [type,...], failed: [{type, error}, ...]}. Fetches
        directly from the adapter and only overwrites the cache on success, so a
        failed type keeps its prior data + timestamp (never an epoch-0 1970
        leak) and is reported in `failed`. Typed auth errors are re-raised so the
        tool layer's _guard converts them to a single friendly re-login message
        rather than one-per-type noise."""
        from financial_mcp.monarch.community_adapter import (
            CloudflareBlockedError,
            SessionExpiredError,
        )

        fetchers = self._fetchers()
        if data_type is not None and data_type not in fetchers:
            raise ValueError(
                f"unknown data_type '{data_type}'; expected one of {', '.join(fetchers)}"
            )
        types = [data_type] if data_type else list(fetchers)
        refreshed: list[str] = []
        failed: list[dict] = []
        for t in types:
            fetch, _model = fetchers[t]
            try:
                items = fetch()
            except (SessionExpiredError, CloudflareBlockedError):
                raise  # global auth failure -> let _guard surface one {error}
            except Exception as exc:
                logger.warning("refresh failed for %s: %s", t, exc)
                failed.append({"type": t, "error": str(exc)})
                continue
            self._cache.store(
                t,
                [m.model_dump(mode="json") for m in items],
                source=self._adapter.source_name(),
            )
            refreshed.append(t)
        return {"refreshed": refreshed, "failed": failed}

    def recategorize(self, transaction_id: str, category_id: str) -> bool:
        applied = self._adapter.recategorize_transaction(transaction_id, category_id)
        # A write makes the cached transactions stale — force a re-pull next read.
        self._cache.invalidate("transactions")
        return applied

    def create_category(self, name: str, group_id: str, icon: str) -> str:
        new_id = self._adapter.create_category(name, group_id, icon)
        self._cache.invalidate("categories")
        return new_id

    def rename_category(self, category_id: str, new_name: str) -> bool:
        applied = self._adapter.rename_category(category_id, new_name)
        # A rename changes the category name that rides on cached transactions.
        self._cache.invalidate("categories")
        self._cache.invalidate("transactions")
        return applied

    def delete_category(self, category_id: str) -> bool:
        applied = self._adapter.delete_category(category_id)
        self._cache.invalidate("categories")
        return applied

    def rename_merchant(self, transaction_id: str, merchant_name: str) -> str | None:
        result = self._adapter.rename_transaction_merchant(transaction_id, merchant_name)
        # The merchant name rides on cached transactions — force a re-pull next read.
        self._cache.invalidate("transactions")
        return result

    def transaction_rules(self) -> list[dict]:
        return self._adapter.get_transaction_rules()

    def create_merchant_rule(self, contains: str, rename_to: str) -> tuple[bool, str | None]:
        return self._adapter.create_transaction_rule(contains, rename_to)

    def create_category_rule(self, contains: str, category_id: str) -> tuple[bool, str | None]:
        return self._adapter.create_category_rule(contains, category_id)

    def delete_transaction_rule(self, rule_id: str) -> bool:
        return self._adapter.delete_transaction_rule(rule_id)

    def update_transaction_rule(self, rule_id: str, operator: str, value: str,
                                set_category_id: str | None, set_merchant: str | None) -> bool:
        return self._adapter.update_transaction_rule(
            rule_id, operator, value, set_category_id, set_merchant)

    def source_name(self) -> str:
        """The effective data source of the adapter actually in use ("live"/"csv").

        Comes from the adapter, NOT Config.source: the 8/28–9/1 outage was
        Config.source=live while build_adapter had silently fallen back to CSV,
        so keying freshness/health on Config would reproduce that dishonesty."""
        return self._adapter.source_name()

    def freshness(self, data_type: str) -> dict:
        """Freshness metadata (as_of, age_seconds, fresh) for one data type."""
        return self._cache.freshness(data_type)

    def cache_status(self) -> list[dict]:
        return self._cache.status()
