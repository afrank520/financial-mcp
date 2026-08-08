# Balance History Source + as-of / delta Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-account balance-history source from Monarch plus two thin read tools — `balance_as_of` (carry-forward + provenance) and `balance_delta` (server-computed, penny-exact) — as the verified foundation for a later savings-rate spec and HELOC glide-path tracking.

**Architecture:** Slots into the existing one-directional layered pattern (Monarch GraphQL → Adapter → DataService+cache → pure analytics → MCP tools). One new GraphQL query (verified live FIRST, before anything is built on it), one new Pydantic model, one adapter method, one pure analytics module, two thin MCP tools. Mirrors the shipped `date_range_summary` slice.

**Tech Stack:** Python 3.12, Pydantic v2, `mcp.server.fastmcp`, `aiohttp` (adapter transport), SQLite cache, `pytest`, `uv` for running.

## Global Constraints

- Money is `Decimal` everywhere; JSON transport serializes Decimals to strings via the existing `_jsonify` / `model_dump(mode="json")` — never float. (verbatim principle from shipped code)
- Analytics modules are **pure** (no I/O), mirroring `financial_mcp/analytics/spending.py`.
- Balance sign = as Monarch reports it — liabilities negative, **not** flipped.
- Delta sign = `end − start` (HELOC paydown surfaces as a positive delta).
- As-of gap policy = **carry-forward with provenance**: return the last snapshot on-or-before the requested date, plus the real `snapshot_date` and `is_carry_forward`; date before an account's first snapshot → `null` + `reason`.
- New cache `data_type` string is `balance_history` and MUST match a field name on `config.CacheTTLs` (because `SqliteCache._ttl_for` does `getattr(self._ttls, data_type, 0)`; a missing field silently means TTL 0 / always-live, which is wrong for slow-moving history).
- `MonarchAdapter` is a strict `abc.ABC`. Adding a new `@abc.abstractmethod` means EVERY concrete subclass — including the test fakes (`FakeAdapter` in `tests/test_mcp_tools.py` and `tests/test_data_service.py`, whose subclasses `ManyTxnAdapter`/`WindowAdapter`/inline `ExpiredAdapter`/`BlockedAdapter` inherit) — must implement it or fail to instantiate. Update those fakes in the same task that adds the abstract method.
- MCP tools are invoked in tests **synchronously** via the helper `_tool(server, name)` = `server._tool_manager._tools[name].fn`, then called directly: `_tool(server, "x")(arg=...)`. No `asyncio` in tool tests.
- The freshness envelope returned by `_envelope(...)` has keys `{as_of, age_hours, stale, count, data}` — there is NO `data_type` key in the output. `_envelope`'s first argument is only the cache key used to look up freshness.
- Tests are offline/deterministic — no live Monarch calls in the pytest suite. The single live check is Task 1, run interactively.
- Run tests with `uv run pytest`.
- Commit after every task.

---

### Task 1: Live discovery — lock the Monarch balance-history query shape

**This task is done interactively against Adam's real org (session in Keychain). It is NOT a pytest task and NOT a subagent task — it produces a confirmed fact the rest of the plan consumes. Do it FIRST; do not build Tasks 2–7 on an unconfirmed query.**

**Files:**
- Create (throwaway, deleted at end of task): `scripts/probe_balance_history.py`
- Modify: `docs/specs/2026-08-08-monarch-mcp-balance-history-design.md` (record the confirmed shape in a new "Confirmed query shape" subsection under §5)

**Interfaces:**
- Consumes: existing `financial_mcp.monarch.auth` (session load), `CommunityAdapter._default_transport` pattern, `GRAPHQL_URL`, `USER_AGENT`.
- Produces (for Tasks 2, 5): the confirmed GraphQL query string to name `GET_BALANCE_HISTORY`, and the exact JSON path to `{account_name, date, balance}` per historical point.

- [ ] **Step 1: Write a probe script that tries candidate queries and prints raw JSON**

The Monarch community library exposes account history two ways; we try the per-account-snapshots candidate first, then the recent-balances fallback, and keep whichever returns real dated per-account balances including the HELOC.

```python
# scripts/probe_balance_history.py  (throwaway — deleted in Step 4)
import asyncio, json
from financial_mcp.monarch import auth
from financial_mcp.monarch.community_adapter import CommunityAdapter

# Candidate A: per-account snapshots (preferred — carries explicit dates)
CANDIDATE_A = """
  query GetAccountHistory($startDate: Date!) {
    accounts {
      id
      displayName
      recentBalances(startDate: $startDate)
      __typename
    }
  }
"""

# Candidate B: aggregate snapshots by account type (fallback / shape check)
CANDIDATE_B = """
  query GetSnapshotsByAccount($startDate: Date!, $timeframe: Timeframe!) {
    snapshotsByAccountType(startDate: $startDate, timeframe: $timeframe) {
      accountType
      month
      balance
      __typename
    }
  }
"""

async def main():
    session = auth.load_session()  # confirm the real accessor name in auth.py
    adapter = CommunityAdapter(session)
    for name, q, vars in [
        ("A recentBalances", CANDIDATE_A, {"startDate": "2026-01-01"}),
        ("B snapshotsByAccountType", CANDIDATE_B, {"startDate": "2026-01-01", "timeframe": "month"}),
    ]:
        try:
            data = await adapter._default_transport("Probe", q, vars)
            print(f"\n===== {name} =====")
            print(json.dumps(data, indent=2)[:3000])
        except Exception as exc:
            print(f"\n===== {name} FAILED: {type(exc).__name__}: {exc}")

asyncio.run(main())
```

- [ ] **Step 2: Run the probe and read the raw shapes**

Run: `uv run python scripts/probe_balance_history.py`
Expected: at least one candidate returns real data. Confirm you can see, per account (esp. the HELOC): an identifiable account name and a series of `{date, balance}` points. Note `recentBalances` may return a bare array indexed by day from `startDate` (map index→date) OR objects with explicit dates — record which.

- [ ] **Step 3: Record the confirmed shape in the spec**

In `docs/specs/2026-08-08-monarch-mcp-balance-history-design.md`, add under §5:

```markdown
### 5.1 Confirmed query shape (filled in Task 1, YYYY-MM-DD)
- Winning query: [paste the exact confirmed GraphQL string]
- Operation name: [e.g. GetAccountHistory]
- Variables: [e.g. {"startDate": "YYYY-MM-DD"}]
- JSON path to points: [e.g. data.accounts[*].recentBalances → indexed daily from startDate]
- Account name field: [e.g. accounts[*].displayName]
- Balance field + sign: [e.g. signed; liabilities negative — CONFIRMED against HELOC]
- Date derivation: [explicit date field, OR index-from-startDate]
```

- [ ] **Step 4: Delete the probe script and commit the confirmed shape**

```bash
rm scripts/probe_balance_history.py
git add docs/specs/2026-08-08-monarch-mcp-balance-history-design.md
git commit -m "docs: confirm Monarch balance-history query shape (live-verified)"
```

**If BOTH candidates fail or neither returns dated per-account balances:** STOP and report to the user before proceeding — the design's core assumption (Monarch exposes per-account history) is wrong and the spec needs revisiting. Do not build downstream tasks on a shape you could not confirm.

---

### Task 2: `BalanceSnapshot` model

**Files:**
- Modify: `financial_mcp/monarch/models.py`
- Test: `tests/test_models.py` (append; file exists)

**Interfaces:**
- Consumes: nothing (leaf model).
- Produces: `BalanceSnapshot(account: str, date: datetime.date, balance: Decimal)` — used by Tasks 3, 4, 5, 6.

- [ ] **Step 1: Write the failing test**

Add `BalanceSnapshot` to the existing `from financial_mcp.monarch.models import Account, Balance, Transaction` line in `tests/test_models.py` (`datetime` and `Decimal` are already imported there), then append:

```python
def test_balance_snapshot_fields_and_json_roundtrip():
    s = BalanceSnapshot(account="HELOC", date=datetime.date(2026, 6, 28), balance=Decimal("-202143.55"))
    assert s.account == "HELOC"
    assert s.date == datetime.date(2026, 6, 28)
    assert s.balance == Decimal("-202143.55")
    dumped = s.model_dump(mode="json")
    assert dumped == {"account": "HELOC", "date": "2026-06-28", "balance": "-202143.55"}
    # reconstructable from cached JSON exactly as DataService._get does (model(**d))
    assert BalanceSnapshot(**dumped) == s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_balance_snapshot_fields_and_json_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'BalanceSnapshot'`.

- [ ] **Step 3: Write minimal implementation**

Append to `financial_mcp/monarch/models.py`:

```python
class BalanceSnapshot(BaseModel):
    account: str
    date: datetime.date
    balance: Decimal
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py::test_balance_snapshot_fields_and_json_roundtrip -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/models.py tests/test_models.py
git commit -m "feat: add BalanceSnapshot model"
```

---

### Task 3: `balance_as_of` analytics (carry-forward + provenance)

**Files:**
- Create: `financial_mcp/analytics/balances.py`
- Test: `tests/test_balances_analytics.py`

**Interfaces:**
- Consumes: `BalanceSnapshot` (Task 2).
- Produces: `balance_as_of(snapshots: list[BalanceSnapshot], target_date: datetime.date) -> list[dict]`. Each dict: `{account, balance: Decimal|None, as_of_requested: str, snapshot_date: str|None, is_carry_forward: bool, reason?: str}`. Rows sorted by account name. Used by Task 4 (delta reuses it) and Task 7 (tool).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_balances_analytics.py
import datetime
from decimal import Decimal
from financial_mcp.monarch.models import BalanceSnapshot
from financial_mcp.analytics.balances import balance_as_of

D = datetime.date

def _snaps():
    return [
        BalanceSnapshot(account="HELOC", date=D(2026, 5, 31), balance=Decimal("-224600.00")),
        BalanceSnapshot(account="HELOC", date=D(2026, 6, 28), balance=Decimal("-202143.55")),
        BalanceSnapshot(account="Brokerage", date=D(2026, 6, 15), balance=Decimal("50000.00")),
    ]

def test_exact_hit_is_not_carry_forward():
    rows = balance_as_of(_snaps(), D(2026, 6, 28))
    heloc = next(r for r in rows if r["account"] == "HELOC")
    assert heloc["balance"] == Decimal("-202143.55")
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is False
    assert heloc["as_of_requested"] == "2026-06-28"

def test_gap_carries_forward_last_known_with_provenance():
    rows = balance_as_of(_snaps(), D(2026, 6, 30))  # weekend gap after 6/28
    heloc = next(r for r in rows if r["account"] == "HELOC")
    assert heloc["balance"] == Decimal("-202143.55")
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is True

def test_before_first_snapshot_is_null_with_reason():
    rows = balance_as_of(_snaps(), D(2026, 6, 1))  # before Brokerage's 6/15
    bro = next(r for r in rows if r["account"] == "Brokerage")
    assert bro["balance"] is None
    assert bro["snapshot_date"] is None
    assert "reason" in bro

def test_rows_cover_all_accounts_sorted():
    rows = balance_as_of(_snaps(), D(2026, 6, 30))
    assert [r["account"] for r in rows] == ["Brokerage", "HELOC"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_balances_analytics.py -v`
Expected: FAIL with `ModuleNotFoundError: financial_mcp.analytics.balances`.

- [ ] **Step 3: Write minimal implementation**

```python
# financial_mcp/analytics/balances.py
from __future__ import annotations

import datetime
from collections import defaultdict

from financial_mcp.monarch.models import BalanceSnapshot


def _as_of_one(points: list[BalanceSnapshot], target: datetime.date) -> BalanceSnapshot | None:
    """Last snapshot on-or-before target (points assumed same account)."""
    eligible = [p for p in points if p.date <= target]
    if not eligible:
        return None
    return max(eligible, key=lambda p: p.date)


def balance_as_of(snapshots: list[BalanceSnapshot], target_date: datetime.date) -> list[dict]:
    by_account: dict[str, list[BalanceSnapshot]] = defaultdict(list)
    for s in snapshots:
        by_account[s.account].append(s)
    rows: list[dict] = []
    for account in sorted(by_account):
        chosen = _as_of_one(by_account[account], target_date)
        if chosen is None:
            rows.append({
                "account": account,
                "balance": None,
                "as_of_requested": target_date.isoformat(),
                "snapshot_date": None,
                "is_carry_forward": False,
                "reason": "no snapshot on or before requested date",
            })
        else:
            rows.append({
                "account": account,
                "balance": chosen.balance,
                "as_of_requested": target_date.isoformat(),
                "snapshot_date": chosen.date.isoformat(),
                "is_carry_forward": chosen.date != target_date,
            })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_balances_analytics.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/analytics/balances.py tests/test_balances_analytics.py
git commit -m "feat: add balance_as_of analytics (carry-forward + provenance)"
```

---

### Task 4: `balance_delta` analytics (server-computed)

**Files:**
- Modify: `financial_mcp/analytics/balances.py`
- Test: `tests/test_balances_analytics.py` (append)

**Interfaces:**
- Consumes: `balance_as_of` (Task 3), `BalanceSnapshot` (Task 2).
- Produces: `balance_delta(snapshots: list[BalanceSnapshot], start: datetime.date, end: datetime.date) -> list[dict]`. Each dict: `{account, start: {balance, snapshot_date, is_carry_forward}|null-ish, end: {...}, delta: Decimal|None, reason?: str}`. `delta = end.balance - start.balance`; if either endpoint balance is None → `delta: None` + `reason`. Used by Task 7 (tool).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_balances_analytics.py  (append)
from financial_mcp.analytics.balances import balance_delta

def test_delta_heloc_paydown_is_positive():
    rows = balance_delta(_snaps(), D(2026, 5, 31), D(2026, 6, 28))
    heloc = next(r for r in rows if r["account"] == "HELOC")
    # -202143.55 - (-224600.00) = +22456.45 (paydown surfaces positive)
    assert heloc["delta"] == Decimal("22456.45")
    assert heloc["start"]["balance"] == Decimal("-224600.00")
    assert heloc["end"]["balance"] == Decimal("-202143.55")

def test_delta_null_when_endpoint_missing():
    rows = balance_delta(_snaps(), D(2026, 6, 1), D(2026, 6, 28))  # Brokerage has no 6/1 start
    bro = next(r for r in rows if r["account"] == "Brokerage")
    assert bro["delta"] is None
    assert "reason" in bro
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_balances_analytics.py -v`
Expected: FAIL with `ImportError: cannot import name 'balance_delta'`.

- [ ] **Step 3: Write minimal implementation**

Append to `financial_mcp/analytics/balances.py`:

```python
def balance_delta(
    snapshots: list[BalanceSnapshot], start: datetime.date, end: datetime.date
) -> list[dict]:
    start_rows = {r["account"]: r for r in balance_as_of(snapshots, start)}
    end_rows = {r["account"]: r for r in balance_as_of(snapshots, end)}
    rows: list[dict] = []
    for account in sorted(set(start_rows) | set(end_rows)):
        s = start_rows.get(account)
        e = end_rows.get(account)
        s_bal = None if s is None else s["balance"]
        e_bal = None if e is None else e["balance"]
        row = {
            "account": account,
            "start": None if s is None else {
                "balance": s["balance"],
                "snapshot_date": s["snapshot_date"],
                "is_carry_forward": s["is_carry_forward"],
            },
            "end": None if e is None else {
                "balance": e["balance"],
                "snapshot_date": e["snapshot_date"],
                "is_carry_forward": e["is_carry_forward"],
            },
        }
        if s_bal is None or e_bal is None:
            row["delta"] = None
            row["reason"] = "missing balance at one or both endpoints"
        else:
            row["delta"] = e_bal - s_bal
        rows.append(row)
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_balances_analytics.py -v`
Expected: PASS (6 tests total in the file).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/analytics/balances.py tests/test_balances_analytics.py
git commit -m "feat: add balance_delta analytics (server-computed, end-minus-start)"
```

---

### Task 5: Adapter — `get_balance_history()` on interface, CommunityAdapter, CsvAdapter

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (add abstract method)
- Modify: `financial_mcp/monarch/queries.py` (add `GET_ACCOUNTS_MIN` + `GET_ACCOUNT_SNAPSHOTS` — the strings CONFIRMED in Task 1)
- Modify: `financial_mcp/monarch/community_adapter.py` (implement fan-out)
- Modify: `financial_mcp/monarch/csv_adapter.py` (return `[]`)
- Modify: `tests/test_mcp_tools.py` and `tests/test_data_service.py` — add `get_balance_history` to their `FakeAdapter` classes (else the new abstract method makes them un-instantiable and the whole suite breaks).
- Test: `tests/test_community_adapter.py` (append), `tests/test_csv_adapter.py` (append)

**Interfaces:**
- Consumes: `BalanceSnapshot` (Task 2); the confirmed `snapshotsForAccount` shape (spec §5.1); existing `CommunityAdapter._transport`, `_run`, `_default_transport`.
- Produces: `MonarchAdapter.get_balance_history(self) -> list[BalanceSnapshot]` on all adapters. Used by Task 6.

> **CONFIRMED QUERY SHAPE (Task 1 live-verified — see spec §5.1):** the winning source is `snapshotsForAccount(accountId:)`, which returns explicit dated `{date, signedBalance}` pairs (NOT the dateless `recentBalances` array that the first probe tried). `get_balance_history` is a **two-step fan-out**: (1) fetch the account list `{id, displayName}`, (2) one `snapshotsForAccount` call per account, tagging each snapshot with that account's `displayName`. Dates come straight from the `date` field — no derivation, no `startDate`, no back-counting. `signedBalance` is already signed as Monarch reports (liabilities negative).

> **CRITICAL ORDERING:** `MonarchAdapter` is a strict `abc.ABC` (see `tests/test_adapter_interface.py`). The moment the abstract method is added, any concrete subclass lacking `get_balance_history` raises `TypeError` on instantiation. The existing test fakes are `FakeAdapter` in `tests/test_mcp_tools.py` (subclassed by `ManyTxnAdapter`, `WindowAdapter`, and inline `ExpiredAdapter`/`BlockedAdapter`) and a separate `FakeAdapter` in `tests/test_data_service.py`. Both must gain a `get_balance_history` stub **in this task's Step 3**, or the whole suite fails to collect.

- [ ] **Step 1: Write the failing tests (stub transport → deterministic, offline)**

The stub mimics the real two-step fan-out: `GetAccountsMin` returns the account list, then `SnapshotsForAccount` is called once per account id.

```python
# tests/test_community_adapter.py  (append)
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
```

(Add `from financial_mcp.monarch.models import BalanceSnapshot` near the top of `tests/test_community_adapter.py` — that file currently imports nothing from models. `datetime`, `Decimal`, `CommunityAdapter`, and `SESSION` are already imported there.)

```python
# tests/test_csv_adapter.py  (append)
def test_csv_adapter_balance_history_is_empty():
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    # get_balance_history reads no files, so paths need not exist
    adapter = CsvAdapter(transactions_path="x", balances_path="y")
    assert adapter.get_balance_history() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_community_adapter.py -k get_balance_history tests/test_csv_adapter.py::test_csv_adapter_balance_history_is_empty -v`
Expected: FAIL — `AttributeError`/`ImportError` (no `get_balance_history`, no `BalanceSnapshot` import).

- [ ] **Step 3: Write minimal implementation (adapter + queries + fakes, all together)**

Add to `financial_mcp/monarch/adapter.py` — update its import line and add the abstract method:

```python
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Transaction
...
    @abc.abstractmethod
    def get_balance_history(self) -> list[BalanceSnapshot]: ...
```

Add a stub to BOTH test fakes so they remain instantiable:

```python
# in tests/test_mcp_tools.py — inside class FakeAdapter (subclasses inherit it)
    def get_balance_history(self):
        return []

# in tests/test_data_service.py — inside class FakeAdapter
    def get_balance_history(self):
        return []
```

Add to `financial_mcp/monarch/queries.py` (the CONFIRMED strings from Task 1 / spec §5.1 — operation name MUST match the document, or Monarch returns HTTP 400):

```python
GET_ACCOUNTS_MIN = """
  query GetAccountsMin {
    accounts {
      id
      displayName
      __typename
    }
  }
"""

GET_ACCOUNT_SNAPSHOTS = """
  query SnapshotsForAccount($id: UUID!) {
    snapshots: snapshotsForAccount(accountId: $id) {
      date
      signedBalance
      __typename
    }
  }
"""
```

Add to `financial_mcp/monarch/community_adapter.py` (import `BalanceSnapshot` and the two new queries):

```python
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Transaction
from financial_mcp.monarch.queries import (
    GET_ACCOUNT_SNAPSHOTS,
    GET_ACCOUNTS,
    GET_ACCOUNTS_MIN,
    GET_TRANSACTIONS,
)

    def get_balance_history(self) -> list[BalanceSnapshot]:
        accts = self._run(self._transport("GetAccountsMin", GET_ACCOUNTS_MIN, {}))
        out: list[BalanceSnapshot] = []
        for acct in accts.get("accounts", []):
            name = acct.get("displayName", "")
            data = self._run(
                self._transport("SnapshotsForAccount", GET_ACCOUNT_SNAPSHOTS, {"id": acct["id"]})
            )
            for snap in data.get("snapshots") or []:
                out.append(BalanceSnapshot(
                    account=name,
                    date=datetime.date.fromisoformat(snap["date"]),
                    balance=Decimal(str(snap["signedBalance"])),
                ))
        out.sort(key=lambda s: (s.account, s.date))
        return out
```

Add to `financial_mcp/monarch/csv_adapter.py` (import `BalanceSnapshot`):

```python
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Transaction
...
    def get_balance_history(self) -> list[BalanceSnapshot]:
        return []
```

- [ ] **Step 4: Run the full suite (all adapters must still satisfy the interface)**

Run: `uv run pytest -v`
Expected: PASS. (An abstract method added to `MonarchAdapter` means any adapter missing `get_balance_history` fails to instantiate — this step catches that across all tests.)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/queries.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/csv_adapter.py tests/test_community_adapter.py tests/test_csv_adapter.py tests/test_mcp_tools.py tests/test_data_service.py
git commit -m "feat: adapter get_balance_history (live query + CSV empty stub)"
```

---

### Task 6: DataService + cache TTL wiring

**Files:**
- Modify: `financial_mcp/config.py` (add `CacheTTLs.balance_history`)
- Modify: `financial_mcp/data_service.py` (TypeVar bound, `balance_history()` method, `refresh()` branch)
- Test: `tests/test_data_service.py` (append; file exists)

**Interfaces:**
- Consumes: `get_balance_history()` (Task 5), `BalanceSnapshot` (Task 2).
- Produces: `DataService.balance_history(self) -> list[BalanceSnapshot]` (cached under data_type `"balance_history"`). Used by Task 7.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data_service.py  (append; create with imports if absent)
import datetime
from decimal import Decimal
from financial_mcp.config import CacheTTLs
from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.data_service import DataService
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Transaction

class _StubAdapter(MonarchAdapter):
    def __init__(self, snaps): self._snaps = snaps; self.calls = 0
    def source_name(self): return "csv"
    def get_transactions(self): return []
    def get_balances(self): return []
    def get_accounts(self): return []
    def get_balance_history(self):
        self.calls += 1
        return self._snaps

def test_balance_history_is_served_and_cached(tmp_path):
    snaps = [BalanceSnapshot(account="HELOC", date=datetime.date(2026, 6, 28), balance=Decimal("-202143.55"))]
    adapter = _StubAdapter(snaps)
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)
    first = svc.balance_history()
    assert first == snaps
    # second call within TTL is served from cache (adapter not hit again)
    second = svc.balance_history()
    assert second == snaps
    assert adapter.calls == 1
    # reconstructed from JSON cache exactly (Decimal + date survive round-trip)
    assert second[0].balance == Decimal("-202143.55")

def test_cachettls_has_balance_history_field():
    assert isinstance(CacheTTLs().balance_history, int)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data_service.py -v`
Expected: FAIL — `AttributeError: 'DataService' object has no attribute 'balance_history'` (and `CacheTTLs` has no `balance_history`).

- [ ] **Step 3: Write minimal implementation**

In `financial_mcp/config.py`, add to `CacheTTLs` (history changes slowly — DAY default):

```python
    balance_history: int = DAY
```

In `financial_mcp/data_service.py`: widen the TypeVar, import the model, add the method and the refresh branch:

```python
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Transaction

T = TypeVar("T", Account, Balance, BalanceSnapshot, Transaction)
...
    def balance_history(self) -> list[BalanceSnapshot]:
        return self._get("balance_history", self._adapter.get_balance_history, BalanceSnapshot)
```

In `refresh()`, extend the default list and the dispatch:

```python
        types = [data_type] if data_type else [
            "transactions", "balances", "accounts", "balance_history"
        ]
        ...
            elif t == "accounts":
                self.accounts()
            elif t == "balance_history":
                self.balance_history()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_data_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/config.py financial_mcp/data_service.py tests/test_data_service.py
git commit -m "feat: DataService.balance_history + balance_history cache TTL"
```

---

### Task 7: MCP tools — `balance_as_of` and `balance_delta`

**Files:**
- Modify: `financial_mcp/mcp_server.py`
- Test: `tests/test_mcp_tools.py` (append; file exists) — invoke tools with the existing synchronous `_tool(server, name)` helper.

**Interfaces:**
- Consumes: `DataService.balance_history()` (Task 6); `balances.balance_as_of` / `balances.balance_delta` (Tasks 3–4); existing `_guard`, `_envelope`, `_jsonify`.
- Produces: two `@server.tool()` functions returning the standard envelope `{as_of, age_hours, stale, count, data}`.

> **REPO IDIOM (verified in `tests/test_mcp_tools.py`):** tools are called synchronously, NOT via async. The helper already in that file is:
> ```python
> def _tool(server, name):
>     return server._tool_manager._tools[name].fn
> ```
> and tools are called like `_tool(server, "spending_summary")(start_date="...", end_date="...")`. There is no `data_type` key in the envelope; assert on `result["data"]`. Reuse the existing `FakeAdapter`/`_tool` helpers already imported at the top of the file — do NOT redefine `MonarchAdapter` imports. Add a small local adapter that returns balance history, mirroring the existing `WindowAdapter` pattern.

- [ ] **Step 1: Write the failing tests (append to `tests/test_mcp_tools.py`)**

```python
# tests/test_mcp_tools.py  (append; datetime, Decimal, SqliteCache, CacheTTLs,
# DataService, build_server, and _tool are already imported/defined above)
from financial_mcp.monarch.models import BalanceSnapshot


class BalanceHistoryAdapter(FakeAdapter):
    """HELOC with two dated snapshots for as-of / delta tools."""

    def get_balance_history(self):
        return [
            BalanceSnapshot(account="HELOC", date=datetime.date(2026, 5, 31), balance=Decimal("-224600.00")),
            BalanceSnapshot(account="HELOC", date=datetime.date(2026, 6, 28), balance=Decimal("-202143.55")),
        ]


def _bh_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(BalanceHistoryAdapter(), cache)


def test_balance_as_of_tool_carry_forward(tmp_path):
    server = build_server(_bh_service(tmp_path))
    result = _tool(server, "balance_as_of")(date="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    heloc = next(r for r in result["data"] if r["account"] == "HELOC")
    assert heloc["balance"] == "-202143.55"      # Decimal serialized as string
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is True


def test_balance_as_of_tool_rejects_bad_date(tmp_path):
    server = build_server(_bh_service(tmp_path))
    result = _tool(server, "balance_as_of")(date="not-a-date")
    assert result == {"error": "date must be YYYY-MM-DD"}


def test_balance_delta_tool_positive_paydown(tmp_path):
    server = build_server(_bh_service(tmp_path))
    result = _tool(server, "balance_delta")(start="2026-05-31", end="2026-06-28")
    heloc = next(r for r in result["data"] if r["account"] == "HELOC")
    assert heloc["delta"] == "22456.45"          # paydown surfaces positive, as string
    assert heloc["start"]["balance"] == "-224600.00"   # nested Decimals also stringified
    assert heloc["end"]["balance"] == "-202143.55"


def test_balance_delta_tool_rejects_reversed_dates(tmp_path):
    server = build_server(_bh_service(tmp_path))
    result = _tool(server, "balance_delta")(start="2026-06-28", end="2026-05-31")
    assert result == {"error": "start must not be after end"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py -v -k "balance_as_of or balance_delta"`
Expected: FAIL — `KeyError`/`AttributeError` (tools not registered in `_tool_manager`).

- [ ] **Step 3: Write minimal implementation**

In `financial_mcp/mcp_server.py`, import the analytics module and add two tools (place beside `spending_summary`):

```python
from financial_mcp.analytics import balances, spending
```

```python
    @server.tool()
    def balance_as_of(date: str) -> dict:
        """Each account's balance as-of a date (YYYY-MM-DD), carry-forward.

        Returns the last known balance on-or-before the date per account, with
        the real snapshot_date it came from and is_carry_forward. Accounts with
        no snapshot on-or-before the date return balance null + reason. Balances
        are signed as Monarch reports them (liabilities negative). Use this for
        glide-path checkpoints like "where was the HELOC on 2026-06-30".
        """
        try:
            target = datetime.date.fromisoformat(date)
        except ValueError:
            return {"error": "date must be YYYY-MM-DD"}

        def _run():
            rows = balances.balance_as_of(svc.balance_history(), target)
            return _envelope("balance_history", _jsonify(rows))

        return _guard(_run)

    @server.tool()
    def balance_delta(start: str, end: str) -> dict:
        """Per-account balance change between two dates (YYYY-MM-DD, inclusive).

        Server-computed delta = end_balance - start_balance per account, so a
        HELOC paydown surfaces as a POSITIVE delta. Each endpoint reports its
        real snapshot_date and is_carry_forward. If either endpoint has no
        balance, delta is null + reason. Use this for "how much did the HELOC
        drop between X and Y" instead of subtracting balance_as_of calls yourself.
        """
        try:
            s = datetime.date.fromisoformat(start)
        except ValueError:
            return {"error": "start must be YYYY-MM-DD"}
        try:
            e = datetime.date.fromisoformat(end)
        except ValueError:
            return {"error": "end must be YYYY-MM-DD"}
        if s > e:
            return {"error": "start must not be after end"}

        def _run():
            rows = balances.balance_delta(svc.balance_history(), s, e)
            return _envelope("balance_history", _jsonify(rows))

        return _guard(_run)
```

> **REQUIRED:** the existing `_jsonify` (bottom of `mcp_server.py`) stringifies only **top-level** `Decimal` values per row: `{k: (str(v) if isinstance(v, Decimal) else v) ...}`. `balance_delta` rows nest `start`/`end` sub-dicts whose `balance` is a `Decimal`, so the shallow version leaves those as `Decimal` and `test_balance_delta_tool_positive_paydown` FAILS. Extend `_jsonify` to recurse into nested dict values as part of this step:
>
> ```python
> def _jsonify(rows: list[dict]) -> list[dict]:
>     """Convert Decimal values (including nested dicts) to strings for JSON transport."""
>     def conv(v):
>         if isinstance(v, Decimal):
>             return str(v)
>         if isinstance(v, dict):
>             return {k: conv(x) for k, x in v.items()}
>         return v
>     return [{k: conv(v) for k, v in r.items()} for r in rows]
> ```
> This stays backward-compatible with existing callers (top-level Decimals still stringify; `spending_summary`'s flat rows are unaffected — confirm `test_spending_summary_envelope_and_math` still passes in Step 4).

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (all prior tests + 4 new tool tests). Pay attention that `test_spending_summary_envelope_and_math` and the other `_jsonify`-dependent tests still pass after the recursion change.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: balance_as_of + balance_delta MCP tools"
```

---

### Task 8: Live end-to-end verification + docs

**This task is run interactively against the real org — like Task 1, it is not a pytest task. It closes the loop the spec's §5 opened.**

**Files:**
- Modify: `docs/INSTALL.md` (document the two new tools under the tool list)
- Modify: `docs/specs/2026-08-08-monarch-mcp-balance-history-design.md` (mark Status: Shipped + live-proven, with the real numbers observed)

- [ ] **Step 1: Confirm the suite is green**

Run: `uv run pytest -q`
Expected: all pass (previously 113 + the new tests).

- [ ] **Step 2: Live-verify in Claude Desktop (fresh chat, after Cmd+Q restart)**

Ask, against the real synced org:
- "What was my HELOC balance as of 2026-06-30?" → expect a carry-forward row with a real balance + snapshot_date + `is_carry_forward`.
- "How much did my HELOC drop between 2026-05-31 and 2026-06-28?" → expect a positive delta matching the account's real movement.
Record the observed numbers.

- [ ] **Step 3: Update docs with the two new tools and real observed numbers**

In `docs/INSTALL.md`, add `balance_as_of` and `balance_delta` to the tool list with one-line descriptions. In the spec, set `Status: Shipped + live-proven (2026-08-08)` and note the observed HELOC as-of / delta figures.

- [ ] **Step 4: Commit**

```bash
git add docs/INSTALL.md docs/specs/2026-08-08-monarch-mcp-balance-history-design.md
git commit -m "docs: balance-history tools shipped + live-proven"
```

---

## Notes for the executor
- **Task 1 is DONE (live-verified 2026-08-08).** The confirmed query is `snapshotsForAccount(accountId:)` returning dated `{date, signedBalance}` pairs — see spec §5.1 and Task 5, both already updated to this shape. Do NOT use the dateless `recentBalances` array (the rejected first candidate). No date derivation is needed; dates come straight from the `date` field.
- Work on the existing `balance-history-source` branch (the design doc + this plan are already committed there).
- All named test files already exist — append to them (don't recreate).
