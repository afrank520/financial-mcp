import asyncio
import datetime
from decimal import Decimal

import pytest

from financial_mcp.monarch.community_adapter import CommunityAdapter, SessionExpiredError


class StubClient:
    """Mimics monarchmoney.MonarchMoney's async read methods."""

    def __init__(self, token):
        self.token = token
        self.fail_status = None  # set to 401/403 to simulate expiry
        self._pages = [
            {
                "allTransactions": {
                    "totalCount": 3,
                    "results": [
                        {
                            "id": "t1",
                            "date": "2026-01-02",
                            "amount": -152.10,
                            "merchant": {"name": "Costco"},
                            "category": {"name": "Groceries"},
                            "account": {"displayName": "Checking"},
                            "notes": "weekly",
                            "plaidName": "COSTCO WHSE #123",
                            "tags": [{"name": "food"}, {"name": "bulk"}],
                        },
                        {
                            "id": "t2",
                            "date": "2026-01-03",
                            "amount": 2500.00,
                            "merchant": {"name": "Paycheck"},
                            "category": {"name": "Income"},
                            "account": {"displayName": "Checking"},
                            "notes": "",
                            "plaidName": "ACME PAYROLL",
                            "tags": [],
                        },
                    ],
                }
            },
            {
                "allTransactions": {
                    "totalCount": 3,
                    "results": [
                        {
                            "id": "t3",
                            "date": "2026-01-05",
                            "amount": -15.99,
                            "merchant": {"name": "Netflix"},
                            "category": {"name": "Entertainment"},
                            "account": {"displayName": "Credit Card"},
                            "notes": "",
                            "plaidName": "NETFLIX.COM",
                            "tags": [],
                        }
                    ],
                }
            },
        ]

    async def get_accounts(self):
        if self.fail_status in (401, 403):
            raise RuntimeError(f"HTTP {self.fail_status} Unauthorized")
        accounts = [
            {"displayName": "Checking", "currentBalance": 3200.00},
            {"displayName": "Credit Card", "currentBalance": -450.00},
        ]
        # Optionally include a null-balance account (simulates manual/investment accounts)
        if hasattr(self, "include_null_balance") and self.include_null_balance:
            accounts.append({"displayName": "Investment", "currentBalance": None})
        return {"accounts": accounts}

    async def get_transactions(self, limit=100, offset=0, **kwargs):
        if self.fail_status in (401, 403):
            raise RuntimeError(f"HTTP {self.fail_status} Unauthorized")
        page = offset // 2  # StubClient serves 2 per page
        return (
            self._pages[page]
            if page < len(self._pages)
            else {"allTransactions": {"totalCount": 3, "results": []}}
        )


def _adapter(**kw):
    return CommunityAdapter(
        "tok",
        client_factory=lambda token: StubClient(token),
        today=lambda: datetime.date(2026, 2, 1),
        page_size=2,
        **kw,
    )


def test_source_name():
    assert _adapter().source_name() == "live"


def test_accounts_mapped():
    accts = {a.name: a for a in _adapter().get_accounts()}
    assert accts["Checking"].latest_balance == Decimal("3200.00")
    assert accts["Checking"].as_of == datetime.date(2026, 2, 1)
    assert accts["Credit Card"].latest_balance == Decimal("-450.00")


def test_balances_derived_from_accounts():
    bals = _adapter().get_balances()
    assert len(bals) == 2
    assert any(b.account == "Credit Card" and b.balance == Decimal("-450.00") for b in bals)
    assert all(b.date == datetime.date(2026, 2, 1) for b in bals)


def test_transactions_paginated_and_mapped():
    txns = _adapter().get_transactions()
    assert len(txns) == 3  # 2 pages stitched
    first = next(t for t in txns if t.merchant == "Costco")
    assert first.amount == Decimal("-152.10")
    assert first.category == "Groceries"
    assert first.account == "Checking"
    assert first.original_statement == "COSTCO WHSE #123"
    assert first.tags == ["food", "bulk"]
    assert first.id == "t1"
    paycheck = next(t for t in txns if t.merchant == "Paycheck")
    assert paycheck.tags == []


def test_expired_session_raises_typed_error():
    a = _adapter()
    # force the stub to report 401
    a._client_factory = lambda token: _fail_client(token, 401)
    with pytest.raises(SessionExpiredError):
        a.get_accounts()


def _fail_client(token, status):
    c = StubClient(token)
    c.fail_status = status
    return c


def test_get_accounts_works_inside_running_event_loop():
    """Regression: asyncio.run() raises RuntimeError when called from a running loop."""
    adapter = _adapter()

    async def call_from_loop():
        # simulates FastMCP invoking a sync tool on its running loop
        return adapter.get_accounts()

    accounts = asyncio.run(call_from_loop())
    assert {a.name for a in accounts} == {"Checking", "Credit Card"}


def test_get_transactions_works_inside_running_event_loop():
    """Regression: asyncio.run() raises RuntimeError when called from a running loop."""
    adapter = _adapter()

    async def call_from_loop():
        # simulates FastMCP invoking a sync tool on its running loop
        return adapter.get_transactions()

    txns = asyncio.run(call_from_loop())
    assert len(txns) == 3
    assert any(t.merchant == "Costco" for t in txns)


def test_null_balance_does_not_crash():
    """Regression: null currentBalance in manual/investment accounts crashes _to_account."""

    def null_balance_client(token):
        c = StubClient(token)
        c.include_null_balance = True
        return c

    adapter = CommunityAdapter(
        "tok",
        client_factory=null_balance_client,
        today=lambda: datetime.date(2026, 2, 1),
    )
    accounts = adapter.get_accounts()
    inv = next(a for a in accounts if a.name == "Investment")
    assert inv.latest_balance == Decimal("0")

    balances = adapter.get_balances()
    inv_bal = next(b for b in balances if b.account == "Investment")
    assert inv_bal.balance == Decimal("0")
