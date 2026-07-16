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
