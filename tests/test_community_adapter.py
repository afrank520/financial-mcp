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
from financial_mcp.monarch.models import BalanceSnapshot, BudgetItem, Cashflow, Category, NetWorthSnapshot

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


def test_accounts_prefer_display_balance_and_expose_trust_fields():
    # A funded manual/asset account whose currentBalance is null but displayBalance
    # is real — reading currentBalance alone reported it as $0 (the D-1 bug).
    data = {
        "accounts": [
            {
                "id": "acc-1",
                "displayName": "Coinbase",
                "currentBalance": None,
                "displayBalance": 4510.98,
                "includeInNetWorth": True,
            },
            {
                "id": "acc-2",
                "displayName": "Dead Corp Card",
                "currentBalance": -579.80,
                "displayBalance": -579.80,
                "includeInNetWorth": False,
            },
        ]
    }
    accts = {a.name: a for a in _adapter(transport=_stub_transport(data)).get_accounts()}
    assert accts["Coinbase"].latest_balance == Decimal("4510.98")  # not $0
    assert accts["Coinbase"].account_id == "acc-1"
    assert accts["Coinbase"].include_in_net_worth is True
    assert accts["Dead Corp Card"].account_id == "acc-2"
    assert accts["Dead Corp Card"].include_in_net_worth is False


def test_accounts_map_is_hidden_flag():
    # Monarch's isHidden is fetched by GET_ACCOUNTS but was previously dropped.
    # I-9 needs it to flag a hidden account that still counts in net worth.
    data = {
        "accounts": [
            {"id": "h1", "displayName": "Hidden but counted",
             "displayBalance": 100.0, "includeInNetWorth": True, "isHidden": True},
            {"id": "v1", "displayName": "Visible",
             "displayBalance": 200.0, "includeInNetWorth": True, "isHidden": False},
            {"id": "d1", "displayName": "No flag present",
             "displayBalance": 300.0, "includeInNetWorth": True},
        ]
    }
    accts = {a.name: a for a in _adapter(transport=_stub_transport(data)).get_accounts()}
    assert accts["Hidden but counted"].is_hidden is True
    assert accts["Visible"].is_hidden is False
    assert accts["No flag present"].is_hidden is False  # missing ⇒ default


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


def _expire_once_then_ok(ok_data=None):
    """A transport that raises SessionExpiredError on its FIRST call, then
    behaves like the normal stub. Models a mid-call expiry that a reload fixes."""
    stub = _stub_transport(ok_data)
    state = {"calls": 0}

    async def transport(operation, query, variables):
        state["calls"] += 1
        if state["calls"] == 1:
            raise SessionExpiredError("expired")
        return await stub(operation, query, variables)

    return transport, state


def test_self_heal_reloads_session_and_retries_once():
    """On SessionExpiredError, the adapter reloads a fresh session and retries
    the call once — the read succeeds and the new session is adopted."""
    transport, state = _expire_once_then_ok()
    fresh = {"session_id": "sid-NEW", "csrftoken": "csrf-NEW"}
    a = _adapter(transport=transport, reload_session=lambda: fresh)

    accts = a.get_accounts()

    assert {x.name for x in accts} == {"Checking", "Credit Card"}
    assert state["calls"] == 2  # failed once, retried once
    assert a._session == fresh  # adopted the reloaded session


def test_self_heal_raises_when_reloaded_session_is_unchanged():
    """If the reload returns the SAME (still-dead) session, don't retry — fail
    fast so a truly-expired session surfaces the re-login prompt (no grind)."""
    transport, state = _expire_once_then_ok()
    a = _adapter(transport=transport, reload_session=lambda: dict(SESSION))

    with pytest.raises(SessionExpiredError):
        a.get_accounts()
    assert state["calls"] == 1  # never retried — same session is pointless


def test_self_heal_raises_when_no_session_on_disk():
    """If the reload finds no session at all, raise (nothing to retry with)."""
    transport, state = _expire_once_then_ok()
    a = _adapter(transport=transport, reload_session=lambda: None)

    with pytest.raises(SessionExpiredError):
        a.get_accounts()
    assert state["calls"] == 1


def test_no_reload_callback_preserves_old_raise_behavior():
    """Back-compat: an adapter built without reload_session (tests, reauth path)
    raises on expiry exactly as before — no reload attempted."""
    a = _adapter(transport=_raising_transport(SessionExpiredError("expired")))
    with pytest.raises(SessionExpiredError):
        a.get_accounts()


def test_self_heal_retries_only_once_when_fresh_session_also_dead():
    """A fresh-but-also-dead session must not loop: retry exactly once, then
    raise. Guards against an infinite reload/retry cycle."""
    state = {"calls": 0}

    async def always_expired(operation, query, variables):
        state["calls"] += 1
        raise SessionExpiredError("expired")

    fresh = {"session_id": "sid-NEW", "csrftoken": "csrf-NEW"}
    a = _adapter(transport=always_expired, reload_session=lambda: fresh)

    with pytest.raises(SessionExpiredError):
        a.get_accounts()
    assert state["calls"] == 2  # original + one retry, no more


def test_self_heal_retries_with_already_healed_session_from_concurrent_call():
    """If another caller healed self._session while this call was in flight, the
    expiry handler retries with the now-current session instead of reloading
    again or raising — avoids a spurious failure in the concurrency window."""
    healed = {"session_id": "sid-HEALED", "csrftoken": "csrf-HEALED"}
    reloads = {"count": 0}
    stub = _stub_transport()
    state = {"calls": 0}

    def reload():
        reloads["count"] += 1
        return dict(SESSION)  # the stale session — proves we DON'T fall back to this

    a = _adapter(reload_session=reload)

    async def transport(operation, query, variables):
        state["calls"] += 1
        if state["calls"] == 1:
            # Simulate a concurrent caller having swapped in a fresh session
            # just before this call's expiry is handled.
            a._session = healed
            raise SessionExpiredError("expired")
        return await stub(operation, query, variables)

    a._raw_transport = transport

    accts = a.get_accounts()

    assert {x.name for x in accts} == {"Checking", "Credit Card"}
    assert state["calls"] == 2          # retried
    assert reloads["count"] == 0        # used the already-healed session, no reload
    assert a._session == healed


def test_self_heal_does_not_retry_cloudflare_block():
    """Cloudflare blocks aren't a session problem — reloading won't help, so
    they raise immediately without a reload/retry."""
    reloads = {"count": 0}

    def reload():
        reloads["count"] += 1
        return {"session_id": "sid-NEW", "csrftoken": "csrf-NEW"}

    a = _adapter(
        transport=_raising_transport(CloudflareBlockedError("blocked")),
        reload_session=reload,
    )
    with pytest.raises(CloudflareBlockedError):
        a.get_accounts()
    assert reloads["count"] == 0  # never tried to reload


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


def test_transactions_dedupe_overlapping_pages_by_id():
    """A shifting page boundary that repeats an id must not double-count a txn."""
    t1 = dict(_TXN_PAGES[0]["allTransactions"]["results"][0])  # id "t1"
    t2 = dict(_TXN_PAGES[0]["allTransactions"]["results"][1])  # id "t2"
    # Page 2 overlaps: it repeats t2 (as if a new txn shifted the window) plus t3.
    t3 = dict(_TXN_PAGES[1]["allTransactions"]["results"][0])  # id "t3"
    pages = [
        {"allTransactions": {"results": [t1, t2]}},
        {"allTransactions": {"results": [t2, t3]}},  # t2 repeated across the boundary
        {"allTransactions": {"results": []}},
    ]

    async def transport(operation, query, variables):
        page = variables["offset"] // 2
        return pages[page] if page < len(pages) else {"allTransactions": {"results": []}}

    a = CommunityAdapter(
        SESSION, transport=transport, today=lambda: datetime.date(2026, 2, 1), page_size=2
    )
    txns = a.get_transactions()
    ids = [t.id for t in txns]
    assert ids == ["t1", "t2", "t3"]        # t2 appears exactly once
    assert len(ids) == len(set(ids))         # no duplicate ids at all


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

    def mock_session(cookies, connector=None):
        body = '{"data": {"accounts": [{"displayName": "Checking"}]}}'
        return _MockSession(200, body)

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)  # Will use _default_transport
    data = adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))
    assert data == {"accounts": [{"displayName": "Checking"}]}


def test_default_transport_200_with_errors_raises_runtime_error(monkeypatch):
    """HTTP 200 with errors but no data raises RuntimeError."""
    import aiohttp

    def mock_session(cookies, connector=None):
        body = '{"errors": [{"message": "Something went wrong"}]}'
        return _MockSession(200, body)

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(RuntimeError, match="Monarch GraphQL error"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_200_non_json_raises_runtime_error(monkeypatch):
    """HTTP 200 with non-JSON body raises RuntimeError."""
    import aiohttp

    def mock_session(cookies, connector=None):
        return _MockSession(200, "<!DOCTYPE html><html>...")

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(RuntimeError, match="Monarch returned non-JSON"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_403_shall_not_pass_raises_cloudflare_error(monkeypatch):
    """HTTP 403 with 'Shall Not Pass' raises CloudflareBlockedError."""
    import aiohttp

    def mock_session(cookies, connector=None):
        return _MockSession(403, "You Shall Not Pass!")

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(CloudflareBlockedError, match="Cloudflare blocked"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_401_raises_session_expired_error(monkeypatch):
    """HTTP 401 (no Shall Not Pass) raises SessionExpiredError."""
    import aiohttp

    def mock_session(cookies, connector=None):
        body = '{"detail": "Authentication credentials were not provided."}'
        return _MockSession(401, body)

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(SessionExpiredError, match="session expired"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_403_normal_body_raises_session_expired_error(monkeypatch):
    """HTTP 403 without Cloudflare marker raises SessionExpiredError."""
    import aiohttp

    def mock_session(cookies, connector=None):
        body = '{"detail": "Token expired"}'
        return _MockSession(403, body)

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(SessionExpiredError, match="session expired"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_default_transport_500_raises_runtime_error(monkeypatch):
    """HTTP 500 raises RuntimeError."""
    import aiohttp

    def mock_session(cookies, connector=None):
        return _MockSession(500, "Internal Server Error")

    monkeypatch.setattr(aiohttp, "ClientSession", mock_session)

    adapter = CommunityAdapter(SESSION, transport=None)
    with pytest.raises(RuntimeError, match="Monarch HTTP 500"):
        adapter._run(adapter._default_transport("GetAccounts", "query {}", {}))


def test_get_balance_history_fans_out_and_maps_classified_snapshots():
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
                {"id": "acct-heloc", "displayName": "HELOC",
                 "type": {"name": "loan"}, "includeInNetWorth": True},
                {"id": "acct-broker", "displayName": "Brokerage",
                 "type": {"name": "brokerage"}, "includeInNetWorth": True},
            ]}
        if operation == "SnapshotsForAccount":
            return {"snapshots": snaps_by_id[variables["id"]]}
        raise AssertionError(f"unexpected operation {operation}")

    adapter = CommunityAdapter(SESSION, transport=stub)
    out = adapter.get_balance_history()
    assert all(isinstance(s, BalanceSnapshot) for s in out)
    heloc = [s for s in out if s.account_id == "acct-heloc"]
    assert [s.date for s in heloc] == [datetime.date(2026, 5, 31), datetime.date(2026, 6, 28)]
    assert heloc[-1].balance == Decimal("-202143.55")
    assert heloc[-1].display_name == "HELOC"
    assert heloc[-1].type_name == "loan"
    assert heloc[-1].include_in_net_worth is True
    assert out == sorted(out, key=lambda s: (s.display_name, s.account_id, s.date))


def test_get_balance_history_classifies_excluded_and_untyped_accounts():
    async def stub(operation, query, variables):
        if operation == "GetAccountsMin":
            return {"accounts": [
                {"id": "old529", "displayName": "Braden 529 (01)",
                 "type": {"name": "brokerage"}, "includeInNetWorth": False},
                {"id": "notype", "displayName": "Odd", "type": None, "includeInNetWorth": True},
            ]}
        if operation == "SnapshotsForAccount":
            return {"snapshots": [{"date": "2026-06-01", "signedBalance": 10.0}]}
        raise AssertionError(operation)

    adapter = CommunityAdapter(SESSION, transport=stub)
    out = {s.account_id: s for s in adapter.get_balance_history()}
    assert out["old529"].include_in_net_worth is False
    assert out["old529"].type_name == "brokerage"
    assert out["notype"].type_name is None       # missing type block -> None, no crash


def test_get_balance_history_skips_accounts_with_no_snapshots():
    async def stub(operation, query, variables):
        if operation == "GetAccountsMin":
            return {"accounts": [{"id": "a1", "displayName": "Closed Card",
                                  "type": {"name": "credit"}, "includeInNetWorth": True}]}
        if operation == "SnapshotsForAccount":
            return {"snapshots": []}
        raise AssertionError(operation)

    adapter = CommunityAdapter(SESSION, transport=stub)
    assert adapter.get_balance_history() == []


def test_get_cashflow_maps_aggregates_and_floored_rate():
    async def stub(operation, query, variables):
        assert operation == "Web_GetCashFlowPage"
        assert variables["filters"]["startDate"] == "2026-06-01"
        assert variables["filters"]["endDate"] == "2026-06-30"
        return {"summary": [{"summary": {
            "sumIncome": 45976.73,
            "sumExpense": -34059.93,
            "savings": 11916.80,
            "savingsRate": 0.2592,
            "__typename": "TransactionsSummary",
        }}]}

    adapter = CommunityAdapter(SESSION, transport=stub)
    cf = adapter.get_cashflow(datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))
    assert isinstance(cf, Cashflow)
    assert cf.start == datetime.date(2026, 6, 1)
    assert cf.end == datetime.date(2026, 6, 30)
    assert cf.income == Decimal("45976.73")     # penny-exact through str()
    assert cf.expense == Decimal("-34059.93")
    assert cf.savings == Decimal("11916.80")
    assert cf.savings_rate_monarch == Decimal("0.2592")


def test_get_cashflow_passes_through_monarch_floored_negative_rate():
    # YTD live case: real savings is negative but Monarch floors the rate to 0.
    async def stub(operation, query, variables):
        return {"summary": [{"summary": {
            "sumIncome": 100000.00,
            "sumExpense": -131086.46,
            "savings": -31086.46,
            "savingsRate": 0.0,
            "__typename": "TransactionsSummary",
        }}]}

    adapter = CommunityAdapter(SESSION, transport=stub)
    cf = adapter.get_cashflow(datetime.date(2026, 1, 1), datetime.date(2026, 8, 9))
    assert cf.savings == Decimal("-31086.46")
    assert cf.savings_rate_monarch == Decimal("0")   # Monarch floored it; we pass it through


def test_get_networth_history_maps_snapshots():
    async def stub(operation, query, variables):
        assert operation == "GetAggregateSnapshots"
        return {"aggregateSnapshots": [
            {"date": "2026-01-31", "balance": 1250000.00, "__typename": "AggregateData"},
            {"date": "2026-02-28", "balance": 1262500.55, "__typename": "AggregateData"},
        ]}

    adapter = CommunityAdapter(SESSION, transport=stub)
    out = adapter.get_networth_history()
    assert all(isinstance(s, NetWorthSnapshot) for s in out)
    assert out[0].date == datetime.date(2026, 1, 31)
    assert out[1].net_worth == Decimal("1262500.55")   # penny-exact through str()


def test_get_categories_maps_group():
    async def stub(operation, query, variables):
        assert operation == "GetCategories"
        assert "type" in query  # group.type must be requested
        return {"categories": [
            {"id": "c1", "name": "Groceries",
             "group": {"id": "g1", "name": "Food & Dining", "type": "expense"}},
            {"id": "c2", "name": "Mystery", "group": None},
            {"id": "c3", "name": "Paychecks",
             "group": {"id": "g2", "name": "Income", "type": "income"}},
        ]}

    adapter = CommunityAdapter(SESSION, transport=stub)
    out = {c.id: c for c in adapter.get_categories()}
    assert out["c1"].group == "Food & Dining"
    assert out["c1"].group_type == "expense"
    assert out["c2"].group is None
    assert out["c2"].group_type is None
    assert out["c3"].group_type == "income"


def test_get_budgets_maps_planned_actual_remaining():
    async def stub(operation, query, variables):
        assert operation == "GetJointPlanningData"
        assert variables["startDate"] == "2026-06-01"
        assert variables["endDate"] == "2026-06-30"
        # Regression: GraphQL rejects a query that declares a variable its body
        # never uses (HTTP 400 "line 2, column 66"). We stripped the goals
        # sections, so the goals flags must not be declared or sent.
        assert set(variables) == {"startDate", "endDate"}
        assert "useLegacyGoals" not in query and "useV2Goals" not in query
        return {"budgetData": {"monthlyAmountsByCategory": [
            {"category": {"id": "c1"}, "monthlyAmounts": [
                {"month": "2026-06-01", "plannedCashFlowAmount": 500.00,
                 "actualAmount": -620.00, "remainingAmount": -120.00}]},
        ]}}

    adapter = CommunityAdapter(SESSION, transport=stub)
    out = adapter.get_budgets(datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))
    assert len(out) == 1
    assert isinstance(out[0], BudgetItem)
    assert out[0].category_id == "c1"
    assert out[0].budgeted == Decimal("500.00")
    assert out[0].actual == Decimal("-620.00")
    assert out[0].remaining == Decimal("-120.00")


def test_get_holdings_fans_out_over_accounts_with_holdings():
    holdings_by_acct = {
        "acct-brok": {"portfolio": {"aggregateHoldings": {"edges": [
            {"node": {
                "quantity": 120.5, "basis": 25000.00, "totalValue": 32341.20,
                "securityPriceChangeDollars": 180.75, "securityPriceChangePercent": 0.56,
                "security": {"name": "Vanguard Total Stock Market ETF", "ticker": "VTI",
                             "currentPrice": 268.40, "closingPrice": 267.10},
            }},
            {"node": {  # manual holding: null ticker, null basis, null day-change, null currentPrice
                "quantity": 1, "basis": None, "totalValue": 1900.00,
                "securityPriceChangeDollars": None, "securityPriceChangePercent": None,
                "security": {"name": "Manual Gold", "ticker": None,
                             "currentPrice": None, "closingPrice": 1900.00},
            }},
        ]}}},
    }

    async def stub(operation, query, variables):
        if operation == "GetAccounts":
            return {"accounts": [
                {"id": "acct-brok", "displayName": "Brokerage", "holdingsCount": 2},
                {"id": "acct-check", "displayName": "Checking", "holdingsCount": 0},
                {"id": "acct-noh", "displayName": "Odd", "holdingsCount": None},
            ]}
        if operation == "Web_GetHoldings":
            return holdings_by_acct[variables["input"]["accountIds"][0]]
        raise AssertionError(f"unexpected operation {operation}")

    adapter = CommunityAdapter(SESSION, transport=stub, today=lambda: datetime.date(2026, 6, 30))
    out = adapter.get_holdings()
    # only the holdingsCount>0 account was queried -> 2 positions
    assert len(out) == 2
    vti = next(h for h in out if h.ticker == "VTI")
    assert vti.account == "Brokerage"
    assert vti.value == Decimal("32341.20")
    assert vti.cost_basis == Decimal("25000.00")
    assert vti.price == Decimal("268.40")           # currentPrice preferred
    assert vti.day_change_dollars == Decimal("180.75")
    gold = next(h for h in out if h.name == "Manual Gold")
    assert gold.ticker is None
    assert gold.cost_basis is None                  # null basis -> None
    assert gold.day_change_dollars is None
    assert gold.price == Decimal("1900.00")         # currentPrice null -> closingPrice fallback


def test_get_recurring_maps_items():
    async def stub(operation, query, variables):
        assert operation == "Web_GetUpcomingRecurringTransactionItems"
        assert variables["startDate"] == "2026-06-01"
        assert variables["endDate"] == "2026-06-30"
        return {"recurringTransactionItems": [
            {"stream": {"frequency": "monthly", "amount": -15.99,
                        "merchant": {"name": "Netflix"}},
             "date": "2026-06-03", "isPast": True, "amount": -15.99,
             "category": {"name": "Entertainment"},
             "account": {"displayName": "Credit Card"}},
            {"stream": {"frequency": None, "merchant": {"name": "Mystery"}},
             "date": "2026-06-20", "isPast": False, "amount": -9.00,
             "category": None, "account": None},
        ]}

    adapter = CommunityAdapter(SESSION, transport=stub)
    out = adapter.get_recurring(datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))
    assert len(out) == 2
    netflix = out[0]
    assert netflix.merchant == "Netflix"
    assert netflix.amount == Decimal("-15.99")
    assert netflix.frequency == "monthly"
    assert netflix.account == "Credit Card"
    assert netflix.is_past is True
    mystery = out[1]
    assert mystery.frequency is None and mystery.account is None and mystery.category is None
    assert mystery.is_past is False


def test_holdings_and_recurring_query_constants_present():
    from financial_mcp.monarch import queries
    assert "Web_GetHoldings" in queries.GET_HOLDINGS
    assert "totalValue" in queries.GET_HOLDINGS
    assert "basis" in queries.GET_HOLDINGS
    assert "ticker" in queries.GET_HOLDINGS
    assert "Web_GetUpcomingRecurringTransactionItems" in queries.GET_RECURRING
    assert "recurringTransactionItems" in queries.GET_RECURRING
    assert "frequency" in queries.GET_RECURRING
    # $filters is USED in the body (not the GET_BUDGETS unused-variable case)
    assert "filters: $filters" in queries.GET_RECURRING


def test_to_transaction_populates_merchant_id_and_logo():
    from financial_mcp.monarch.community_adapter import _to_transaction
    d = {
        "id": "t1", "date": "2026-08-01", "amount": -12.5,
        "merchant": {"id": "m1", "name": "Amazon", "logoUrl": "http://x/y.png"},
        "category": {"name": "Shopping"}, "account": {"displayName": "Card"},
        "plaidName": "AMZN Mktp US*2X4", "notes": "", "tags": [],
    }
    t = _to_transaction(d)
    assert t.merchant_id == "m1"
    assert t.merchant_logo == "http://x/y.png"


def test_to_transaction_merchant_id_and_logo_default_empty():
    from financial_mcp.monarch.community_adapter import _to_transaction
    d = {"id": "t2", "date": "2026-08-01", "amount": -1,
         "merchant": {"name": "Corner Store"}, "category": {"name": "Food"},
         "account": {"displayName": "Card"}, "plaidName": "", "notes": "", "tags": []}
    t = _to_transaction(d)
    assert t.merchant_id == ""
    assert t.merchant_logo == ""
