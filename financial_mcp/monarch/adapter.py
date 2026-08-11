from __future__ import annotations

import abc
import datetime

from financial_mcp.monarch.models import (
    Account, Balance, BalanceSnapshot, BudgetItem, Cashflow, Category, NetWorthSnapshot, Transaction,
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
    def source_name(self) -> str: ...
