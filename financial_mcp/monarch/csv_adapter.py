from __future__ import annotations

import csv
import datetime
from decimal import Decimal

from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import (
    Account, Balance, BalanceSnapshot, BudgetItem, Cashflow, Category, Holding, NetWorthSnapshot,
    RecurringItem, Transaction,
)


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s.strip())


def _parse_tags(s: str) -> list[str]:
    return [t.strip() for t in s.split(",") if t.strip()]


class CsvAdapter(MonarchAdapter):
    def __init__(self, transactions_path: str, balances_path: str) -> None:
        self._transactions_path = transactions_path
        self._balances_path = balances_path

    def source_name(self) -> str:
        return "csv"

    def get_transactions(self) -> list[Transaction]:
        out: list[Transaction] = []
        with open(self._transactions_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                date = _parse_date(row["Date"])
                merchant = row["Merchant"].strip()
                amount_str = row["Amount"].strip()
                if not amount_str:
                    raise ValueError(
                        f"Empty Amount cell for Date={row['Date']}, Merchant={row['Merchant'].strip()}"
                    )
                amount = Decimal(amount_str)
                original = row["Original Statement"].strip()
                out.append(
                    Transaction(
                        id=Transaction.synth_id(date, merchant, amount, original),
                        date=date,
                        merchant=merchant,
                        category=row["Category"].strip(),
                        account=row["Account"].strip(),
                        original_statement=original,
                        notes=row["Notes"].strip(),
                        amount=amount,
                        tags=_parse_tags(row["Tags"]),
                        owner=row["Owner"].strip(),
                    )
                )
        return out

    def get_balances(self) -> list[Balance]:
        out: list[Balance] = []
        with open(self._balances_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                balance_str = row["Balance"].strip()
                if not balance_str:
                    raise ValueError(
                        f"Empty Balance cell for Date={row['Date']}, Account={row['Account'].strip()}"
                    )
                out.append(
                    Balance(
                        date=_parse_date(row["Date"]),
                        balance=Decimal(balance_str),
                        account=row["Account"].strip(),
                    )
                )
        return out

    def get_accounts(self) -> list[Account]:
        latest: dict[str, Balance] = {}
        for bal in self.get_balances():
            cur = latest.get(bal.account)
            if cur is None or bal.date > cur.date:
                latest[bal.account] = bal
        return [
            Account(name=name, latest_balance=b.balance, as_of=b.date)
            for name, b in sorted(latest.items())
        ]

    def get_balance_history(self) -> list[BalanceSnapshot]:
        return []

    def get_cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow:
        raise RuntimeError("cashflow is not available in CSV mode")

    def get_networth_history(self) -> list[NetWorthSnapshot]:
        raise RuntimeError("net-worth history is not available in CSV mode")

    def get_categories(self) -> list[Category]:
        raise RuntimeError("categories are not available in CSV mode")

    def get_budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]:
        raise RuntimeError("budgets are not available in CSV mode")

    def get_holdings(self) -> list[Holding]:
        raise RuntimeError("holdings are not available in CSV mode")

    def get_recurring(self, start: datetime.date, end: datetime.date) -> list[RecurringItem]:
        raise RuntimeError("recurring transactions are not available in CSV mode")

    def recategorize_transaction(self, transaction_id: str, category_id: str) -> bool:
        raise RuntimeError("writes are not available in CSV mode")

    def create_category(self, name: str, group_id: str, icon: str) -> str:
        raise RuntimeError("writes are not available in CSV mode")

    def rename_category(self, category_id: str, new_name: str) -> bool:
        raise RuntimeError("writes are not available in CSV mode")

    def delete_category(self, category_id: str) -> bool:
        raise RuntimeError("writes are not available in CSV mode")

    def rename_transaction_merchant(self, transaction_id: str, merchant_name: str) -> str | None:
        raise RuntimeError("writes are not available in CSV mode")

    def get_transaction_rules(self) -> list[dict]:
        return []
