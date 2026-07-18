# Monarch MCP — Plan 2: Live Auth & Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live Monarch data path — a `CommunityAdapter` backed by the `monarchmoney` library and a one-time terminal login that stores only a session token in the macOS Keychain — for the three entities Plan 1 already models (accounts, transactions, balances), with the credential path fully isolated behind the existing adapter interface.

**Architecture:** All new code is additive behind Plan 1's `MonarchAdapter` seam. `auth.py` wraps the `keyring` library for token storage. `CommunityAdapter` wraps the async `monarchmoney` client, bridging async→sync with `asyncio.run()` inside each method and mapping the library's GraphQL dicts into the existing `Transaction`/`Balance`/`Account` models. `login_cli.py` runs once in a terminal (token paste primary, email+password+MFA fallback). `main.py` gains a `source: csv | live` switch that falls back to CSV with a visible log warning when no session exists. Models, cache, `DataService`, analytics, and all existing MCP tools are untouched.

**Tech Stack:** Python 3.12+, `monarchmoney==0.1.15` (async Monarch client), `keyring>=25.0` (already declared), Pydantic v2, pytest, Ruff, Black.

## Global Constraints

- Python **3.12+**; modern typing (`X | None`, `list[X]`), no `typing.Optional`.
- **No secrets in any committed file.** Credentials live only in the macOS Keychain via `keyring`. Password read with `getpass`, never stored, never logged. Token never logged in cleartext.
- Pin **`monarchmoney==0.1.15`** exactly. The library is imported **only** inside `financial_mcp/monarch/community_adapter.py` and `financial_mcp/login_cli.py` — never elsewhere.
- The `monarchmoney` async client is bridged to sync **inside the adapter** via `asyncio.run()`. The `MonarchAdapter` interface stays synchronous; no Plan 1 file becomes async.
- Tests use a **fake in-memory keyring backend** and a **stub `monarchmoney` client** — zero network, zero real secrets, deterministic.
- `source` config default is **`csv`** — Plan 1 behavior is unchanged until the user explicitly sets `live`.
- Test coverage target **≥90%** on `auth` and adapter mapping/bridge logic.
- Standalone git history in `Monarch MCP/`; clean commit per task.
- Existing Plan 1 signatures this plan depends on (verbatim):
  - `MonarchAdapter` abstract methods: `get_transactions() -> list[Transaction]`, `get_balances() -> list[Balance]`, `get_accounts() -> list[Account]`, `source_name() -> str`.
  - `Transaction(id, date, merchant, category, account, original_statement, notes, amount, tags, owner)`; `Balance(date, balance, account)`; `Account(name, latest_balance, as_of)`.
  - `Config` fields today: `db_path, log_level, log_file, write_mode, batch_cap, cache_ttls, csv_transactions_path, csv_balances_path`.
  - `DataService._get` already serves stale cache on **any** adapter `Exception`, else re-raises.

---

### Task 1: Config — add `source` and `keyring_service_name`

**Files:**
- Modify: `financial_mcp/config.py:24-32` (the `Config` model)
- Modify: `config.example.yaml`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: existing `Config`, `load_config`.
- Produces: `Config.source: Literal["csv", "live"]` (default `"csv"`); `Config.keyring_service_name: str` (default `"financial_mcp_monarch"`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:
```python
def test_source_defaults_to_csv():
    cfg = load_config(None)
    assert cfg.source == "csv"
    assert cfg.keyring_service_name == "financial_mcp_monarch"


def test_source_live_and_service_name_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("source: live\nkeyring_service_name: my_service\n")
    cfg = load_config(str(p))
    assert cfg.source == "live"
    assert cfg.keyring_service_name == "my_service"


def test_invalid_source_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("source: cloud\n")
    try:
        load_config(str(p))
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'source'`

- [ ] **Step 3: Add the fields**

In `financial_mcp/config.py`, add two fields to `Config` (after `csv_balances_path`):
```python
    csv_balances_path: str | None = None
    source: Literal["csv", "live"] = "csv"
    keyring_service_name: str = "financial_mcp_monarch"
```
(`Literal` is already imported at the top of the file.)

- [ ] **Step 4: Update the committed example config**

Append to `config.example.yaml`:
```yaml

# Data source: "csv" = offline Plan-1 exports | "live" = Monarch API.
# Run `financial-mcp-login` once before using "live". If "live" is set but no
# session is stored, the server logs a warning and falls back to CSV.
source: csv
keyring_service_name: financial_mcp_monarch
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (all config tests)

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/config.py config.example.yaml tests/test_config.py
git commit -m "feat: add source (csv|live) and keyring_service_name config"
```

---

### Task 2: Keychain token store (`auth.py`)

**Files:**
- Create: `financial_mcp/monarch/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `keyring` library.
- Produces:
  - `financial_mcp.monarch.auth.DEFAULT_SERVICE: str = "financial_mcp_monarch"`
  - `financial_mcp.monarch.auth.TOKEN_USERNAME: str = "monarch_token"`
  - `save_token(token: str, service_name: str = DEFAULT_SERVICE) -> None`
  - `load_token(service_name: str = DEFAULT_SERVICE) -> str | None` — `None` if never stored.
  - `delete_token(service_name: str = DEFAULT_SERVICE) -> None` — no-op if absent.

- [ ] **Step 1: Write the failing test**

`tests/test_auth.py`:
```python
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

    assert auth.load_token("svc") is None
    auth.save_token("tok-123", "svc")
    assert auth.load_token("svc") == "tok-123"


def test_delete_token():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    auth.save_token("tok-123", "svc")
    auth.delete_token("svc")
    assert auth.load_token("svc") is None


def test_delete_absent_is_noop():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    auth.delete_token("svc")  # must not raise
    assert auth.load_token("svc") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.monarch.auth'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/monarch/auth.py`:
```python
from __future__ import annotations

import keyring

DEFAULT_SERVICE = "financial_mcp_monarch"
TOKEN_USERNAME = "monarch_token"


def save_token(token: str, service_name: str = DEFAULT_SERVICE) -> None:
    keyring.set_password(service_name, TOKEN_USERNAME, token)


def load_token(service_name: str = DEFAULT_SERVICE) -> str | None:
    return keyring.get_password(service_name, TOKEN_USERNAME)


def delete_token(service_name: str = DEFAULT_SERVICE) -> None:
    try:
        keyring.delete_password(service_name, TOKEN_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/auth.py tests/test_auth.py
git commit -m "feat: Keychain-backed Monarch token store"
```

---

### Task 3: Live adapter (`community_adapter.py`)

**Files:**
- Create: `financial_mcp/monarch/community_adapter.py`
- Modify: `pyproject.toml:6-11` (add `monarchmoney==0.1.15` to `dependencies`)
- Test: `tests/test_community_adapter.py`

**Interfaces:**
- Consumes: `MonarchAdapter`, `Account`, `Balance`, `Transaction` (Plan 1); `monarchmoney.MonarchMoney`.
- Produces:
  - `financial_mcp.monarch.community_adapter.SessionExpiredError(RuntimeError)`
  - `CommunityAdapter(token: str, service_name: str = auth.DEFAULT_SERVICE, client_factory: Callable[[str], object] | None = None, today: Callable[[], datetime.date] = datetime.date.today)` implementing `MonarchAdapter`.
    - `source_name() -> "live"`.
    - `get_accounts()`, `get_transactions()` (paginated), `get_balances()` (derived from accounts, dated `today()`).
    - `client_factory` and `today` are injectable so tests pass a stub client and a fixed date.

**Response-shape note (read before implementing):** The field names below reflect the
`monarchmoney` v0.1.15 GraphQL responses (`get_accounts()` → `{"accounts": [...]}`;
`get_transactions()` → `{"allTransactions": {"totalCount": N, "results": [...]}}`). Task 5's
manual smoke test is where these are confirmed against the real account — if a real key differs,
fix the `_to_*` mappers here and re-run the adapter tests. The stub in Step 1 encodes exactly
this assumed shape, so the unit tests lock the contract the mappers expect.

- [ ] **Step 1: Write the failing test**

`tests/test_community_adapter.py`:
```python
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
        return {
            "accounts": [
                {"displayName": "Checking", "currentBalance": 3200.00},
                {"displayName": "Credit Card", "currentBalance": -450.00},
            ]
        }

    async def get_transactions(self, limit=100, offset=0, **kwargs):
        if self.fail_status in (401, 403):
            raise RuntimeError(f"HTTP {self.fail_status} Unauthorized")
        page = offset // 2  # StubClient serves 2 per page
        return self._pages[page] if page < len(self._pages) else {
            "allTransactions": {"totalCount": 3, "results": []}
        }


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_community_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.monarch.community_adapter'`

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, add to the `dependencies` list:
```toml
dependencies = [
    "mcp>=1.2.0",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "keyring>=25.0",
    "monarchmoney==0.1.15",
]
```
Then run: `python -m pip install -e ".[dev]"`

- [ ] **Step 4: Write minimal implementation**

`financial_mcp/monarch/community_adapter.py`:
```python
from __future__ import annotations

import asyncio
import datetime
from collections.abc import Callable
from decimal import Decimal

from financial_mcp.monarch import auth
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, Transaction


class SessionExpiredError(RuntimeError):
    """Raised when Monarch rejects the stored token (401/403). Caller should
    prompt the user to re-run `financial-mcp-login`."""


def _default_client_factory(token: str) -> object:
    from monarchmoney import MonarchMoney

    return MonarchMoney(token=token)


def _is_auth_error(exc: Exception) -> bool:
    msg = str(exc)
    return "401" in msg or "403" in msg or "Unauthorized" in msg


def _to_account(d: dict, as_of: datetime.date) -> Account:
    return Account(
        name=d["displayName"],
        latest_balance=Decimal(str(d["currentBalance"])),
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
        token: str,
        service_name: str = auth.DEFAULT_SERVICE,
        client_factory: Callable[[str], object] | None = None,
        today: Callable[[], datetime.date] = datetime.date.today,
        page_size: int = 100,
    ) -> None:
        self._token = token
        self._service_name = service_name
        self._client_factory = client_factory or _default_client_factory
        self._today = today
        self._page_size = page_size

    def source_name(self) -> str:
        return "live"

    def _run(self, coro):
        try:
            return asyncio.run(coro)
        except Exception as exc:  # noqa: BLE001 — re-typed below
            if _is_auth_error(exc):
                raise SessionExpiredError(
                    "Monarch session expired — re-run `financial-mcp-login`."
                ) from exc
            raise

    def get_accounts(self) -> list[Account]:
        client = self._client_factory(self._token)
        raw = self._run(client.get_accounts())
        as_of = self._today()
        return [_to_account(a, as_of) for a in raw.get("accounts", [])]

    def get_balances(self) -> list[Balance]:
        as_of = self._today()
        return [
            Balance(date=a.as_of, balance=a.latest_balance, account=a.name)
            for a in self.get_accounts()
        ]

    def get_transactions(self) -> list[Transaction]:
        client = self._client_factory(self._token)
        out: list[Transaction] = []
        offset = 0
        while True:
            raw = self._run(client.get_transactions(limit=self._page_size, offset=offset))
            block = raw.get("allTransactions", {})
            results = block.get("results", [])
            out.extend(_to_transaction(r) for r in results)
            offset += len(results)
            if not results or offset >= block.get("totalCount", 0):
                break
        return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_community_adapter.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/community_adapter.py tests/test_community_adapter.py pyproject.toml
git commit -m "feat: live CommunityAdapter over monarchmoney with async bridge"
```

---

### Task 4: Adapter selection in `main.py`

**Files:**
- Modify: `financial_mcp/main.py` (whole file)
- Test: `tests/test_main_adapter_selection.py`

**Interfaces:**
- Consumes: `Config`, `auth.load_token`, `CsvAdapter`, `CommunityAdapter`.
- Produces: `financial_mcp.main.build_adapter(cfg: Config) -> MonarchAdapter` — extracted so it is unit-testable without running the stdio server. Selection logic:
  - `cfg.source == "live"`: load token; if present → `CommunityAdapter`; else log warning + fall back to `_csv_or_exit(cfg)`.
  - `cfg.source == "csv"`: `_csv_or_exit(cfg)`.
  - `_csv_or_exit(cfg)` → `CsvAdapter` if both CSV paths set, else `SystemExit` with instruction.

- [ ] **Step 1: Write the failing test**

`tests/test_main_adapter_selection.py`:
```python
import pytest

from financial_mcp.config import Config
from financial_mcp.main import build_adapter
from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter


def test_csv_source_builds_csv_adapter():
    cfg = Config(source="csv", csv_transactions_path="t.csv", csv_balances_path="b.csv")
    assert isinstance(build_adapter(cfg), CsvAdapter)


def test_csv_source_without_paths_exits():
    cfg = Config(source="csv")
    with pytest.raises(SystemExit):
        build_adapter(cfg)


def test_live_with_token_builds_community_adapter(monkeypatch):
    monkeypatch.setattr("financial_mcp.main.auth.load_token", lambda service: "tok-abc")
    cfg = Config(source="live")
    adapter = build_adapter(cfg)
    assert isinstance(adapter, CommunityAdapter)


def test_live_without_token_falls_back_to_csv(monkeypatch, caplog):
    monkeypatch.setattr("financial_mcp.main.auth.load_token", lambda service: None)
    cfg = Config(source="live", csv_transactions_path="t.csv", csv_balances_path="b.csv")
    import logging

    with caplog.at_level(logging.WARNING, logger="financial_mcp"):
        adapter = build_adapter(cfg)
    assert isinstance(adapter, CsvAdapter)
    assert any("financial-mcp-login" in r.message for r in caplog.records)


def test_live_without_token_and_no_csv_exits(monkeypatch):
    monkeypatch.setattr("financial_mcp.main.auth.load_token", lambda service: None)
    cfg = Config(source="live")
    with pytest.raises(SystemExit):
        build_adapter(cfg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main_adapter_selection.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_adapter' from 'financial_mcp.main'`

- [ ] **Step 3: Rewrite `main.py`**

`financial_mcp/main.py`:
```python
from __future__ import annotations

import logging
import os

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import Config, load_config
from financial_mcp.data_service import DataService
from financial_mcp.mcp_server import build_server
from financial_mcp.monarch import auth
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter
from financial_mcp.utils.logging import configure_logging

logger = logging.getLogger("financial_mcp")


def _csv_or_exit(cfg: Config) -> MonarchAdapter:
    if not cfg.csv_transactions_path or not cfg.csv_balances_path:
        raise SystemExit(
            "No usable data source. Set source=live and run `financial-mcp-login`, "
            "or set csv_transactions_path and csv_balances_path in config.yaml."
        )
    return CsvAdapter(cfg.csv_transactions_path, cfg.csv_balances_path)


def build_adapter(cfg: Config) -> MonarchAdapter:
    if cfg.source == "live":
        token = auth.load_token(cfg.keyring_service_name)
        if token:
            return CommunityAdapter(token, cfg.keyring_service_name)
        logger.warning(
            "no Monarch session — run `financial-mcp-login`; falling back to CSV"
        )
        return _csv_or_exit(cfg)
    return _csv_or_exit(cfg)


def main() -> None:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    configure_logging(cfg)

    adapter = build_adapter(cfg)
    cache = SqliteCache(cfg.db_path, cfg.cache_ttls)
    svc = DataService(adapter, cache)
    logger.info("financial-mcp starting (source=%s)", adapter.source_name())

    server = build_server(svc)
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_main_adapter_selection.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/main.py tests/test_main_adapter_selection.py
git commit -m "feat: source-driven adapter selection with visible CSV fallback"
```

---

### Task 5: Login CLI, session-expiry tool message, docs & git-ignore

**Files:**
- Create: `financial_mcp/login_cli.py`
- Modify: `financial_mcp/mcp_server.py` (map `SessionExpiredError` to a plain message)
- Modify: `.gitignore` (add `.mm/`)
- Modify: `docs/INSTALL.md` (Plan 2 section)
- Test: `tests/test_mcp_tools.py` (append one test for the expiry message)

**Interfaces:**
- Consumes: `auth`, `monarchmoney.MonarchMoney`, `RequireMFAException`; `SessionExpiredError`; `DataService`.
- Produces: `financial_mcp.login_cli.main() -> None` (entry point `financial-mcp-login`, already declared in `pyproject.toml`). Interactive; not unit-tested (covered by the manual smoke test).

- [ ] **Step 1: Write the failing test (tool maps expiry to a clean message)**

Append to `tests/test_mcp_tools.py`:
```python
def test_get_accounts_reports_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(FakeAdapter):
        def get_accounts(self):
            raise SessionExpiredError("expired")

    from financial_mcp.cache.sqlite_cache import SqliteCache
    from financial_mcp.config import CacheTTLs
    from financial_mcp.data_service import DataService

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "get_accounts")()
    assert isinstance(result, dict)
    assert "financial-mcp-login" in result["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_tools.py::test_get_accounts_reports_expired_session -v`
Expected: FAIL — `get_accounts` raises `SessionExpiredError` instead of returning an error dict.

- [ ] **Step 3: Handle the typed error in the tool layer**

In `financial_mcp/mcp_server.py`, add the import at the top:
```python
from financial_mcp.monarch.community_adapter import SessionExpiredError
```
Then wrap the `get_accounts` tool body:
```python
    @server.tool()
    def get_accounts() -> list[dict] | dict:
        """List financial accounts with their latest known balance."""
        try:
            return _dump(svc.accounts())
        except SessionExpiredError as exc:
            return {"error": str(exc)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_tools.py -v`
Expected: PASS (all mcp-tool tests)

- [ ] **Step 5: Write the login CLI**

`financial_mcp/login_cli.py`:
```python
from __future__ import annotations

import asyncio
import getpass
import sys

from financial_mcp.config import load_config
import os

from financial_mcp.monarch import auth


def _service_name() -> str:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    return cfg.keyring_service_name


async def _validate_token(token: str) -> bool:
    from monarchmoney import MonarchMoney

    mm = MonarchMoney(token=token)
    try:
        await mm.get_accounts()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"That token did not work: {exc}", file=sys.stderr)
        return False


async def _login_with_password(email: str, password: str) -> str | None:
    from monarchmoney import MonarchMoney, RequireMFAException

    mm = MonarchMoney()
    try:
        await mm.login(email, password)
    except RequireMFAException:
        code = input("MFA code: ").strip()
        await mm.multi_factor_authenticate(email, password, code)
    return mm.token


def _token_path() -> None:
    token = getpass.getpass(
        "Paste your Monarch token (from the `Authorization: Token XXXX` "
        "request header in browser DevTools): "
    ).strip()
    if token.lower().startswith("token "):
        token = token[len("token ") :].strip()
    if asyncio.run(_validate_token(token)):
        auth.save_token(token, _service_name())
        print("Session saved to Keychain.")
    else:
        raise SystemExit("Login failed. Try again.")


def _password_path() -> None:
    email = input("Monarch email: ").strip()
    password = getpass.getpass("Monarch password (not stored): ")
    token = asyncio.run(_login_with_password(email, password))
    if not token:
        raise SystemExit("Login failed — no token returned.")
    auth.save_token(token, _service_name())
    print("Session saved to Keychain.")


def main() -> None:
    if "--logout" in sys.argv:
        auth.delete_token(_service_name())
        print("Logged out — token removed from Keychain.")
        return
    print("Monarch login. Choose a method:")
    print("  1) Paste browser token (recommended)")
    print("  2) Email + password + MFA")
    choice = input("> ").strip()
    if choice == "2":
        _password_path()
    else:
        _token_path()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Git-ignore the library's session pickle**

In `.gitignore`, under the "Local cache / secrets" section, add:
```
.mm/
```

- [ ] **Step 7: Document the Plan 2 flow**

Append to `docs/INSTALL.md`:
````markdown

## Plan 2 — Live Monarch sync

### 1. Log in once (terminal, not Claude)
```bash
financial-mcp-login
```
Choose **1** and paste the token from the `Authorization: Token XXXX` request
header in a logged-in Monarch browser tab (DevTools → Network → any
`api.monarch.com` request). Or choose **2** for email + password + MFA. Only the
resulting session token is stored, in your macOS Keychain — never your password.

To log out: `financial-mcp-login --logout`.

### 2. Switch config to live
In `config.yaml`:
```yaml
source: live
```

### 3. Manual smoke test (once)
```bash
FINANCIAL_MCP_CONFIG=config.yaml python -c "
from financial_mcp.config import load_config
from financial_mcp.monarch import auth
from financial_mcp.monarch.community_adapter import CommunityAdapter
cfg = load_config('config.yaml')
a = CommunityAdapter(auth.load_token(cfg.keyring_service_name), cfg.keyring_service_name)
print('accounts:', [x.name for x in a.get_accounts()])
print('transactions:', len(a.get_transactions()))
"
```
Expected: your real account names and a transaction count. **If the field names
in the output look wrong**, the real GraphQL response shape differs from the
mappers in `community_adapter.py` — adjust `_to_account` / `_to_transaction`
there and re-run `pytest tests/test_community_adapter.py`.

If the token has expired, tools return a clear "re-run `financial-mcp-login`"
message and the server keeps serving cached data with its age.
````

- [ ] **Step 8: Run the full suite and lint**

Run: `python -m pytest -q && python -m ruff check financial_mcp tests && python -m black --check financial_mcp tests`
Expected: all tests PASS; ruff clean; black reports no changes (run `python -m black financial_mcp tests` first if needed, then re-commit).

- [ ] **Step 9: Commit**

```bash
git add financial_mcp/login_cli.py financial_mcp/mcp_server.py .gitignore docs/INSTALL.md tests/test_mcp_tools.py
git commit -m "feat: login CLI, session-expiry tool message, Plan 2 install docs"
```

---

## Self-Review

**Spec coverage (Plan 2 design doc §1–§11):**
- §3 decision 1 (scope: accounts/transactions/balances only) → Task 3 maps exactly these three ✅
- §3 decision 2 + §2.1 (pin `monarchmoney==0.1.15`, wrapped only in adapter + login_cli) → Task 3 Step 3, Global Constraints ✅
- §3 decision 3 (`source: csv|live`, default csv, visible fallback) → Task 1 (config) + Task 4 (selection + logged warning) ✅
- §3 decision 4 (async→sync bridge inside adapter) → Task 3 `_run`/`asyncio.run` ✅
- §3 decision 5 (mocked client + fake keyring + manual smoke test) → Task 2 (fake keyring), Task 3 (stub client), Task 5 Step 7 (smoke test) ✅
- §4 `auth.py` save/load/delete → Task 2 ✅
- §4 `community_adapter.py` mapping isolation + typed `SessionExpiredError` → Task 3 (`_to_*`, `SessionExpiredError`) ✅
- §4 `login_cli.py` token-paste primary + email/pw/MFA fallback, no TOTP seed stored, `--logout` → Task 5 Step 5 ✅
- §4 config additions + example.yaml → Task 1 ✅
- §4 `main.py` selection → Task 4 ✅
- §2.2 (session is token-only) → Task 2 stores one secret ✅
- §6 error handling (expired → typed error → tool message; stale-serve via existing DataService) → Task 5 Steps 1–4; DataService stale path is Plan 1 (unchanged) ✅
- §9 git discipline (`.mm/` ignored, no secrets committed, commit per task) → Task 5 Step 6, per-task commits ✅
- §8 coverage ≥90% on auth + adapter mapping → Tasks 2 & 3 test suites ✅
- **Deferred (intentional, per §11):** richer entities, `get_account_history`, `transport.py`, writes.

**Placeholder scan:** No TBD/TODO. Every code step is complete and runnable. The one honest
verification checkpoint — "confirm real GraphQL field names via smoke test" — is explicit in
Task 3's response-shape note and Task 5 Step 7, with exact remediation instructions, not a
hand-wave.

**Type consistency:** `MonarchAdapter` methods (`get_transactions`/`get_balances`/`get_accounts`/
`source_name`) match Plan 1 and Task 3. `SessionExpiredError` is defined in Task 3 and imported
identically in Task 4 (via CommunityAdapter) and Task 5 (mcp_server). `auth.DEFAULT_SERVICE`,
`save_token`/`load_token`/`delete_token` signatures match across Tasks 2, 4, 5. `build_adapter`,
`_csv_or_exit` names match between Task 4 impl and its test. `Config.source` /
`keyring_service_name` match across Tasks 1, 4, 5. `CommunityAdapter` constructor kwargs
(`client_factory`, `today`, `page_size`) match its test in Task 3.
