# Savings Rate + "Where It Went" Reconciliation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one MCP tool `savings_summary(start, end)` that reports Monarch's authoritative cashflow savings rate (with a true signed rate alongside Monarch's floored one) and reconciles it against the change in cash+debt account balances, naming the unattributed gap — the P1 advisory differentiator.

**Architecture:** Slots into the existing one-directional layered pattern (Monarch GraphQL → Adapter → DataService → pure analytics → MCP tool). Adds one new GraphQL query (`GET_CASHFLOW_SUMMARY`, live-verified), a `Cashflow` model, an uncached `get_cashflow` adapter method, and a new pure module `analytics/savings.py`. Folds in the `id`-keying migration of the shipped balance-history code (retiring the displayName known-boundary) because this is the first feature that *sums* deltas and is therefore the first where a duplicate-displayName collision is a correctness bug.

**Tech Stack:** Python 3.12, Pydantic v2, `mcp.server.fastmcp`, `aiohttp` (adapter transport), SQLite cache, `pytest`, `uv` for running.

## Global Constraints

- Money is `Decimal` everywhere; JSON transport serializes Decimals to strings via `_jsonify` / `model_dump(mode="json")` — never float. Parse Monarch's JSON numbers via `Decimal(str(x))` (through-string), never `Decimal(float)`.
- Analytics modules are **pure** (no I/O), mirroring `financial_mcp/analytics/balances.py` and `spending.py`.
- Balance sign = as Monarch reports it — liabilities negative, **not** flipped.
- Delta sign = `end − start` (HELOC paydown surfaces as a positive delta).
- Savings rate is on **net (take-home) income** — Monarch cannot see pre-tax withholding. The tool never invents gross income or an "adjusted" rate. The `methodology` string (verbatim below) is returned on every call.
- Cashflow is **NEVER cached** — one lightweight live `aggregates` call per invocation, window-parameterized. The pre-existing unused `CacheTTLs.cashflow` slot stays unused.
- The balance side reuses the **cached** `balance_history` (data_type `"balance_history"`, TTL = DAY). The freshness envelope on `savings_summary` reports `balance_history` freshness (the stale-prone input).
- Balance analytics key on account **`id`**, not `displayName` (duplicate display names exist — the 529 succession case). `displayName` is retained only as a human label (`account` field) in output rows.
- Account classification for reconciliation scope, from the extended `GET_ACCOUNTS_MIN`: **summed into the gap** = `type.name ∈ {depository, credit, loan}` AND `includeInNetWorth == true`; **shown separately, not summed** = `type.name ∈ {brokerage, real_estate}`; **excluded entirely** = `includeInNetWorth == false`, or any unclassified type. Null-delta accounts are never silently dropped from the sum — they are listed by name in `excluded_accounts`.
- `MonarchAdapter` is a strict `abc.ABC`. Adding a new `@abc.abstractmethod` (`get_cashflow`) means EVERY concrete subclass — including the test fakes (`FakeAdapter` in `tests/test_mcp_tools.py` and `tests/test_data_service.py`, plus `_StubAdapter` in `tests/test_data_service.py`, and every subclass of those fakes) — must implement it or fail to instantiate. Update those fakes in the SAME task that adds the abstract method.
- GraphQL operation name in the request payload MUST match the query document's named operation, or Monarch returns HTTP 400 (the balance-history lesson).
- MCP tools are invoked in tests **synchronously** via `_tool(server, name)` = `server._tool_manager._tools[name].fn`, then called directly: `_tool(server, "x")(arg=...)`. No `asyncio` in tool tests.
- The freshness envelope from `_envelope(...)` has keys `{as_of, age_hours, stale, count, data}` — there is NO `data_type` key in the output; `_envelope`'s first arg is only the cache key used to look up freshness.
- Tests are offline/deterministic — no live Monarch calls in the pytest suite. The single live check is Task 8, run interactively.
- Run tests with `uv run pytest`. Commit after every task.

**The `methodology` string (return verbatim on every `savings_summary` call):**

> Savings rate is on take-home (net) income; pre-tax 401k/403b/withholding are not in Monarch's cashflow and are excluded from the rate — that saving appears in where_it_went as retirement/brokerage balances rising. The reconciliation compares cash savings against the change in cash and debt account balances only; investment and real-estate balances are shown separately because their changes are mostly market movement, not saving. The remaining gap reflects internal transfers (excluded from cashflow), snapshot timing, and any manual/unlinked cash accounts; it is not attributed to a single cause.

---

### Task 1: Live discovery — DONE (both queries live-verified 2026-08-09)

**No work.** Both external surfaces were probed live against the real org before the design was locked; the confirmed shapes live in the design spec §5. The rest of the plan consumes those facts:

- **`GET_CASHFLOW_SUMMARY`** (spec §5.1): operation name `Web_GetCashFlowPage`; JSON path `data.summary[0].summary` → `{sumIncome, sumExpense, savings, savingsRate}`. Monarch **floors negative savings rates at 0** (YTD savings −31086.46 → savingsRate 0.0). June 2026 → income 45976.73, expense −34059.93, savings 11916.80, savingsRate 0.2592.
- **Account types** (spec §5.2): `type.name` and `includeInNetWorth` classify all 40 accounts cleanly; duplicate displayNames confirmed (the 529 succession case) — the evidence behind the `id`-keying migration (Tasks 4–5).

Do NOT re-probe. Build Tasks 2–7 on these confirmed shapes; Task 8 is the closing live e2e.

---

### Task 2: `Cashflow` model

**Files:**
- Modify: `financial_mcp/monarch/models.py`
- Test: `tests/test_models.py` (append; file exists)

**Interfaces:**
- Consumes: nothing (leaf model).
- Produces: `Cashflow(start: datetime.date, end: datetime.date, income: Decimal, expense: Decimal, savings: Decimal, savings_rate_monarch: Decimal)` — used by Tasks 3, 6, 7.

- [ ] **Step 1: Write the failing test**

Add `Cashflow` to the existing `from financial_mcp.monarch.models import ...` line in `tests/test_models.py` (`datetime` and `Decimal` are already imported there), then append:

```python
def test_cashflow_fields_and_json_roundtrip():
    cf = Cashflow(
        start=datetime.date(2026, 6, 1),
        end=datetime.date(2026, 6, 30),
        income=Decimal("45976.73"),
        expense=Decimal("-34059.93"),
        savings=Decimal("11916.80"),
        savings_rate_monarch=Decimal("0.2592"),
    )
    assert cf.income == Decimal("45976.73")
    assert cf.expense == Decimal("-34059.93")
    assert cf.savings == Decimal("11916.80")
    assert cf.savings_rate_monarch == Decimal("0.2592")
    dumped = cf.model_dump(mode="json")
    assert dumped == {
        "start": "2026-06-01",
        "end": "2026-06-30",
        "income": "45976.73",
        "expense": "-34059.93",
        "savings": "11916.80",
        "savings_rate_monarch": "0.2592",
    }
    # reconstructable from cached JSON exactly
    assert Cashflow(**dumped) == cf
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_cashflow_fields_and_json_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'Cashflow'`.

- [ ] **Step 3: Write minimal implementation**

Append to `financial_mcp/monarch/models.py` (the file already imports `datetime`, `Decimal`, and `BaseModel`):

```python
class Cashflow(BaseModel):
    start: datetime.date
    end: datetime.date
    income: Decimal
    expense: Decimal
    savings: Decimal
    savings_rate_monarch: Decimal
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py::test_cashflow_fields_and_json_roundtrip -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/models.py tests/test_models.py
git commit -m "feat: add Cashflow model"
```

---

### Task 3: Adapter — `get_cashflow(start, end)` on interface, CommunityAdapter, CsvAdapter

**Files:**
- Modify: `financial_mcp/monarch/queries.py` (add `GET_CASHFLOW_SUMMARY` — the string CONFIRMED in spec §5.1)
- Modify: `financial_mcp/monarch/adapter.py` (add abstract method)
- Modify: `financial_mcp/monarch/community_adapter.py` (implement one uncached live call)
- Modify: `financial_mcp/monarch/csv_adapter.py` (raise "not available in CSV mode")
- Modify: `tests/test_mcp_tools.py` and `tests/test_data_service.py` — add `get_cashflow` to their `FakeAdapter` classes AND to `_StubAdapter` in `tests/test_data_service.py` (else the new abstract method makes them un-instantiable and the whole suite fails to collect).
- Test: `tests/test_community_adapter.py` (append), `tests/test_csv_adapter.py` (append)

**Interfaces:**
- Consumes: `Cashflow` (Task 2); the confirmed `aggregates` shape (spec §5.1); existing `CommunityAdapter._transport`, `_run`.
- Produces: `MonarchAdapter.get_cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow` on all adapters. Used by Task 6.

> **CONFIRMED QUERY SHAPE (spec §5.1):** operation name `Web_GetCashFlowPage` (MUST match the document or Monarch returns HTTP 400). Variables `{"filters": {"search": "", "categories": [], "accounts": [], "tags": [], "startDate": "YYYY-MM-DD", "endDate": "YYYY-MM-DD"}}`. JSON path `data.summary[0].summary` → `{sumIncome, sumExpense, savings, savingsRate}`. **Uncached** — window-parameterized, low hit-rate.

> **CRITICAL ORDERING:** `MonarchAdapter` is a strict `abc.ABC` (see `tests/test_adapter_interface.py`). The moment `get_cashflow` is added as abstract, any concrete subclass lacking it raises `TypeError` on instantiation. Fakes to update in THIS task's Step 3: `FakeAdapter` in `tests/test_mcp_tools.py` (subclassed by `ManyTxnAdapter`, `WindowAdapter`, `BalanceHistoryAdapter`, and inline `ExpiredAdapter`/`BlockedAdapter`), `FakeAdapter` in `tests/test_data_service.py`, and `_StubAdapter` in `tests/test_data_service.py`.

- [ ] **Step 1: Write the failing tests (stub transport → deterministic, offline)**

```python
# tests/test_community_adapter.py  (append; datetime, Decimal, CommunityAdapter,
# SESSION already imported at top of file)
from financial_mcp.monarch.models import Cashflow  # add near the BalanceSnapshot import


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
```

```python
# tests/test_csv_adapter.py  (append; the file's _adapter() helper exists)
def test_csv_adapter_get_cashflow_raises():
    import pytest
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    import datetime
    adapter = CsvAdapter(transactions_path="x", balances_path="y")
    with pytest.raises(RuntimeError, match="not available in CSV mode"):
        adapter.get_cashflow(datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_community_adapter.py -k get_cashflow tests/test_csv_adapter.py::test_csv_adapter_get_cashflow_raises -v`
Expected: FAIL — `ImportError`/`AttributeError` (no `Cashflow` import, no `get_cashflow`).

- [ ] **Step 3: Write minimal implementation (queries + adapter ABC + concrete adapters + fakes, all together)**

Add to `financial_mcp/monarch/queries.py` (the CONFIRMED string from spec §5.1):

```python
GET_CASHFLOW_SUMMARY = """
  query Web_GetCashFlowPage($filters: TransactionFilterInput) {
    summary: aggregates(filters: $filters, fillEmptyValues: true) {
      summary {
        sumIncome
        sumExpense
        savings
        savingsRate
        __typename
      }
      __typename
    }
  }
"""
```

Add to `financial_mcp/monarch/adapter.py` — update the import line and add the abstract method (note `import datetime` is needed for the signature type):

```python
import abc
import datetime

from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Cashflow, Transaction
...
    @abc.abstractmethod
    def get_cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow: ...
```

Add a stub to the three fakes so they remain instantiable:

```python
# in tests/test_mcp_tools.py — inside class FakeAdapter (subclasses inherit it)
    def get_cashflow(self, start, end):
        return Cashflow(
            start=start, end=end,
            income=Decimal("0"), expense=Decimal("0"),
            savings=Decimal("0"), savings_rate_monarch=Decimal("0"),
        )

# in tests/test_data_service.py — inside class FakeAdapter AND class _StubAdapter
    def get_cashflow(self, start, end):
        return Cashflow(
            start=start, end=end,
            income=Decimal("0"), expense=Decimal("0"),
            savings=Decimal("0"), savings_rate_monarch=Decimal("0"),
        )
```

(Add `Cashflow` to the `from financial_mcp.monarch.models import ...` line in BOTH test files.)

Add to `financial_mcp/monarch/community_adapter.py` — import `Cashflow` and `GET_CASHFLOW_SUMMARY`, then add the method:

```python
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Cashflow, Transaction
from financial_mcp.monarch.queries import (
    GET_ACCOUNT_SNAPSHOTS,
    GET_ACCOUNTS,
    GET_ACCOUNTS_MIN,
    GET_CASHFLOW_SUMMARY,
    GET_TRANSACTIONS,
)

    def get_cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow:
        variables = {
            "filters": {
                "search": "",
                "categories": [],
                "accounts": [],
                "tags": [],
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            }
        }
        data = self._run(
            self._transport("Web_GetCashFlowPage", GET_CASHFLOW_SUMMARY, variables)
        )
        summary = data["summary"][0]["summary"]
        return Cashflow(
            start=start,
            end=end,
            income=Decimal(str(summary["sumIncome"])),
            expense=Decimal(str(summary["sumExpense"])),
            savings=Decimal(str(summary["savings"])),
            savings_rate_monarch=Decimal(str(summary["savingsRate"])),
        )
```

Add to `financial_mcp/monarch/csv_adapter.py` — import `Cashflow` and add the method:

```python
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Cashflow, Transaction
...
    def get_cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow:
        raise RuntimeError("cashflow is not available in CSV mode")
```

- [ ] **Step 4: Run the full suite (all adapters must still satisfy the interface)**

Run: `uv run pytest -v`
Expected: PASS. (The new abstract method means any adapter/fake missing `get_cashflow` fails to instantiate — this step catches that across all tests.)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/queries.py financial_mcp/monarch/adapter.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/csv_adapter.py tests/test_community_adapter.py tests/test_csv_adapter.py tests/test_mcp_tools.py tests/test_data_service.py
git commit -m "feat: adapter get_cashflow (live aggregates query + CSV raises)"
```

---

### Task 4: `id`-keying migration — `BalanceSnapshot` model (SHIPPED-CODE CHANGE)

**Highest-care task. Changes a shipped model.** `BalanceSnapshot` moves from a displayName-only key to `account_id` (the join key) + `display_name` (human label) + classification fields. The reviewer must confirm the two shipped tools still behave correctly after Tasks 4–6 land.

**Files:**
- Modify: `financial_mcp/monarch/models.py`
- Test: `tests/test_models.py` (replace the existing `test_balance_snapshot_fields_and_json_roundtrip`)

**Interfaces:**
- Consumes: nothing (leaf model).
- Produces: `BalanceSnapshot(account_id: str, display_name: str, date: datetime.date, balance: Decimal, type_name: str | None = None, include_in_net_worth: bool = True)`. Used by Tasks 5, 6, 7. **Field rename: `account` → `display_name`; new required `account_id`; new optional `type_name`, `include_in_net_worth`.**

- [ ] **Step 1: Replace the failing test**

Replace `test_balance_snapshot_fields_and_json_roundtrip` in `tests/test_models.py` with:

```python
def test_balance_snapshot_fields_and_json_roundtrip():
    s = BalanceSnapshot(
        account_id="acct-heloc",
        display_name="HELOC",
        date=datetime.date(2026, 6, 28),
        balance=Decimal("-202143.55"),
        type_name="loan",
        include_in_net_worth=True,
    )
    assert s.account_id == "acct-heloc"
    assert s.display_name == "HELOC"
    assert s.balance == Decimal("-202143.55")
    assert s.type_name == "loan"
    assert s.include_in_net_worth is True
    dumped = s.model_dump(mode="json")
    assert dumped == {
        "account_id": "acct-heloc",
        "display_name": "HELOC",
        "date": "2026-06-28",
        "balance": "-202143.55",
        "type_name": "loan",
        "include_in_net_worth": True,
    }
    assert BalanceSnapshot(**dumped) == s


def test_balance_snapshot_classification_defaults():
    s = BalanceSnapshot(
        account_id="a1", display_name="Checking",
        date=datetime.date(2026, 6, 1), balance=Decimal("100.00"),
    )
    assert s.type_name is None
    assert s.include_in_net_worth is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -k balance_snapshot -v`
Expected: FAIL — `ValidationError` (unexpected/ missing fields) on the current model.

- [ ] **Step 3: Write minimal implementation**

Replace the existing `BalanceSnapshot` class in `financial_mcp/monarch/models.py`:

```python
class BalanceSnapshot(BaseModel):
    account_id: str
    display_name: str
    date: datetime.date
    balance: Decimal
    type_name: str | None = None
    include_in_net_worth: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -k balance_snapshot -v`
Expected: PASS (2 tests). The rest of the suite is red until Tasks 5–6 land — that is expected mid-migration; do not "fix" other files here beyond what Steps above name.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/models.py tests/test_models.py
git commit -m "feat!: re-key BalanceSnapshot on account_id + classification (migration 1/3)"
```

---

### Task 5: `id`-keying migration — `balance_as_of` / `balance_delta` analytics (SHIPPED-CODE CHANGE)

Re-key the shipped analytics on `account_id` (so two accounts sharing a `display_name` are NEVER merged), and carry `display_name` + classification onto every output row.

**Files:**
- Modify: `financial_mcp/analytics/balances.py`
- Test: `tests/test_balances_analytics.py` (rewrite `_snaps()` and existing assertions; add id-keying test)

**Interfaces:**
- Consumes: `BalanceSnapshot` (Task 4, id-keyed).
- Produces:
  - `balance_as_of(snapshots, target_date) -> list[dict]`, each row `{account_id, display_name, type_name, include_in_net_worth, balance: Decimal|None, as_of_requested, snapshot_date: str|None, is_carry_forward: bool, reason?}`, sorted by `(display_name, account_id)`.
  - `balance_delta(snapshots, start, end) -> list[dict]`, each row `{account_id, display_name, type_name, include_in_net_worth, start: {balance, snapshot_date, is_carry_forward}|None, end: {...}|None, delta: Decimal|None, reason?}`.
  Used by Tasks 7 (savings) and the shipped tools (unchanged tool code).

- [ ] **Step 1: Rewrite the failing tests**

Replace the whole body of `tests/test_balances_analytics.py` with (keeps the same behavioral coverage, now id-keyed, adds the collision test):

```python
import datetime
from decimal import Decimal

from financial_mcp.analytics.balances import balance_as_of, balance_delta
from financial_mcp.monarch.models import BalanceSnapshot

D = datetime.date


def _snaps():
    return [
        BalanceSnapshot(account_id="heloc", display_name="HELOC", date=D(2026, 5, 31),
                        balance=Decimal("-224600.00"), type_name="loan", include_in_net_worth=True),
        BalanceSnapshot(account_id="heloc", display_name="HELOC", date=D(2026, 6, 28),
                        balance=Decimal("-202143.55"), type_name="loan", include_in_net_worth=True),
        BalanceSnapshot(account_id="broker", display_name="Brokerage", date=D(2026, 6, 15),
                        balance=Decimal("50000.00"), type_name="brokerage", include_in_net_worth=True),
    ]


def test_exact_hit_is_not_carry_forward():
    rows = balance_as_of(_snaps(), D(2026, 6, 28))
    heloc = next(r for r in rows if r["account_id"] == "heloc")
    assert heloc["display_name"] == "HELOC"
    assert heloc["type_name"] == "loan"
    assert heloc["include_in_net_worth"] is True
    assert heloc["balance"] == Decimal("-202143.55")
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is False
    assert heloc["as_of_requested"] == "2026-06-28"


def test_gap_carries_forward_last_known_with_provenance():
    rows = balance_as_of(_snaps(), D(2026, 6, 30))
    heloc = next(r for r in rows if r["account_id"] == "heloc")
    assert heloc["balance"] == Decimal("-202143.55")
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is True


def test_before_first_snapshot_is_null_with_reason():
    rows = balance_as_of(_snaps(), D(2026, 6, 1))
    bro = next(r for r in rows if r["account_id"] == "broker")
    assert bro["balance"] is None
    assert bro["snapshot_date"] is None
    assert "reason" in bro


def test_rows_cover_all_accounts_sorted_by_display_then_id():
    rows = balance_as_of(_snaps(), D(2026, 6, 30))
    assert [r["display_name"] for r in rows] == ["Brokerage", "HELOC"]


def test_duplicate_display_names_are_not_merged():
    # 529 succession: same display_name, different ids -> two distinct rows.
    snaps = [
        BalanceSnapshot(account_id="529-old", display_name="Braden 529 (01)", date=D(2026, 6, 1),
                        balance=Decimal("1000.00"), type_name="brokerage", include_in_net_worth=False),
        BalanceSnapshot(account_id="529-new", display_name="Braden 529 (01)", date=D(2026, 6, 1),
                        balance=Decimal("2000.00"), type_name="brokerage", include_in_net_worth=True),
    ]
    rows = balance_as_of(snaps, D(2026, 6, 1))
    ids = sorted(r["account_id"] for r in rows)
    assert ids == ["529-new", "529-old"]
    by_id = {r["account_id"]: r for r in rows}
    assert by_id["529-old"]["balance"] == Decimal("1000.00")
    assert by_id["529-new"]["balance"] == Decimal("2000.00")


def test_delta_heloc_paydown_is_positive():
    rows = balance_delta(_snaps(), D(2026, 5, 31), D(2026, 6, 28))
    heloc = next(r for r in rows if r["account_id"] == "heloc")
    assert heloc["delta"] == Decimal("22456.45")
    assert heloc["display_name"] == "HELOC"
    assert heloc["type_name"] == "loan"
    assert heloc["include_in_net_worth"] is True
    assert heloc["start"]["balance"] == Decimal("-224600.00")
    assert heloc["end"]["balance"] == Decimal("-202143.55")


def test_delta_null_when_endpoint_missing():
    rows = balance_delta(_snaps(), D(2026, 6, 1), D(2026, 6, 28))
    bro = next(r for r in rows if r["account_id"] == "broker")
    assert bro["delta"] is None
    assert "reason" in bro
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_balances_analytics.py -v`
Expected: FAIL — rows still keyed on `account`; `KeyError: 'account_id'`.

- [ ] **Step 3: Write minimal implementation**

Replace the whole body of `financial_mcp/analytics/balances.py`:

```python
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
    by_id: dict[str, list[BalanceSnapshot]] = defaultdict(list)
    for s in snapshots:
        by_id[s.account_id].append(s)
    rows: list[dict] = []
    for account_id, points in by_id.items():
        latest = points[-1]  # any snapshot carries account-level label + classification
        base = {
            "account_id": account_id,
            "display_name": latest.display_name,
            "type_name": latest.type_name,
            "include_in_net_worth": latest.include_in_net_worth,
            "as_of_requested": target_date.isoformat(),
        }
        chosen = _as_of_one(points, target_date)
        if chosen is None:
            rows.append({
                **base,
                "balance": None,
                "snapshot_date": None,
                "is_carry_forward": False,
                "reason": "no snapshot on or before requested date",
            })
        else:
            rows.append({
                **base,
                "balance": chosen.balance,
                "snapshot_date": chosen.date.isoformat(),
                "is_carry_forward": chosen.date != target_date,
            })
    rows.sort(key=lambda r: (r["display_name"], r["account_id"]))
    return rows


def balance_delta(
    snapshots: list[BalanceSnapshot], start: datetime.date, end: datetime.date
) -> list[dict]:
    start_rows = {r["account_id"]: r for r in balance_as_of(snapshots, start)}
    end_rows = {r["account_id"]: r for r in balance_as_of(snapshots, end)}
    rows: list[dict] = []
    for account_id in set(start_rows) | set(end_rows):
        s = start_rows.get(account_id)
        e = end_rows.get(account_id)
        meta = s or e  # both carry the same account-level label + classification
        s_bal = None if s is None else s["balance"]
        e_bal = None if e is None else e["balance"]
        row = {
            "account_id": account_id,
            "display_name": meta["display_name"],
            "type_name": meta["type_name"],
            "include_in_net_worth": meta["include_in_net_worth"],
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
    rows.sort(key=lambda r: (r["display_name"], r["account_id"]))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_balances_analytics.py -v`
Expected: PASS (7 tests). The MCP-tool tests for `balance_as_of`/`balance_delta` are still red until Task 6 updates their fake + assertions — expected.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/analytics/balances.py tests/test_balances_analytics.py
git commit -m "feat!: key balance analytics on account_id + carry classification (migration 2/3)"
```

---

### Task 6: `id`-keying migration — adapter fan-out + shipped-tool tests (SHIPPED-CODE CHANGE, migration 3/3)

Extend `GET_ACCOUNTS_MIN` to carry `type.name` + `includeInNetWorth`, rebuild the `get_balance_history` fan-out to emit id-keyed classified snapshots, and update the shipped `balance_as_of`/`balance_delta` MCP-tool tests + their fake to the new row shape. After this task the whole suite is green again.

**Files:**
- Modify: `financial_mcp/monarch/queries.py` (extend `GET_ACCOUNTS_MIN`)
- Modify: `financial_mcp/monarch/community_adapter.py` (`get_balance_history` fan-out)
- Modify: `tests/test_community_adapter.py` (rewrite the two `get_balance_history` tests)
- Modify: `tests/test_mcp_tools.py` (`BalanceHistoryAdapter` fake + the 3 `balance_as_of`/`balance_delta` assertions)
- Modify: `tests/test_data_service.py` (`_StubAdapter`/`FakeAdapter` `get_balance_history` stubs + the `test_balance_history_is_served_and_cached` snapshot)

**Interfaces:**
- Consumes: `BalanceSnapshot` (Task 4, id-keyed); existing `_transport`, `_run`.
- Produces: `get_balance_history()` returns id-keyed classified `BalanceSnapshot`s, sorted by `(display_name, account_id, date)`. The shipped tools' code in `mcp_server.py` is UNCHANGED (they already pass rows through `_jsonify` + `_envelope`); only their tests move to the new row keys.

- [ ] **Step 1: Rewrite the failing tests**

In `tests/test_community_adapter.py`, replace both `get_balance_history` tests:

```python
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
```

In `tests/test_mcp_tools.py`, update the `BalanceHistoryAdapter` fake and the 3 assertions:

```python
class BalanceHistoryAdapter(FakeAdapter):
    """HELOC with two dated snapshots for as-of / delta tools."""

    def get_balance_history(self):
        return [
            BalanceSnapshot(account_id="heloc", display_name="HELOC", date=datetime.date(2026, 5, 31),
                            balance=Decimal("-224600.00"), type_name="loan", include_in_net_worth=True),
            BalanceSnapshot(account_id="heloc", display_name="HELOC", date=datetime.date(2026, 6, 28),
                            balance=Decimal("-202143.55"), type_name="loan", include_in_net_worth=True),
        ]
```

Change the three lookups from `r["account"] == "HELOC"` to `r["display_name"] == "HELOC"` in `test_balance_as_of_tool_carry_forward`, `test_balance_delta_tool_positive_paydown` (the rest of those assertions — `balance`, `snapshot_date`, `is_carry_forward`, `delta`, nested `start`/`end` — are unchanged).

In `tests/test_data_service.py`, update `_StubAdapter.get_balance_history` and `test_balance_history_is_served_and_cached` to the new fields:

```python
def test_balance_history_is_served_and_cached(tmp_path):
    snaps = [BalanceSnapshot(account_id="heloc", display_name="HELOC",
                             date=datetime.date(2026, 6, 28), balance=Decimal("-202143.55"),
                             type_name="loan", include_in_net_worth=True)]
    ...
```

(The rest of that test — cache-hit count, Decimal round-trip — is unchanged.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_community_adapter.py -k get_balance_history tests/test_mcp_tools.py -k "balance_as_of or balance_delta" -v`
Expected: FAIL — old fan-out still builds `account=`/no classification; `ValidationError`/`KeyError`.

- [ ] **Step 3: Write minimal implementation**

Extend `GET_ACCOUNTS_MIN` in `financial_mcp/monarch/queries.py`:

```python
GET_ACCOUNTS_MIN = """
  query GetAccountsMin {
    accounts {
      id
      displayName
      includeInNetWorth
      type {
        name
        __typename
      }
      __typename
    }
  }
"""
```

Rebuild `get_balance_history` in `financial_mcp/monarch/community_adapter.py`:

```python
    def get_balance_history(self) -> list[BalanceSnapshot]:
        accts = self._run(self._transport("GetAccountsMin", GET_ACCOUNTS_MIN, {}))
        out: list[BalanceSnapshot] = []
        for acct in accts.get("accounts", []):
            account_id = acct["id"]
            display_name = acct.get("displayName", "")
            type_name = (acct.get("type") or {}).get("name")
            include = acct.get("includeInNetWorth", True)
            data = self._run(
                self._transport("SnapshotsForAccount", GET_ACCOUNT_SNAPSHOTS, {"id": account_id})
            )
            for snap in data.get("snapshots") or []:
                out.append(BalanceSnapshot(
                    account_id=account_id,
                    display_name=display_name,
                    date=datetime.date.fromisoformat(snap["date"]),
                    balance=Decimal(str(snap["signedBalance"])),
                    type_name=type_name,
                    include_in_net_worth=include,
                ))
        out.sort(key=lambda s: (s.display_name, s.account_id, s.date))
        return out
```

- [ ] **Step 4: Run the full suite (migration complete — must be fully green)**

Run: `uv run pytest -v`
Expected: PASS (every test). If anything referencing `BalanceSnapshot.account` remains, it surfaces here.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/queries.py financial_mcp/monarch/community_adapter.py tests/test_community_adapter.py tests/test_mcp_tools.py tests/test_data_service.py
git commit -m "feat!: id-keyed classified balance-history fan-out (migration 3/3)"
```

---

### Task 7: `analytics/savings.py` pure module

The analytical heart: computes the true signed rate (with zero-income guard), splits balance-delta rows into cash+debt / investments / excluded, sums the scoped net-worth delta, and derives the reconciliation gap. Pure — no I/O.

**Files:**
- Create: `financial_mcp/analytics/savings.py`
- Test: `tests/test_savings_analytics.py`

**Interfaces:**
- Consumes: `Cashflow` (Task 2); `balance_delta` output rows (Task 5) — dicts with keys `account_id, display_name, type_name, include_in_net_worth, delta: Decimal|None, start, end`.
- Produces:
  - `METHODOLOGY: str` (the verbatim Global-Constraints string).
  - `summarize(cashflow: Cashflow, delta_rows: list[dict]) -> dict` returning the full tool-data object (see the return shape in Step 3). Used by Task 9 (tool).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_savings_analytics.py
import datetime
from decimal import Decimal

from financial_mcp.analytics import savings
from financial_mcp.monarch.models import Cashflow

D = datetime.date


def _cf(income="45976.73", expense="-34059.93", savings="11916.80", rate="0.2592"):
    return Cashflow(
        start=D(2026, 6, 1), end=D(2026, 6, 30),
        income=Decimal(income), expense=Decimal(expense),
        savings=Decimal(savings), savings_rate_monarch=Decimal(rate),
    )


def _row(account_id, display_name, type_name, include, delta):
    return {
        "account_id": account_id,
        "display_name": display_name,
        "type_name": type_name,
        "include_in_net_worth": include,
        "start": None,
        "end": None,
        "delta": None if delta is None else Decimal(delta),
    }


def test_true_signed_rate_quantized_to_four_dp():
    out = savings.summarize(_cf(), [])
    # 11916.80 / 45976.73 = 0.2591919... -> 0.2592
    assert out["cashflow"]["savings_rate"] == Decimal("0.2592")
    assert out["cashflow"]["savings_rate_monarch"] == Decimal("0.2592")
    assert out["cashflow"]["rate_is_floored"] is False


def test_floored_negative_rate_flagged():
    cf = _cf(income="100000.00", expense="-131086.46", savings="-31086.46", rate="0")
    out = savings.summarize(cf, [])
    # our signed rate is negative; Monarch floored to 0 -> flag true
    assert out["cashflow"]["savings_rate"] == Decimal("-0.3109")
    assert out["cashflow"]["savings_rate_monarch"] == Decimal("0")
    assert out["cashflow"]["rate_is_floored"] is True


def test_zero_income_guard_no_divide_by_zero():
    cf = _cf(income="0", expense="-500.00", savings="-500.00", rate="0")
    out = savings.summarize(cf, [])
    assert out["cashflow"]["savings_rate"] is None
    assert out["cashflow"]["rate_reason"] == "no income in window; rate undefined"
    assert out["cashflow"]["rate_is_floored"] is False


def test_cash_and_debt_summed_investments_shown_separately():
    rows = [
        _row("chk", "Checking", "depository", True, "3000.00"),
        _row("cc", "Credit Card", "credit", True, "-500.00"),
        _row("heloc", "HELOC", "loan", True, "10615.00"),
        _row("401k", "401k", "brokerage", True, "8000.00"),
        _row("house", "House", "real_estate", True, "1000.00"),
    ]
    out = savings.summarize(_cf(), rows)
    cd_ids = {r["account_id"] for r in out["where_it_went"]["cash_and_debt"]}
    inv_ids = {r["account_id"] for r in out["where_it_went"]["investments_shown_separately"]}
    assert cd_ids == {"chk", "cc", "heloc"}
    assert inv_ids == {"401k", "house"}
    # 3000 - 500 + 10615 = 13115
    assert out["net_worth_delta_cash_debt"] == Decimal("13115.00")
    # gap = net_worth_delta_cash_debt - savings = 13115.00 - 11916.80 = 1198.20
    assert out["reconciliation_gap"] == Decimal("1198.20")


def test_excluded_account_not_summed_and_listed():
    rows = [
        _row("chk", "Checking", "depository", True, "1000.00"),
        _row("corp", "Corporate Card", "credit", False, "-9999.00"),   # excluded from net worth
    ]
    out = savings.summarize(_cf(), rows)
    assert out["net_worth_delta_cash_debt"] == Decimal("1000.00")   # corp NOT summed
    excluded_ids = {e["account_id"] for e in out["excluded_accounts"]}
    assert "corp" in excluded_ids


def test_unclassified_type_excluded():
    rows = [_row("odd", "Odd", None, True, "5.00")]
    out = savings.summarize(_cf(), rows)
    assert out["net_worth_delta_cash_debt"] == Decimal("0")
    assert {e["account_id"] for e in out["excluded_accounts"]} == {"odd"}


def test_null_delta_cash_account_excluded_not_dropped_silently():
    rows = [_row("newchk", "New Checking", "depository", True, None)]
    out = savings.summarize(_cf(), rows)
    assert out["net_worth_delta_cash_debt"] == Decimal("0")
    e = next(e for e in out["excluded_accounts"] if e["account_id"] == "newchk")
    assert "reason" in e


def test_window_and_methodology_present():
    out = savings.summarize(_cf(), [])
    assert out["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    assert out["methodology"] == savings.METHODOLOGY
    assert "take-home" in out["methodology"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_savings_analytics.py -v`
Expected: FAIL — `ModuleNotFoundError: financial_mcp.analytics.savings`.

- [ ] **Step 3: Write minimal implementation**

```python
# financial_mcp/analytics/savings.py
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from financial_mcp.monarch.models import Cashflow

CASH_DEBT_TYPES = {"depository", "credit", "loan"}
INVESTMENT_TYPES = {"brokerage", "real_estate"}

METHODOLOGY = (
    "Savings rate is on take-home (net) income; pre-tax 401k/403b/withholding "
    "are not in Monarch's cashflow and are excluded from the rate — that saving "
    "appears in where_it_went as retirement/brokerage balances rising. The "
    "reconciliation compares cash savings against the change in cash and debt "
    "account balances only; investment and real-estate balances are shown "
    "separately because their changes are mostly market movement, not saving. "
    "The remaining gap reflects internal transfers (excluded from cashflow), "
    "snapshot timing, and any manual/unlinked cash accounts; it is not "
    "attributed to a single cause."
)


def _signed_rate(cashflow: Cashflow) -> tuple[Decimal | None, str | None]:
    if cashflow.income == 0:
        return None, "no income in window; rate undefined"
    rate = (cashflow.savings / cashflow.income).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return rate, None


def summarize(cashflow: Cashflow, delta_rows: list[dict]) -> dict:
    rate, rate_reason = _signed_rate(cashflow)
    rate_is_floored = (
        rate is not None and rate < 0 and cashflow.savings_rate_monarch == 0
    )

    cash_and_debt: list[dict] = []
    investments: list[dict] = []
    excluded: list[dict] = []
    net_worth_delta = Decimal("0")

    for row in delta_rows:
        tag = {
            "account_id": row["account_id"],
            "display_name": row["display_name"],
        }
        if not row["include_in_net_worth"]:
            excluded.append({**tag, "reason": "excluded from Monarch net worth"})
            continue
        type_name = row["type_name"]
        if type_name in INVESTMENT_TYPES:
            investments.append(row)
            continue
        if type_name not in CASH_DEBT_TYPES:
            excluded.append(
                {**tag, "reason": "account type not classified for reconciliation"}
            )
            continue
        # cash + debt
        if row["delta"] is None:
            excluded.append(
                {**tag, "reason": "missing balance at one or both endpoints"}
            )
            continue
        cash_and_debt.append(row)
        net_worth_delta += row["delta"]

    gap = net_worth_delta - cashflow.savings

    cashflow_out: dict = {
        "income": cashflow.income,
        "expense": cashflow.expense,
        "savings": cashflow.savings,
        "savings_rate": rate,
        "savings_rate_monarch": cashflow.savings_rate_monarch,
        "rate_is_floored": rate_is_floored,
    }
    if rate_reason:
        cashflow_out["rate_reason"] = rate_reason

    return {
        "window": {"start": cashflow.start.isoformat(), "end": cashflow.end.isoformat()},
        "cashflow": cashflow_out,
        "where_it_went": {
            "cash_and_debt": cash_and_debt,
            "investments_shown_separately": investments,
        },
        "net_worth_delta_cash_debt": net_worth_delta,
        "reconciliation_gap": gap,
        "excluded_accounts": excluded,
        "methodology": METHODOLOGY,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_savings_analytics.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/analytics/savings.py tests/test_savings_analytics.py
git commit -m "feat: add savings analytics (signed rate + scoped reconciliation)"
```

---

### Task 8: `savings_summary` MCP tool

**Files:**
- Modify: `financial_mcp/mcp_server.py` (add the tool + extend `_jsonify`'s `conv` to recurse into lists)
- Test: `tests/test_mcp_tools.py` (append)

**Interfaces:**
- Consumes: `DataService.get_cashflow`-backed access, `DataService.balance_history()` (cached), `balances.balance_delta` (Task 5), `savings.summarize` (Task 7); existing `_guard`, `_envelope`, `_jsonify`.
- Produces: one `@server.tool()` `savings_summary(start: str, end: str) -> dict` returning the standard envelope whose `data` is `savings.summarize(...)`.

> **`DataService` has no cashflow accessor** (cashflow is uncached). The tool calls the adapter through a thin, uncached DataService passthrough. Add `DataService.cashflow(start, end)` in this task — a one-liner that calls the adapter directly (no cache), mirroring how `accounts()` is never cached but here even simpler since there is no store step.

> **`_jsonify` gap:** `savings.summarize` nests `Decimal`s inside LIST values (`where_it_went.cash_and_debt` is a list of delta rows, each with nested `start`/`end` dicts). The current `conv` recurses into dicts but returns lists unchanged, so those Decimals would stay unstringified and the envelope would fail MCP JSON transport. Extend `conv` to recurse into lists too (backward-compatible: existing flat/dict-nested callers are unaffected).

- [ ] **Step 1: Write the failing tests (append to `tests/test_mcp_tools.py`)**

```python
# tests/test_mcp_tools.py  (append)
from financial_mcp.monarch.models import Cashflow  # ensure imported (Task 3 added it)


class SavingsAdapter(FakeAdapter):
    """Cashflow + two dated snapshots per account for savings_summary."""

    def get_cashflow(self, start, end):
        return Cashflow(
            start=start, end=end,
            income=Decimal("45976.73"), expense=Decimal("-34059.93"),
            savings=Decimal("11916.80"), savings_rate_monarch=Decimal("0.2592"),
        )

    def get_balance_history(self):
        def two(account_id, name, type_name, s, e):
            return [
                BalanceSnapshot(account_id=account_id, display_name=name, date=datetime.date(2026, 6, 1),
                                balance=Decimal(s), type_name=type_name, include_in_net_worth=True),
                BalanceSnapshot(account_id=account_id, display_name=name, date=datetime.date(2026, 6, 30),
                                balance=Decimal(e), type_name=type_name, include_in_net_worth=True),
            ]
        return (
            two("chk", "Checking", "depository", "0.00", "3000.00")
            + two("heloc", "HELOC", "loan", "-100000.00", "-89385.00")   # +10615 paydown
            + two("cc", "Credit Card", "credit", "0.00", "-500.00")       # -500
            + two("401k", "401k", "brokerage", "0.00", "8000.00")         # shown separately
        )


def _sav_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(SavingsAdapter(), cache)


def test_savings_summary_envelope_and_reconciliation(tmp_path):
    server = build_server(_sav_service(tmp_path))
    result = _tool(server, "savings_summary")(start="2026-06-01", end="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    assert data["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    # money serialized as strings
    assert data["cashflow"]["income"] == "45976.73"
    assert data["cashflow"]["savings_rate"] == "0.2592"
    assert data["cashflow"]["rate_is_floored"] is False
    # 3000 - 500 + 10615 = 13115.00 ; gap = 13115.00 - 11916.80 = 1198.20
    assert data["net_worth_delta_cash_debt"] == "13115.00"
    assert data["reconciliation_gap"] == "1198.20"
    cd = {r["account_id"] for r in data["where_it_went"]["cash_and_debt"]}
    inv = {r["account_id"] for r in data["where_it_went"]["investments_shown_separately"]}
    assert cd == {"chk", "cc", "heloc"}
    assert inv == {"401k"}
    # nested Decimals inside list rows are stringified
    heloc = next(r for r in data["where_it_went"]["cash_and_debt"] if r["account_id"] == "heloc")
    assert heloc["delta"] == "10615.00"
    assert heloc["end"]["balance"] == "-89385.00"
    assert "take-home" in data["methodology"]


def test_savings_summary_bad_start_date(tmp_path):
    server = build_server(_sav_service(tmp_path))
    result = _tool(server, "savings_summary")(start="June 1", end="2026-06-30")
    assert result == {"error": "start must be YYYY-MM-DD"}


def test_savings_summary_bad_end_date(tmp_path):
    server = build_server(_sav_service(tmp_path))
    result = _tool(server, "savings_summary")(start="2026-06-01", end="nope")
    assert result == {"error": "end must be YYYY-MM-DD"}


def test_savings_summary_start_after_end(tmp_path):
    server = build_server(_sav_service(tmp_path))
    result = _tool(server, "savings_summary")(start="2026-06-30", end="2026-06-01")
    assert result == {"error": "start must not be after end"}


def test_savings_summary_error_on_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(SavingsAdapter):
        def get_cashflow(self, start, end):
            raise SessionExpiredError("expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "savings_summary")(start="2026-06-01", end="2026-06-30")
    assert "financial-mcp-login" in result["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py -k savings_summary -v`
Expected: FAIL — tool not registered / `DataService` has no `cashflow`.

- [ ] **Step 3: Write minimal implementation**

Add the uncached passthrough to `financial_mcp/data_service.py` (import `Cashflow`, `datetime`):

```python
import datetime
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Cashflow, Transaction
...
    def cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow:
        # Never cached — one lightweight window-parameterized live call.
        return self._adapter.get_cashflow(start, end)
```

In `financial_mcp/mcp_server.py`, import the savings module and add the tool (place beside `balance_delta`):

```python
from financial_mcp.analytics import balances, savings, spending
```

```python
    @server.tool()
    def savings_summary(start: str, end: str) -> dict:
        """Savings rate + "where it went" reconciliation for a window (YYYY-MM-DD, inclusive).

        Reports Monarch's authoritative cashflow savings rate (on take-home / net
        income) plus a true signed rate — Monarch floors negative rates at 0, so
        rate_is_floored flags when they diverge. Then reconciles that cash savings
        against the change in cash+debt account balances (net_worth_delta_cash_debt),
        naming the unattributed reconciliation_gap. Investment/real-estate balances
        are shown separately (mostly market movement, not saving). The methodology
        field explains what the rate does and does not include — always surface it
        when discussing the number. Use this for "what was my savings rate" and
        "where did my savings go" questions.
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
            cashflow = svc.cashflow(s, e)
            delta_rows = balances.balance_delta(svc.balance_history(), s, e)
            data = savings.summarize(cashflow, delta_rows)
            return _envelope("balance_history", _jsonify([data])[0], count=1)

        return _guard(_run)
```

Extend `_jsonify`'s `conv` at the bottom of `financial_mcp/mcp_server.py` to recurse into lists:

```python
def _jsonify(rows: list[dict]) -> list[dict]:
    """Convert Decimal values (including nested dicts and lists) to strings for JSON transport."""

    def conv(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v

    return [{k: conv(v) for k, v in r.items()} for r in rows]
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (all prior tests + 5 new savings_summary tests). Confirm `test_spending_summary_envelope_and_math` and the `balance_delta` tool tests still pass after the `_jsonify` list-recursion change.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/data_service.py financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: savings_summary MCP tool"
```

---

### Task 9: Live end-to-end verification + docs

**Run interactively against the real org — like Task 1, not a pytest task.** Closes the loop the spec's §5 opened and confirms the numbers match the Monarch dashboard (Goal #4 ground truth).

**Files:**
- Modify: `docs/specs/2026-08-09-monarch-mcp-savings-rate-where-it-went-design.md` (mark Status: Shipped + live-proven, record the real observed numbers)

- [ ] **Step 1: Confirm the suite is green**

Run: `uv run pytest -q`
Expected: all pass (129 prior + the new model/adapter/analytics/tool tests).

- [ ] **Step 2: Live-verify in Claude Desktop (fresh chat, after Cmd+Q restart)**

Against the real synced org, ask:
- "What was my savings rate in June 2026?" → expect `savings_rate` ≈ `0.2592`, `rate_is_floored: false`, and the methodology surfaced. Cross-check against the Monarch cashflow dashboard for June — the numbers must match penny-for-penny (income 45976.73, expense −34059.93, savings 11916.80).
- "What was my savings rate year-to-date 2026, and where did it go?" → expect the YTD floored case: `savings_rate_monarch: 0.0`, our `savings_rate` negative, `rate_is_floored: true`; a `reconciliation_gap`; and the 529/corporate-card duplicates absent from `cash_and_debt` (dropped by `include_in_net_worth`). Confirm no duplicate-displayName account is double-counted.
Record the observed numbers.

- [ ] **Step 3: Update the spec with shipped status + real numbers**

In `docs/specs/2026-08-09-monarch-mcp-savings-rate-where-it-went-design.md`, set `Status: Shipped + live-proven (2026-08-09)` and note the observed June rate, the YTD floored case, and that the reconciliation matched the Monarch dashboard.

- [ ] **Step 4: Commit**

```bash
git add docs/specs/2026-08-09-monarch-mcp-savings-rate-where-it-went-design.md
git commit -m "docs: savings_summary shipped + live-proven"
```

---

## Notes for the executor

- **Task 1 is DONE** (both queries live-verified 2026-08-09 — see spec §5). Do NOT re-probe. Task 9 is the closing live e2e.
- **Migration ordering is load-bearing.** Tasks 4 → 5 → 6 re-key the shipped `BalanceSnapshot` from `account` (displayName) to `account_id` + `display_name` + classification. The suite is intentionally RED between Task 4 and Task 6 Step 4 — each migration commit is a checkpoint, and only Task 6 Step 4 restores full green. Do not attempt to keep the whole suite green mid-migration by editing files a later task owns.
- **The shipped tool code in `mcp_server.py` (`balance_as_of`, `balance_delta`) does not change** — those tools pass rows straight through `_jsonify`/`_envelope`, so the row-key change is absorbed by their *tests*, not their code (Task 6). Confirm this holds; if a shipped tool needs a code edit, that is a signal something in Task 5's row shape drifted from what the tool assumed.
- Work on a fresh branch off `main` (e.g. `savings-rate-summary`); do not build on `main` directly.
- All named test files already exist — append to / edit them, don't recreate. `tests/test_savings_analytics.py` is the one new test file.
- `Decimal` quantization uses `ROUND_HALF_UP` to 4 dp for the rate; every other money value stays full-precision `Decimal` and is stringified only at the `_jsonify` boundary.

