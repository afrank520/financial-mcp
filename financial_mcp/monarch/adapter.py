from __future__ import annotations

import abc
import datetime

from financial_mcp.monarch.models import (
    Account, Balance, BalanceSnapshot, BudgetItem, Cashflow, Category, Holding, NetWorthSnapshot,
    RecurringItem, Transaction,
)


class MonarchAdapter(abc.ABC):
    """Read contract the rest of the app depends on. Never references a
    concrete Monarch library — implementations are swappable (CSV, community)."""

    @abc.abstractmethod
    def get_transactions(self) -> list[Transaction]: ...

    @abc.abstractmethod
    def get_balances(self) -> list[Balance]: ...

    @abc.abstractmethod
    def get_accounts(self) -> list[Account]: ...

    @abc.abstractmethod
    def get_balance_history(self) -> list[BalanceSnapshot]: ...

    @abc.abstractmethod
    def get_cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow: ...

    @abc.abstractmethod
    def get_networth_history(self) -> list[NetWorthSnapshot]: ...

    @abc.abstractmethod
    def get_categories(self) -> list[Category]: ...

    @abc.abstractmethod
    def get_budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]: ...

    @abc.abstractmethod
    def get_holdings(self) -> list[Holding]: ...

    @abc.abstractmethod
    def get_recurring(self, start: datetime.date, end: datetime.date) -> list[RecurringItem]: ...

    @abc.abstractmethod
    def recategorize_transaction(self, transaction_id: str, category_id: str) -> bool: ...

    @abc.abstractmethod
    def create_category(self, name: str, group_id: str, icon: str) -> str: ...

    @abc.abstractmethod
    def rename_category(self, category_id: str, new_name: str) -> bool: ...

    @abc.abstractmethod
    def source_name(self) -> str: ...
