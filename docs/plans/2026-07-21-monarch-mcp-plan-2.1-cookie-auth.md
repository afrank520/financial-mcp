# Monarch MCP — Plan 2.1: Cookie-Based Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace dead token auth with browser-session-cookie auth against `api.monarch.com`, so the local server answers account/transaction/balance questions from live Monarch data.

**Architecture:** The `MonarchAdapter` seam is unchanged — only the auth + transport layer, login CLI, and dependencies change. We own the GraphQL wire path with `aiohttp` (cookies + `x-csrftoken` + `origin` headers), drop the `monarchmoney` dependency, and copy the two query strings we need into our own module. Everything downstream (models, cache, DataService, MCP tools) is untouched.

**Tech Stack:** Python 3.14, aiohttp (direct dep), keyring (macOS Keychain), pydantic v2, pytest.

**Design spec:** `docs/specs/2026-07-21-monarch-mcp-plan-2.1-cookie-auth-design.md`

## Global Constraints

- **Endpoint:** `https://api.monarch.com/graphql`. The old `api.monarchmoney.com` is dead (405).
- **Auth = two cookies:** `session_id` + `csrftoken`. No `cf_clearance`/`__cf_bm` needed (proven by spike).
- **Required request shape** (proven by spike): cookies `session_id` + `csrftoken`; headers `x-csrftoken` (= csrftoken value), `origin: https://app.monarch.com`, `client-platform: web`, `content-type: application/json`, `accept: */*`, and a real-browser `user-agent`. Omitting `x-csrftoken` → 403 CSRF; omitting `origin` → 403 Referer.
- **Secrets live ONLY in macOS Keychain** via `keyring`. Never logged, never echoed, never committed. The cURL blob is read from stdin and never printed; only `session_id` + `csrftoken` are extracted — all other cookies discarded.
- **Entity scope:** accounts, transactions, balances only (unchanged from Plan 2). Richer entities → Plan 3.
- **Retain the Plan-2 loop-safe `_run` bridge verbatim** — FastMCP calls sync tools on its running event loop, so `asyncio.run` must thread-offload when a loop is already running.
- **Two distinct typed errors:** `SessionExpiredError` (401/403 + JSON detail) and `CloudflareBlockedError` (403 + `"Shall Not Pass"` body). Both subclass `RuntimeError` so DataService's stale-on-failure path still catches them.
- **Per-call `aiohttp.ClientSession`** (NOT shared) — a shared session breaks under the loop-hopping `_run` bridge.
- TDD, DRY, YAGNI, frequent commits. `ruff` + `black` clean. Keep the full suite green at the end of every task.

---

### Task 1: GraphQL query constants (`queries.py`)

**Files:**
- Create: `financial_mcp/monarch/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `GET_ACCOUNTS: str`, `GET_TRANSACTIONS: str` — GraphQL query strings imported by `community_adapter.py` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queries.py
from financial_mcp.monarch import queries


def test_get_accounts_query_shape():
    q = queries.GET_ACCOUNTS
    assert "query GetAccounts" in q
    # fields the _to_account mapper reads
    assert "displayName" in q
    assert "currentBalance" in q


def test_get_transactions_query_shape():
    q = queries.GET_TRANSACTIONS
    assert "allTransactions" in q
    assert "totalCount" in q
    assert "results(offset: $offset, limit: $limit" in q
    # fields the _to_transaction mapper reads
    for field in ("amount", "date", "plaidName", "notes", "merchant", "category", "account", "tags"):
        assert field in q
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_queries.py -v`
Expected: FAIL — `ModuleNotFoundError: financial_mcp.monarch.queries`

- [ ] **Step 3: Write the module**

Copy the two query strings verbatim from `monarchmoney` 0.1.15 (`GetAccounts`, `GetTransactionsList`), crediting the source.

```python
# financial_mcp/monarch/queries.py
"""Monarch GraphQL query strings.

Copied verbatim from the monarchmoney library (github.com/hammem/monarchmoney,
v0.1.15, MIT License) so we own our copy of the Monarch contract without a
dependency on that (now dead-domain) library. Only the read queries this plan
needs are included; Plan 3 will add more.
"""

GET_ACCOUNTS = """
  query GetAccounts {
    accounts {
      ...AccountFields
      __typename
    }
    householdPreferences {
      id
      accountGroupOrder
      __typename
    }
  }

  fragment AccountFields on Account {
    id
    displayName
    syncDisabled
    deactivatedAt
    isHidden
    isAsset
    mask
    createdAt
    updatedAt
    displayLastUpdatedAt
    currentBalance
    displayBalance
    includeInNetWorth
    hideFromList
    hideTransactionsFromReports
    includeBalanceInNetWorth
    includeInGoalBalance
    dataProvider
    dataProviderAccountId
    isManual
    transactionsCount
    holdingsCount
    manualInvestmentsTrackingMethod
    order
    logoUrl
    type {
      name
      display
      __typename
    }
    subtype {
      name
      display
      __typename
    }
    credential {
      id
      updateRequired
      disconnectedFromDataProviderAt
      dataProvider
      institution {
        id
        plaidInstitutionId
        name
        status
        __typename
      }
      __typename
    }
    institution {
      id
      name
      primaryColor
      url
      __typename
    }
    __typename
  }
"""

GET_TRANSACTIONS = """
  query GetTransactionsList($offset: Int, $limit: Int, $filters: TransactionFilterInput, $orderBy: TransactionOrdering) {
    allTransactions(filters: $filters) {
      totalCount
      results(offset: $offset, limit: $limit, orderBy: $orderBy) {
        id
        ...TransactionOverviewFields
        __typename
      }
      __typename
    }
    transactionRules {
      id
      __typename
    }
  }

  fragment TransactionOverviewFields on Transaction {
    id
    amount
    pending
    date
    hideFromReports
    plaidName
    notes
    isRecurring
    reviewStatus
    needsReview
    attachments {
      id
      extension
      filename
      originalAssetUrl
      publicId
      sizeBytes
      __typename
    }
    isSplitTransaction
    createdAt
    updatedAt
    category {
      id
      name
      __typename
    }
    merchant {
      name
      id
      transactionsCount
      __typename
    }
    account {
      id
      displayName
      __typename
    }
    tags {
      id
      name
      color
      order
      __typename
    }
    __typename
  }
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_queries.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/queries.py tests/test_queries.py
git commit -m "feat: add Monarch GraphQL query constants (drop-in for library queries)"
```

---

### Task 2: Cookie-based adapter transport (`community_adapter.py`)

**Files:**
- Modify: `financial_mcp/monarch/community_adapter.py` (full rewrite of auth/transport; mappers unchanged)
- Modify: `financial_mcp/mcp_server.py` (catch the new `CloudflareBlockedError` too)
- Test: `tests/test_community_adapter.py` (rewrite to inject a stub transport)
- Test: `tests/test_mcp_tools.py` (add Cloudflare-error tool test — see Step 7)

**Interfaces:**
- Consumes: `queries.GET_ACCOUNTS`, `queries.GET_TRANSACTIONS` (Task 1); `auth.DEFAULT_SERVICE` (unchanged constant).
- Produces:
  - `CommunityAdapter(session: dict, service_name: str = auth.DEFAULT_SERVICE, transport: Callable[[str, str, dict], Awaitable[dict]] | None = None, today: Callable[[], datetime.date] = datetime.date.today, page_size: int = 100)` where `session` is `{"session_id": str, "csrftoken": str}`.
  - `SessionExpiredError(RuntimeError)`, `CloudflareBlockedError(RuntimeError)`.
  - `GRAPHQL_URL: str`, `USER_AGENT: str` module constants.
  - The injected `transport` is an async callable `(operation, query, variables) -> data_dict` that returns the GraphQL `data` object and raises the typed errors on auth failure.

- [ ] **Step 1: Write the failing tests (rewrite the file)**

Replace `tests/test_community_adapter.py` entirely. The stub is now an async `transport` that returns canned `data` dicts (what the real transport returns after unwrapping/error-checking), keyed by operation.

```python
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
        accounts = {"accounts": accounts["accounts"] + [{"displayName": "Investment", "currentBalance": None}]}

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

    a = CommunityAdapter(SESSION, transport=transport, today=lambda: datetime.date(2026, 2, 1), page_size=1)
    assert len(a.get_transactions()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_community_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'CloudflareBlockedError'` (and signature mismatches)

- [ ] **Step 3: Rewrite `community_adapter.py`**

```python
# financial_mcp/monarch/community_adapter.py
from __future__ import annotations

import asyncio
import concurrent.futures
import datetime
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal

from financial_mcp.monarch import auth
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, Transaction
from financial_mcp.monarch.queries import GET_ACCOUNTS, GET_TRANSACTIONS

GRAPHQL_URL = "https://api.monarch.com/graphql"
# A real-browser User-Agent keeps us clear of any future Cloudflare UA rule.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


class SessionExpiredError(RuntimeError):
    """Raised when Monarch rejects the stored session (401/403 auth failure).
    Caller should prompt the user to re-run `financial-mcp-login`."""


class CloudflareBlockedError(RuntimeError):
    """Raised when Cloudflare blocks the request (403 "Shall Not Pass") before it
    reaches the Monarch API. Distinct from an expired session so a future Monarch
    tightening is diagnosable rather than mistaken for a dead token."""


def _to_account(d: dict, as_of: datetime.date) -> Account:
    return Account(
        name=d["displayName"],
        latest_balance=Decimal(str(d.get("currentBalance") or 0)),
        as_of=as_of,
    )


def _to_transaction(d: dict) -> Transaction:
    date = datetime.date.fromisoformat(d["date"])
    merchant = (d.get("merchant") or {}).get("name", "")
    amount = Decimal(str(d["amount"]))
    original = d.get("plaidName") or ""
    tid = d.get("id") or Transaction.synth_id(date, merchant, amount, original)
    return Transaction(
        id=str(tid),
        date=date,
        merchant=merchant,
        category=(d.get("category") or {}).get("name", ""),
        account=(d.get("account") or {}).get("displayName", ""),
        original_statement=original,
        notes=d.get("notes") or "",
        amount=amount,
        tags=[t["name"] for t in (d.get("tags") or [])],
        owner=d.get("owner") or "",
    )


class CommunityAdapter(MonarchAdapter):
    def __init__(
        self,
        session: dict,
        service_name: str = auth.DEFAULT_SERVICE,
        transport: Callable[[str, str, dict], Awaitable[dict]] | None = None,
        today: Callable[[], datetime.date] = datetime.date.today,
        page_size: int = 100,
    ) -> None:
        self._session = session
        self._service_name = service_name
        self._transport = transport or self._default_transport
        self._today = today
        self._page_size = page_size

    def source_name(self) -> str:
        return "live"

    async def _default_transport(self, operation: str, query: str, variables: dict) -> dict:
        """POST a GraphQL query to Monarch with cookie auth. Returns the `data`
        object; raises typed errors on auth/Cloudflare failure. A fresh
        ClientSession per call — a shared session breaks under the _run bridge,
        which may execute each call on a different event loop."""
        import aiohttp

        sid = self._session["session_id"]
        csrf = self._session["csrftoken"]
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "client-platform": "web",
            "origin": "https://app.monarch.com",
            "user-agent": USER_AGENT,
            "x-csrftoken": csrf,
        }
        cookies = {"session_id": sid, "csrftoken": csrf}
        payload = {"operationName": operation, "variables": variables, "query": query}

        async with aiohttp.ClientSession(cookies=cookies) as session:
            async with session.post(GRAPHQL_URL, json=payload, headers=headers) as resp:
                status = resp.status
                text = await resp.text()

        if status == 200:
            try:
                body = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Monarch returned non-JSON (HTTP 200): {text[:200]}") from exc
            if body.get("data"):
                return body["data"]
            raise RuntimeError(f"Monarch GraphQL error: {json.dumps(body.get('errors'))[:200]}")

        if status in (401, 403):
            if "Shall Not Pass" in text:
                raise CloudflareBlockedError(
                    "Monarch's Cloudflare blocked the request — refresh your session via "
                    "`financial-mcp-login`."
                )
            raise SessionExpiredError("Monarch session expired — re-run `financial-mcp-login`.")

        raise RuntimeError(f"Monarch HTTP {status}: {text[:200]}")

    def _run(self, coro):
        """Run a coroutine to completion, tolerating an already-running event loop.
        FastMCP invokes sync tools on its running loop, where asyncio.run() would
        raise; offload to a worker thread in that case."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running = False
        else:
            running = True
        if running:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    def get_accounts(self) -> list[Account]:
        data = self._run(self._transport("GetAccounts", GET_ACCOUNTS, {}))
        as_of = self._today()
        return [_to_account(a, as_of) for a in data.get("accounts", [])]

    def get_balances(self) -> list[Balance]:
        return [
            Balance(date=a.as_of, balance=a.latest_balance, account=a.name)
            for a in self.get_accounts()
        ]

    def get_transactions(self) -> list[Transaction]:
        out: list[Transaction] = []
        offset = 0
        while True:
            variables = {
                "offset": offset,
                "limit": self._page_size,
                "orderBy": "date",
                "filters": {"search": "", "categories": [], "accounts": [], "tags": []},
            }
            data = self._run(self._transport("GetTransactionsList", GET_TRANSACTIONS, variables))
            block = data.get("allTransactions", {})
            results = block.get("results", [])
            out.extend(_to_transaction(r) for r in results)
            if not results:
                break
            offset += len(results)
            total = block.get("totalCount")
            if total is not None and offset >= total:
                break
        return out
```

Notes for the implementer:
- The typed errors are raised *inside* `_default_transport` (which sees the HTTP status). The stub transport in tests raises them directly. The old `_is_auth_error` string-matching helper is gone — we now key off real HTTP status, which is more precise (folds in the Plan-2 deferred minor).
- `aiohttp` is imported lazily inside `_default_transport` so importing the module never requires the network stack; tests inject a stub and never construct a real client.

- [ ] **Step 4: Update `mcp_server.py` to also surface `CloudflareBlockedError`**

In `financial_mcp/mcp_server.py`, change the import and the `get_accounts` tool's except clause:

```python
from financial_mcp.monarch.community_adapter import CloudflareBlockedError, SessionExpiredError
```

```python
    @server.tool()
    def get_accounts() -> list[dict] | dict:
        """List financial accounts with their latest known balance."""
        try:
            return _dump(svc.accounts())
        except (SessionExpiredError, CloudflareBlockedError) as exc:
            return {"error": f"{exc}. Re-authenticate: financial-mcp-login"}
```

- [ ] **Step 5: Run adapter tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_community_adapter.py -v`
Expected: PASS (all tests, including both running-loop regressions, null-balance, both typed errors, totalCount-absent pagination)

- [ ] **Step 6: Add a Cloudflare-block tool test to `tests/test_mcp_tools.py`**

First read the existing SessionExpiredError tool test in that file to match its style, then add a sibling for `CloudflareBlockedError`. It must assert the tool returns an `{"error": ...}` dict containing `financial-mcp-login` (not raise). Use the same fake-DataService/adapter pattern already present in the file. Example shape (adapt to the file's existing fixtures):

```python
def test_get_accounts_tool_surfaces_cloudflare_block():
    from financial_mcp.monarch.community_adapter import CloudflareBlockedError
    # build a server whose svc.accounts() raises CloudflareBlockedError,
    # following the same construction as the existing SessionExpiredError test,
    # then invoke the get_accounts tool and assert:
    #   result == {"error": "...financial-mcp-login"} style dict
    ...
```

- [ ] **Step 7: Run the tool tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -v`
Expected: PASS (existing + new Cloudflare test)

- [ ] **Step 8: Run the full suite to confirm green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. `test_main_adapter_selection.py` still passes because `build_adapter` is unchanged and `CommunityAdapter("tok-abc", "svc")` is positionally compatible (the first arg is stored as `session` without validation). It is fixed properly in Task 3.

- [ ] **Step 9: Lint + commit**

```bash
.venv/bin/ruff check financial_mcp tests && .venv/bin/black --check financial_mcp tests
git add financial_mcp/monarch/community_adapter.py financial_mcp/mcp_server.py tests/test_community_adapter.py tests/test_mcp_tools.py
git commit -m "feat: cookie-based GraphQL transport for CommunityAdapter"
```

---

### Task 3: Session storage + adapter selection (`auth.py` + `main.py`)

**Files:**
- Modify: `financial_mcp/monarch/auth.py` (token API → session-dict API)
- Modify: `financial_mcp/main.py` (`build_adapter` loads session dict)
- Test: `tests/test_auth.py` (session round-trip)
- Test: `tests/test_main_adapter_selection.py` (session-based selection)

**Interfaces:**
- Consumes: `CommunityAdapter(session, service_name)` (Task 2).
- Produces:
  - `auth.save_session(session_id: str, csrftoken: str, service_name=DEFAULT_SERVICE) -> None`
  - `auth.load_session(service_name=DEFAULT_SERVICE) -> dict | None` — `{"session_id","csrftoken"}` or `None`
  - `auth.delete_session(service_name=DEFAULT_SERVICE) -> None`
  - `auth.DEFAULT_SERVICE` unchanged; `SESSION_USERNAME = "monarch_session"`.

- [ ] **Step 1: Write the failing tests (rewrite `test_auth.py`)**

Keep the `MemoryKeyring` backend fixture; replace the token tests with session tests.

```python
# tests/test_auth.py
import keyring
from keyring.backends.fail import Keyring as FailKeyring


class MemoryKeyring(FailKeyring):
    """In-memory keyring backend for tests — no OS Keychain touched."""

    priority = 1  # type: ignore[assignment]

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


def _use_memory_backend():
    keyring.set_keyring(MemoryKeyring())


def test_save_and_load_roundtrip():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    assert auth.load_session("svc") is None
    auth.save_session("sid-1", "csrf-1", "svc")
    assert auth.load_session("svc") == {"session_id": "sid-1", "csrftoken": "csrf-1"}


def test_delete_session():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    auth.save_session("sid-1", "csrf-1", "svc")
    auth.delete_session("svc")
    assert auth.load_session("svc") is None


def test_delete_absent_is_noop():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    auth.delete_session("svc")  # must not raise
    assert auth.load_session("svc") is None


def test_load_ignores_malformed_blob():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    keyring.set_password("svc", auth.SESSION_USERNAME, "not-json")
    assert auth.load_session("svc") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_auth.py -v`
Expected: FAIL — `AttributeError: module 'financial_mcp.monarch.auth' has no attribute 'save_session'`

- [ ] **Step 3: Rewrite `auth.py`**

```python
# financial_mcp/monarch/auth.py
from __future__ import annotations

import json

import keyring

DEFAULT_SERVICE = "financial_mcp_monarch"
SESSION_USERNAME = "monarch_session"


def save_session(session_id: str, csrftoken: str, service_name: str = DEFAULT_SERVICE) -> None:
    blob = json.dumps({"session_id": session_id, "csrftoken": csrftoken})
    keyring.set_password(service_name, SESSION_USERNAME, blob)


def load_session(service_name: str = DEFAULT_SERVICE) -> dict | None:
    blob = keyring.get_password(service_name, SESSION_USERNAME)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "session_id" not in data or "csrftoken" not in data:
        return None
    return {"session_id": data["session_id"], "csrftoken": data["csrftoken"]}


def delete_session(service_name: str = DEFAULT_SERVICE) -> None:
    try:
        keyring.delete_password(service_name, SESSION_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent
```

- [ ] **Step 4: Rewrite `test_main_adapter_selection.py` for sessions**

```python
# tests/test_main_adapter_selection.py
import pytest

from financial_mcp.config import Config
from financial_mcp.main import build_adapter
from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter

_SESSION = {"session_id": "sid-1", "csrftoken": "csrf-1"}


def test_csv_source_builds_csv_adapter():
    cfg = Config(source="csv", csv_transactions_path="t.csv", csv_balances_path="b.csv")
    assert isinstance(build_adapter(cfg), CsvAdapter)


def test_csv_source_without_paths_exits():
    cfg = Config(source="csv")
    with pytest.raises(SystemExit):
        build_adapter(cfg)


def test_live_with_session_builds_community_adapter(monkeypatch):
    monkeypatch.setattr("financial_mcp.main.auth.load_session", lambda service: _SESSION)
    cfg = Config(source="live")
    adapter = build_adapter(cfg)
    assert isinstance(adapter, CommunityAdapter)


def test_live_without_session_falls_back_to_csv(monkeypatch, caplog):
    monkeypatch.setattr("financial_mcp.main.auth.load_session", lambda service: None)
    cfg = Config(source="live", csv_transactions_path="t.csv", csv_balances_path="b.csv")
    import logging

    with caplog.at_level(logging.WARNING, logger="financial_mcp"):
        adapter = build_adapter(cfg)
    assert isinstance(adapter, CsvAdapter)
    assert any("financial-mcp-login" in r.message for r in caplog.records)


def test_live_without_session_and_no_csv_exits(monkeypatch):
    monkeypatch.setattr("financial_mcp.main.auth.load_session", lambda service: None)
    cfg = Config(source="live")
    with pytest.raises(SystemExit):
        build_adapter(cfg)
```

- [ ] **Step 5: Run to verify these fail**

Run: `.venv/bin/python -m pytest tests/test_main_adapter_selection.py -v`
Expected: FAIL — `build_adapter` still calls `auth.load_token` / constructs with a token.

- [ ] **Step 6: Update `build_adapter` in `main.py`**

Replace the `if cfg.source == "live":` block:

```python
def build_adapter(cfg: Config) -> MonarchAdapter:
    if cfg.source == "live":
        session = auth.load_session(cfg.keyring_service_name)
        if session:
            return CommunityAdapter(session, cfg.keyring_service_name)
        logger.warning("no Monarch session — run `financial-mcp-login`; falling back to CSV")
        return _csv_or_exit(cfg)
    return _csv_or_exit(cfg)
```

- [ ] **Step 7: Run both test files, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_auth.py tests/test_main_adapter_selection.py -v`
Expected: PASS
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (full suite green)

- [ ] **Step 8: Lint + commit**

```bash
.venv/bin/ruff check financial_mcp tests && .venv/bin/black --check financial_mcp tests
git add financial_mcp/monarch/auth.py financial_mcp/main.py tests/test_auth.py tests/test_main_adapter_selection.py
git commit -m "feat: store session cookies (session_id + csrftoken) in Keychain; select adapter by session"
```

---

### Task 4: Login CLI — cURL-paste flow (`login_cli.py`)

**Files:**
- Modify: `financial_mcp/login_cli.py` (full rewrite around cookie extraction)
- Test: `tests/test_login_cli.py` (new — parser unit tests)

**Interfaces:**
- Consumes: `auth.save_session` / `auth.delete_session` (Task 3); `CommunityAdapter(session)` (Task 2) for validation.
- Produces: `parse_curl_cookies(blob: str) -> dict` returning `{"session_id","csrftoken"}`; raises `ValueError` with a clear message if either cookie is absent.

- [ ] **Step 1: Write the failing parser tests**

```python
# tests/test_login_cli.py
import pytest

from financial_mcp.login_cli import parse_curl_cookies

# Synthetic blob — FAKE cookie values, safe to commit.
_CURL_B_FLAG = (
    "curl 'https://api.monarch.com/graphql' "
    "-H 'content-type: application/json' "
    "-b 'ajs_anonymous_id=xxx; cf_clearance=yyy; "
    "session_id=SID_FAKE_123; csrftoken=CSRF_FAKE_456; _fbp=zzz' "
    "-H 'x-csrftoken: CSRF_FAKE_456'"
)

_CURL_COOKIE_HEADER = (
    "curl 'https://api.monarch.com/graphql' "
    "-H 'cookie: session_id=SID_FAKE_123; csrftoken=CSRF_FAKE_456' "
    "-H 'x-csrftoken: CSRF_FAKE_456'"
)


def test_parse_b_flag():
    got = parse_curl_cookies(_CURL_B_FLAG)
    assert got == {"session_id": "SID_FAKE_123", "csrftoken": "CSRF_FAKE_456"}


def test_parse_cookie_header():
    got = parse_curl_cookies(_CURL_COOKIE_HEADER)
    assert got == {"session_id": "SID_FAKE_123", "csrftoken": "CSRF_FAKE_456"}


def test_missing_session_id_raises():
    blob = "curl 'https://api.monarch.com/graphql' -b 'csrftoken=CSRF_FAKE_456'"
    with pytest.raises(ValueError, match="session_id"):
        parse_curl_cookies(blob)


def test_missing_csrftoken_raises():
    blob = "curl 'https://api.monarch.com/graphql' -b 'session_id=SID_FAKE_123'"
    with pytest.raises(ValueError, match="csrftoken"):
        parse_curl_cookies(blob)


def test_empty_blob_raises():
    with pytest.raises(ValueError):
        parse_curl_cookies("")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_login_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_curl_cookies'`

- [ ] **Step 3: Rewrite `login_cli.py`**

```python
# financial_mcp/login_cli.py
from __future__ import annotations

import os
import shlex
import sys

from financial_mcp.config import load_config
from financial_mcp.monarch import auth

_WANTED = ("session_id", "csrftoken")


def _service_name() -> str:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    return cfg.keyring_service_name


def parse_curl_cookies(blob: str) -> dict:
    """Extract session_id + csrftoken from a browser 'Copy as cURL' command.

    Reads cookies from a -b/--cookie argument or a 'Cookie:' header. Raises
    ValueError (naming the missing cookie) if either required cookie is absent.
    """
    text = blob.replace("\\\n", " ").strip()
    if not text:
        raise ValueError("empty input — paste the full 'Copy as cURL' command")
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"could not parse the cURL command: {exc}") from exc

    cookie_str = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-b", "--cookie") and i + 1 < len(tokens):
            cookie_str = tokens[i + 1]
            i += 2
            continue
        if tok in ("-H", "--header") and i + 1 < len(tokens):
            k, _, v = tokens[i + 1].partition(":")
            if k.strip().lower() == "cookie":
                cookie_str = cookie_str or v.strip()
            i += 2
            continue
        i += 1

    jar: dict[str, str] = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            jar[k.strip()] = v.strip()

    out = {}
    for name in _WANTED:
        if name not in jar or not jar[name]:
            raise ValueError(
                f"could not find '{name}' cookie in the pasted request — make sure you "
                "copied a request to api.monarch.com while logged in"
            )
        out[name] = jar[name]
    return out


def _validate(session: dict) -> bool:
    """Confirm the session works with one live accounts call."""
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    try:
        CommunityAdapter(session).get_accounts()
        return True
    except Exception:  # noqa: BLE001
        print(
            "That session did not work — it may be expired or incomplete. "
            "Log in fresh in your browser and copy a new request.",
            file=sys.stderr,
        )
        return False


def _login() -> None:
    print(
        "Monarch login.\n"
        "1. In your browser, open Monarch and log in.\n"
        "2. DevTools -> Network tab, filter 'graphql', click a POST request to "
        "api.monarch.com.\n"
        "3. Right-click it -> Copy -> Copy as cURL.\n"
        "4. Paste it below, then press Ctrl-D (Ctrl-Z Enter on Windows):"
    )
    blob = sys.stdin.read()
    try:
        session = parse_curl_cookies(blob)
    except ValueError as exc:
        raise SystemExit(f"Login failed: {exc}")
    if not _validate(session):
        raise SystemExit("Login failed. Try again with a fresh request.")
    auth.save_session(session["session_id"], session["csrftoken"], _service_name())
    print("Session saved to Keychain.")


def main() -> None:
    if "--logout" in sys.argv:
        auth.delete_session(_service_name())
        print("Logged out — session removed from Keychain.")
        return
    _login()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run parser tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_login_cli.py -v`
Expected: PASS
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (full suite). No module imports `monarchmoney` anymore.

- [ ] **Step 5: Verify no lingering monarchmoney imports**

Run: `grep -rn "monarchmoney\|import MonarchMoney\|RequireMFAException" financial_mcp/`
Expected: no matches (login_cli and community_adapter both cleaned).

- [ ] **Step 6: Lint + commit**

```bash
.venv/bin/ruff check financial_mcp tests && .venv/bin/black --check financial_mcp tests
git add financial_mcp/login_cli.py tests/test_login_cli.py
git commit -m "feat: cURL-paste cookie login flow (financial-mcp-login)"
```

---

### Task 5: Drop the dependency + docs

**Files:**
- Modify: `pyproject.toml` (remove `monarchmoney`, add `aiohttp`)
- Modify: `config.example.yaml` (login comment)
- Modify: `docs/INSTALL.md` (Plan 2 section → cURL flow + updated smoke test)

**Interfaces:** none (final cleanup task).

- [ ] **Step 1: Edit `pyproject.toml` dependencies**

Change the `dependencies` list — remove the `monarchmoney` line, add `aiohttp` (currently only pulled in transitively by the library we're dropping, so it must become a direct dep or it vanishes on a fresh install):

```toml
dependencies = [
    "mcp>=1.2.0",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "keyring>=25.0",
    "aiohttp>=3.9",
]
```

- [ ] **Step 2: Confirm the environment still resolves and tests pass**

Run: `.venv/bin/python -c "import aiohttp; print('aiohttp', aiohttp.__version__)"`
Expected: prints a 3.x version (already installed).
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (full suite — dependency edit doesn't change installed packages in the existing venv; this confirms nothing imports the removed library).

- [ ] **Step 3: Update `config.example.yaml`**

Replace the `source:` comment block so it no longer implies a token:

```yaml
# Data source: "csv" = offline Plan-1 exports | "live" = Monarch API.
# Run `financial-mcp-login` once before using "live" — it stores your Monarch
# session cookies (session_id + csrftoken) in the OS keyring. If "live" is set
# but no session is stored, the server logs a warning and falls back to CSV.
source: csv
keyring_service_name: financial_mcp_monarch
```

- [ ] **Step 4: Rewrite the `## Plan 2 — Live Monarch sync` section of `docs/INSTALL.md`**

Replace the section (lines for login, config, smoke test) with:

````markdown
## Plan 2 — Live Monarch sync (cookie auth)

Monarch's API requires a logged-in browser session (there is no public token/OAuth).
`financial-mcp-login` reads your session cookies once and stores them in the macOS
Keychain — never your password, never on disk.

### 1. Log in once (terminal, not Claude)
```bash
financial-mcp-login
```
1. In your browser, open Monarch (`app.monarch.com`) and log in as usual.
2. Open DevTools → **Network** tab, type `graphql` in the filter.
3. Click a **POST** request to `api.monarch.com/graphql` (reload the page if none appear).
4. Right-click it → **Copy → Copy as cURL**.
5. Paste it into the terminal at the prompt, then press **Ctrl-D**.

The command extracts only `session_id` + `csrftoken` and stores them in your Keychain;
every other cookie in the paste is discarded. To log out: `financial-mcp-login --logout`.

### 2. Switch config to live
In `config.yaml`:
```yaml
source: live
```

### 3. Manual smoke test (once, assumes a valid session)
```bash
FINANCIAL_MCP_CONFIG=config.yaml python -c "
from financial_mcp.config import load_config
from financial_mcp.monarch import auth
from financial_mcp.monarch.community_adapter import CommunityAdapter
cfg = load_config('config.yaml')
a = CommunityAdapter(auth.load_session(cfg.keyring_service_name), cfg.keyring_service_name)
print('accounts:', [x.name for x in a.get_accounts()])
print('transactions:', len(a.get_transactions()))
"
```
Expected: your real account names and a transaction count. **If the field names look
wrong**, the live GraphQL shape differs from the mappers in `community_adapter.py` —
adjust `_to_account` / `_to_transaction` and re-run `pytest tests/test_community_adapter.py`.

If the session has expired, tools return a clear "re-run `financial-mcp-login`" message
and the server keeps serving cached data with its age.

> **Coming next (Plan 2.2):** a browser-driven login that opens a window, lets you sign
> in with Google normally, and captures the session automatically — no DevTools or cURL.
````

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml config.example.yaml docs/INSTALL.md
git commit -m "chore: drop monarchmoney dep (add aiohttp); docs for cookie login"
```

---

## Definition of Done

- Full suite green; `ruff` + `black` clean.
- No source file imports `monarchmoney`; `pyproject.toml` lists `aiohttp`, not `monarchmoney`.
- `financial-mcp-login` accepts a Copy-as-cURL paste, extracts `session_id` + `csrftoken`, validates with a live call, and stores them in the Keychain; `--logout` clears them.
- `CommunityAdapter` fetches accounts/transactions/balances live from `api.monarch.com` with cookie auth, paginates transactions, and raises `SessionExpiredError` / `CloudflareBlockedError` distinctly.
- After merge, the manual smoke test in `docs/INSTALL.md` is run once against the real account to confirm live connectivity through Claude Desktop (the connectivity proof this plan exists for).

## Notes for the executor

- **Deferred to Plan 2.2:** browser-driven (Playwright) login. Not in this plan.
- **Plan-2 deferred minors folded in here:** `_is_auth_error` string-match replaced by real HTTP-status checks; pagination `totalCount`-absent guard added; login import ordering cleaned in the rewrite.
- **Refinement vs. the design spec:** the spec said "single shared `aiohttp.ClientSession`." This plan uses a **per-call** session because the loop-safe `_run` bridge may run each call on a different event loop, and aiohttp sessions are loop-bound. Low call frequency (12h cache) makes the overhead irrelevant.
