# tests/test_community_adapter.py
import asyncio
import datetime
from decimal import Decimal

import pytest

from financial_mcp.monarch.community_adapter import (
    CloudflareBlockedError,
    CommunityAdapter,
    SessionExpiredError,
)
from financial_mcp.monarch.models import BalanceSnapshot

SESSION = {"session_id": "sid-123", "csrftoken": "csrf-abc"}

_ACCOUNTS_DATA = {
    "accounts": [
        {"displayName": "Checking", "currentBalance": 3200.00},
        {"displayName": "Credit Card", "currentBalance": -450.00},
    ]
}

_TXN_PAGES = [
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


def _stub_transport(accounts_data=None, include_null_balance=False):
    """Return an async transport(operation, query, variables) -> data dict."""
    accounts = dict(accounts_data or _ACCOUNTS_DATA)
    if include_null_balance:
        accounts = {
            "accounts": accounts["accounts"]
            + [{"displayName": "Investment", "currentBalance": None}]
        }

    async def transport(operation, query, variables):
        if operation == "GetAccounts":
            return accounts
        if operation == "GetTransactionsList":
            page = variables["offset"] // 2  # 2 per page
            if page < len(_TXN_PAGES):
                return _TXN_PAGES[page]
            return {"allTransactions": {"totalCount": 3, "results": []}}
        raise AssertionError(f"unexpected operation {operation}")

    return transport


def _raising_transport(exc):
    async def transport(operation, query, variables):
        raise exc

    return transport


def _adapter(transport=None, **kw):
    return CommunityAdapter(
        SESSION,
        transport=transport or _stub_transport(),
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
    a = _adapter(transport=_raising_transport(SessionExpiredError("expired")))
    with pytest.raises(SessionExpiredError):
        a.get_accounts()


def test_cloudflare_block_raises_typed_error():
    a = _adapter(transport=_raising_transport(CloudflareBlockedError("blocked")))
    with pytest.raises(CloudflareBlockedError):
        a.get_accounts()


def test_get_accounts_works_inside_running_event_loop():
    """Regression: asyncio.run() raises RuntimeError when called from a running loop."""
    adapter = _adapter()

    async def call_from_loop():
        return adapter.get_accounts()

    accounts = asyncio.run(call_from_loop())
    assert {a.name for a in accounts} == {"Checking", "Credit Card"}


def test_get_transactions_works_inside_running_event_loop():
    """Regression: asyncio.run() raises RuntimeError when called from a running loop."""
    adapter = _adapter()

    async def call_from_loop():
        return adapter.get_transactions()

    txns = asyncio.run(call_from_loop())
    assert len(txns) == 3
    assert any(t.merchant == "Costco" for t in txns)


def test_null_balance_does_not_crash():
    """Regression: null currentBalance in manual/investment accounts crashes _to_account."""
    adapter = _adapter(transport=_stub_transport(include_null_balance=True))
    inv = next(a for a in adapter.get_accounts() if a.name == "Investment")
    assert inv.latest_balance == Decimal("0")
    inv_bal = next(b for b in adapter.get_balances() if b.account == "Investment")
    assert inv_bal.balance == Decimal("0")


def test_transactions_paginate_when_totalcount_absent():
    """Guard: keep paging while results come back even if totalCount is missing."""

    pages = [
        {"allTransactions": {"results": [dict(_TXN_PAGES[0]["allTransactions"]["results"][0])]}},
        {"allTransactions": {"results": []}},
    ]

    async def transport(operation, query, variables):
        page = variables["offset"] // 1
        return pages[page] if page < len(pages) else {"allTransactions": {"results": []}}

    a = CommunityAdapter(
        SESSION, transport=transport, today=lambda: datetime.date(2026, 2, 1), page_size=1
    )
    assert len(a.get_transactions()) == 1


# Tests for the real _default_transport (Finding #2)


class _MockResponse:
    """Mock aiohttp response that acts as an async context manager."""

    def __init__(self, status, text):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def text(self):
        return self._text


class _MockSession:
    """Mock aiohttp ClientSession that acts as an async context manager."""

    def __init__(self, status, response_text):
        self._status = status
        self._response_text = response_text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def post(self, url, json=None, headers=None):
        return _MockResponse(self._status, self._response_text)


def test_default_transport_200_with_data(monkeypatch):
    """HTTP 200 with data returns the data dict."""
    import aiohttp

    def mock_session(cookies):
        body = '{"data": {"accounts": [{"displayName": "Checking"}]}}'
        return _MockSession(200, body)

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)  # Will use _default_transport
    data = adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))
    assert data == {"accounts": [{"displayName": "Checking"}]}


def test_default_transport_200_with_errors_raises_runtime_error(monkeypatch):
    """HTTP 200 with errors but no data raises RuntimeError."""
    import aiohttp

    def mock_session(cookies):
        body = '{"errors": [{"message": "Something went wrong"}]}'
        return _MockSession(200, body)

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(RuntimeError, match="Monarch GraphQL error"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_200_non_json_raises_runtime_error(monkeypatch):
    """HTTP 200 with non-JSON body raises RuntimeError."""
    import aiohttp

    def mock_session(cookies):
        return _MockSession(200, "<!DOCTYPE html><html>...")

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(RuntimeError, match="Monarch returned non-JSON"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_403_shall_not_pass_raises_cloudflare_error(monkeypatch):
    """HTTP 403 with 'Shall Not Pass' raises CloudflareBlockedError."""
    import aiohttp

    def mock_session(cookies):
        return _MockSession(403, "You Shall Not Pass!")

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(CloudflareBlockedError, match="Cloudflare blocked"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_401_raises_session_expired_error(monkeypatch):
    """HTTP 401 (no Shall Not Pass) raises SessionExpiredError."""
    import aiohttp

    def mock_session(cookies):
        body = '{"detail": "Authentication credentials were not provided."}'
        return _MockSession(401, body)

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(SessionExpiredError, match="session expired"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_403_normal_body_raises_session_expired_error(monkeypatch):
    """HTTP 403 without Cloudflare marker raises SessionExpiredError."""
    import aiohttp

    def mock_session(cookies):
        body = '{"detail": "Token expired"}'
        return _MockSession(403, body)

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(SessionExpiredError, match="session expired"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_500_raises_runtime_error(monkeypatch):
    """HTTP 500 raises RuntimeError."""
    import aiohttp

    def mock_session(cookies):
        return _MockSession(500, "Internal Server Error")

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(RuntimeError, match="Monarch HTTP 500"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_get_balance_history_fans_out_and_maps_dated_snapshots():
    # Two accounts; each snapshotsForAccount call returns dated pairs.
    snaps_by_id = {
        "acct-heloc": [
            {"date": "2026-05-31", "signedBalance": -224600.00, "__typename": "AccountSnapshot"},
            {"date": "2026-06-28", "signedBalance": -202143.55, "__typename": "AccountSnapshot"},
        ],
        "acct-broker": [
            {"date": "2026-06-15", "signedBalance": 50000.00, "__typename": "AccountSnapshot"},
        ],
    }

    async def stub(operation, query, variables):
        if operation == "GetAccountsMin":
            return {"accounts": [
                {"id": "acct-heloc", "displayName": "HELOC"},
                {"id": "acct-broker", "displayName": "Brokerage"},
            ]}
        if operation == "SnapshotsForAccount":
            return {"snapshots": snaps_by_id[variables["id"]]}
        raise AssertionError(f"unexpected operation {operation}")

    adapter = CommunityAdapter(SESSION, transport=stub)
    out = adapter.get_balance_history()
    assert all(isinstance(s, BalanceSnapshot) for s in out)
    heloc = [s for s in out if s.account == "HELOC"]
    assert [s.date for s in heloc] == [datetime.date(2026, 5, 31), datetime.date(2026, 6, 28)]
    assert heloc[-1].balance == Decimal("-202143.55")   # signed, penny-exact from a float
    # sorted by (account, date)
    assert out == sorted(out, key=lambda s: (s.account, s.date))


def test_get_balance_history_skips_accounts_with_no_snapshots():
    async def stub(operation, query, variables):
        if operation == "GetAccountsMin":
            return {"accounts": [{"id": "a1", "displayName": "Closed Card"}]}
        if operation == "SnapshotsForAccount":
            return {"snapshots": []}
        raise AssertionError(operation)

    adapter = CommunityAdapter(SESSION, transport=stub)
    assert adapter.get_balance_history() == []
