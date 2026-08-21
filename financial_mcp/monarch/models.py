from __future__ import annotations

import datetime
import hashlib
from decimal import Decimal

from pydantic import BaseModel


class Transaction(BaseModel):
    id: str
    date: datetime.date
    merchant: str
    category: str
    account: str
    original_statement: str
    notes: str
    amount: Decimal
    tags: list[str]
    owner: str

    @staticmethod
    def synth_id(
        date: datetime.date, merchant: str, amount: Decimal, original_statement: str
    ) -> str:
        key = f"{date.isoformat()}|{merchant}|{amount}|{original_statement}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()


class Balance(BaseModel):
    date: datetime.date
    balance: Decimal
    account: str


class Account(BaseModel):
    name: str
    latest_balance: Decimal
    as_of: datetime.date


class BalanceSnapshot(BaseModel):
    account_id: str
    display_name: str
    date: datetime.date
    balance: Decimal
    type_name: str | None = None
    include_in_net_worth: bool = True


class Cashflow(BaseModel):
    start: datetime.date
    end: datetime.date
    income: Decimal
    expense: Decimal
    savings: Decimal
    savings_rate_monarch: Decimal


class NetWorthSnapshot(BaseModel):
    date: datetime.date
    net_worth: Decimal


class Category(BaseModel):
    id: str
    name: str
    icon: str | None = None
    group: str | None = None
    group_id: str | None = None
    group_type: str | None = None   # Monarch's own: "income" | "expense" | "transfer"


class BudgetItem(BaseModel):
    category_id: str
    budgeted: Decimal
    actual: Decimal
    remaining: Decimal


class Holding(BaseModel):
    account: str                          # displayName of the investment account
    ticker: str | None = None             # None for manual/unlisted holdings
    name: str
    quantity: Decimal
    price: Decimal                         # Monarch's currentPrice (fallback: closingPrice)
    value: Decimal                         # Monarch's own totalValue (pass-through)
    cost_basis: Decimal | None = None      # Monarch's basis; None when Monarch has none
    day_change_dollars: Decimal | None = None    # Monarch's securityPriceChangeDollars
    day_change_percent: Decimal | None = None    # Monarch's securityPriceChangePercent


class RecurringItem(BaseModel):
    date: datetime.date                    # this instance's date
    merchant: str
    amount: Decimal                        # this instance's amount (Monarch's own, signed)
    frequency: str | None = None           # stream frequency: "monthly" | "weekly" | ...
    account: str | None = None             # displayName being charged
    category: str | None = None
    is_past: bool                          # Monarch's isPast
