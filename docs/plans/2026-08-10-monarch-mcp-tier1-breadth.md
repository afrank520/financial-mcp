# Tier 1 Breadth Implementation Plan — net-worth trend + budgets + real categories

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three read-only Monarch capabilities — `networth_trend`, `budget_status`, and a group-rollup upgrade to `category_summary` — extending the capability menu (goal #2, Tier 1).

**Architecture:** Same one-directional layering as every prior plan: `model → query → adapter → DataService → pure analytics → MCP tool`. Net-worth and categories fetch the full dataset and cache via the existing `DataService._get` path (net-worth windows in the analytics layer, mirroring `balance_history`). Budgets is window-parameterized, so it uses an **uncached passthrough** mirroring `cashflow`. `budget_status` and the categories rollup compose in the tool layer (like `savings_summary`), joining budget/transaction rows against the cached categories map.

**Tech Stack:** Python 3.14, Pydantic v2 (Decimal), FastMCP over stdio, SQLite cache, `uv run pytest`.

## Global Constraints

- **No invented data.** Report Monarch's own numbers as-is — net-worth `balance`, budget planned/actual/remaining. Never re-derive rollover or synthesize values.
- **Net-worth trend is net worth only** — no assets/liabilities split (the base `aggregateSnapshots` query returns net worth per date only).
- **Decimal penny-exactness end to end.** Parse Monarch JSON numbers through string: `Decimal(str(x))`. Money stays `Decimal` until the `_jsonify` boundary stringifies it.
- **Server computes all arithmetic** (change, totals, over-budget) — never leave sums for Claude.
- **Date-window tools reuse the exact validation strings:** `"start must be YYYY-MM-DD"`, `"end must be YYYY-MM-DD"`, `"start must not be after end"`.
- **CSV mode raises** `"<thing> is not available in CSV mode"` for each new adapter method.
- **No live calls in the test suite** — stub transports only. Live verification is a separate interactive task.
- **Operation names must match the query document exactly** (the balance-history 400 lesson).
- **Budgets caching deviation from spec §4:** budgets uses an uncached passthrough (window-parameterized, mirrors the shipped `cashflow` pattern), NOT the cached `_get` path. Net-worth and categories are cached.
- **Category-map keys:** transactions carry a category *name*; `Category` carries `name` + `group`. The categories rollup joins on **name**. Budgets rows carry a category *id*; `budget_status` joins on **id**.

---

### Task 1: Live-probe the three queries (INTERACTIVE — run with the user, not a subagent)

**Run interactively against the real org — like the savings-rate Task 1, not a pytest task.** Closes spec §5. Confirms the query documents, operation names, and JSON paths before any adapter code is written, so Task 4 uses verified strings.

**Files:**
- Modify: `docs/specs/2026-08-10-monarch-mcp-tier1-breadth-design.md` (record confirmed query strings + one sample payload each under §5)

- [ ] **Step 1: Probe `GetAggregateSnapshots`** against the live org (via the running server's transport or a scratch script the user runs). Confirm operation name, that `aggregateSnapshots` returns `{date, balance}` points, and the JSON path `data.aggregateSnapshots`. Record a 2-3 point sample.

- [ ] **Step 2: Probe `GetJointPlanningData`** (budgets). Confirm operation name and that `data.budgetData.monthlyAmountsByCategory[].{category{id}, monthlyAmounts[]{month, plannedCashFlowAmount, actualAmount, remainingAmount}}` is present for a recent month window. Record a sample row.

- [ ] **Step 3: Probe `GetCategories`.** Confirm `data.categories[].{id, name, group{id,name}}`. Record 2-3 sample categories including their group.

- [ ] **Step 4: Record findings** in the spec §5 (confirmed query text + sample payloads). If any operation name / field / path differs from the reference-lib shapes in the spec, note the correction — Task 3/4 uses the corrected version.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/2026-08-10-monarch-mcp-tier1-breadth-design.md
git commit -m "docs: live-verify Tier 1 breadth queries"
```

---

### Task 2: Models — `NetWorthSnapshot`, `Category`, `BudgetItem`

**Files:**
- Modify: `financial_mcp/monarch/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `NetWorthSnapshot(date: datetime.date, net_worth: Decimal)`
  - `Category(id: str, name: str, group: str | None = None)`
  - `BudgetItem(category_id: str, budgeted: Decimal, actual: Decimal, remaining: Decimal)`

- [ ] **Step 1: Write the failing test (append to `tests/test_models.py`)**

```python
def test_networth_snapshot_holds_decimal():
    from financial_mcp.monarch.models import NetWorthSnapshot
    s = NetWorthSnapshot(date=datetime.date(2026, 6, 30), net_worth=Decimal("123456.78"))
    assert s.net_worth == Decimal("123456.78")


def test_category_group_optional():
    from financial_mcp.monarch.models import Category
    c = Category(id="c1", name="Groceries", group="Food & Dining")
    assert c.group == "Food & Dining"
    assert Category(id="c2", name="Odd").group is None


def test_budget_item_fields():
    from financial_mcp.monarch.models import BudgetItem
    b = BudgetItem(category_id="c1", budgeted=Decimal("500.00"),
                   actual=Decimal("-620.00"), remaining=Decimal("-120.00"))
    assert b.category_id == "c1"
    assert b.remaining == Decimal("-120.00")
```

(Ensure `import datetime` and `from decimal import Decimal` are present at the top of the test file.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_models.py -k "networth_snapshot or category_group or budget_item" -v`
Expected: FAIL (ImportError — models not defined).

- [ ] **Step 3: Add the models to `financial_mcp/monarch/models.py`** (after `Cashflow`)

```python
class NetWorthSnapshot(BaseModel):
    date: datetime.date
    net_worth: Decimal


class Category(BaseModel):
    id: str
    name: str
    group: str | None = None


class BudgetItem(BaseModel):
    category_id: str
    budgeted: Decimal
    actual: Decimal
    remaining: Decimal
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/models.py tests/test_models.py
git commit -m "feat: add NetWorthSnapshot, Category, BudgetItem models"
```

---

### Task 3: Query constants

**Files:**
- Modify: `financial_mcp/monarch/queries.py`

**Interfaces:**
- Produces module constants `GET_AGGREGATE_SNAPSHOTS`, `GET_BUDGETS`, `GET_CATEGORIES`.

> Use the query text **confirmed in Task 1**. The strings below are the expected shapes from the reference lib; replace with Task 1's confirmed text if it differed.

- [ ] **Step 1: Append the three query constants to `financial_mcp/monarch/queries.py`**

```python
GET_AGGREGATE_SNAPSHOTS = """
  query GetAggregateSnapshots($filters: AggregateSnapshotFilters) {
    aggregateSnapshots(filters: $filters) {
      date
      balance
      __typename
    }
  }
"""

GET_BUDGETS = """
  query GetJointPlanningData($startDate: Date!, $endDate: Date!, $useLegacyGoals: Boolean!, $useV2Goals: Boolean!) {
    budgetData(startMonth: $startDate, endMonth: $endDate) {
      monthlyAmountsByCategory {
        category { id __typename }
        monthlyAmounts {
          month
          plannedCashFlowAmount
          actualAmount
          remainingAmount
          __typename
        }
        __typename
      }
      __typename
    }
  }
"""

GET_CATEGORIES = """
  query GetCategories {
    categories {
      id
      name
      group { id name __typename }
      __typename
    }
  }
"""
```

- [ ] **Step 2: Verify they import**

Run: `uv run python -c "from financial_mcp.monarch.queries import GET_AGGREGATE_SNAPSHOTS, GET_BUDGETS, GET_CATEGORIES; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add financial_mcp/monarch/queries.py
git commit -m "feat: add aggregate-snapshots, budgets, categories query constants"
```

---

### Task 4: Adapter methods (interface + Community + CSV + test-fake updates)

**This is the coordinated adapter task.** Adding abstract methods to `MonarchAdapter` breaks every subclass (both concrete adapters AND the test fakes) until each implements them, so all of that happens in this one task to keep the suite green.

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (3 new abstract methods)
- Modify: `financial_mcp/monarch/community_adapter.py` (implement all 3)
- Modify: `financial_mcp/monarch/csv_adapter.py` (raise for all 3)
- Modify: `tests/test_data_service.py` (add the 3 methods to `FakeAdapter` and `_StubAdapter`)
- Modify: `tests/test_mcp_tools.py` (add the 3 methods to `FakeAdapter`)
- Test: `tests/test_community_adapter.py` (mapping), `tests/test_csv_adapter.py` (raises)

**Interfaces:**
- Consumes: models from Task 2; query constants from Task 3.
- Produces (on `MonarchAdapter` and both concrete adapters):
  - `get_networth_history(self) -> list[NetWorthSnapshot]` (no window — fetches all; caller windows in analytics)
  - `get_categories(self) -> list[Category]`
  - `get_budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]`

- [ ] **Step 1: Write the failing adapter tests (append to `tests/test_community_adapter.py`)**

```python
from financial_mcp.monarch.models import BudgetItem, Category, NetWorthSnapshot  # add to imports


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
        return {"categories": [
            {"id": "c1", "name": "Groceries", "group": {"id": "g1", "name": "Food & Dining"}},
            {"id": "c2", "name": "Mystery", "group": None},
        ]}

    adapter = CommunityAdapter(SESSION, transport=stub)
    out = {c.id: c for c in adapter.get_categories()}
    assert out["c1"].name == "Groceries"
    assert out["c1"].group == "Food & Dining"
    assert out["c2"].group is None      # missing group block -> None, no crash


def test_get_budgets_maps_planned_actual_remaining():
    async def stub(operation, query, variables):
        assert operation == "GetJointPlanningData"
        assert variables["startDate"] == "2026-06-01"
        assert variables["endDate"] == "2026-06-30"
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
```

- [ ] **Step 2: Write the failing CSV tests (append to `tests/test_csv_adapter.py`)**

```python
import pytest


def _csv_adapter():
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    return CsvAdapter("unused.csv", "unused.csv")


def test_networth_history_not_available_in_csv():
    with pytest.raises(RuntimeError, match="not available in CSV mode"):
        _csv_adapter().get_networth_history()


def test_categories_not_available_in_csv():
    with pytest.raises(RuntimeError, match="not available in CSV mode"):
        _csv_adapter().get_categories()


def test_budgets_not_available_in_csv():
    import datetime
    with pytest.raises(RuntimeError, match="not available in CSV mode"):
        _csv_adapter().get_budgets(datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_community_adapter.py tests/test_csv_adapter.py -k "networth or categories or budgets" -v`
Expected: FAIL (methods not defined / abstract).

- [ ] **Step 4: Add abstract methods to `financial_mcp/monarch/adapter.py`**

Add to the `MonarchAdapter` ABC (and extend the import line to include the new models):

```python
    @abc.abstractmethod
    def get_networth_history(self) -> list[NetWorthSnapshot]: ...

    @abc.abstractmethod
    def get_categories(self) -> list[Category]: ...

    @abc.abstractmethod
    def get_budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]: ...
```

Update the import at the top of `adapter.py`:
```python
from financial_mcp.monarch.models import (
    Account, Balance, BalanceSnapshot, BudgetItem, Cashflow, Category, NetWorthSnapshot, Transaction,
)
```

- [ ] **Step 5: Implement in `financial_mcp/monarch/community_adapter.py`**

Extend the models import and the queries import, then add the three methods:

```python
# imports
from financial_mcp.monarch.models import (
    Account, Balance, BalanceSnapshot, BudgetItem, Cashflow, Category, NetWorthSnapshot, Transaction,
)
from financial_mcp.monarch.queries import (
    GET_ACCOUNT_SNAPSHOTS, GET_ACCOUNTS, GET_ACCOUNTS_MIN, GET_AGGREGATE_SNAPSHOTS,
    GET_BUDGETS, GET_CASHFLOW_SUMMARY, GET_CATEGORIES, GET_TRANSACTIONS,
)
```

```python
    def get_networth_history(self) -> list[NetWorthSnapshot]:
        # Fetch the full series (no window); callers window in the analytics layer,
        # mirroring get_balance_history. startDate/endDate omitted -> Monarch's full range.
        variables = {"filters": {"startDate": None, "endDate": None, "accountType": None}}
        data = self._run(
            self._transport("GetAggregateSnapshots", GET_AGGREGATE_SNAPSHOTS, variables)
        )
        out = [
            NetWorthSnapshot(
                date=datetime.date.fromisoformat(p["date"]),
                net_worth=Decimal(str(p["balance"])),
            )
            for p in data.get("aggregateSnapshots") or []
        ]
        out.sort(key=lambda s: s.date)
        return out

    def get_categories(self) -> list[Category]:
        data = self._run(self._transport("GetCategories", GET_CATEGORIES, {}))
        return [
            Category(
                id=c["id"],
                name=c["name"],
                group=(c.get("group") or {}).get("name"),
            )
            for c in data.get("categories") or []
        ]

    def get_budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]:
        variables = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "useLegacyGoals": False,
            "useV2Goals": True,
        }
        data = self._run(self._transport("GetJointPlanningData", GET_BUDGETS, variables))
        rows = (data.get("budgetData") or {}).get("monthlyAmountsByCategory") or []
        out: list[BudgetItem] = []
        for r in rows:
            cat_id = (r.get("category") or {}).get("id")
            months = r.get("monthlyAmounts") or []
            if not cat_id or not months:
                continue
            m = months[0]  # one month per window row
            out.append(BudgetItem(
                category_id=cat_id,
                budgeted=Decimal(str(m.get("plannedCashFlowAmount") or 0)),
                actual=Decimal(str(m.get("actualAmount") or 0)),
                remaining=Decimal(str(m.get("remainingAmount") or 0)),
            ))
        return out
```

- [ ] **Step 6: Implement raises in `financial_mcp/monarch/csv_adapter.py`**

Extend the models import, then add:

```python
    def get_networth_history(self) -> list[NetWorthSnapshot]:
        raise RuntimeError("net-worth history is not available in CSV mode")

    def get_categories(self) -> list[Category]:
        raise RuntimeError("categories are not available in CSV mode")

    def get_budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]:
        raise RuntimeError("budgets are not available in CSV mode")
```

- [ ] **Step 7: Update the test fakes so the suite stays green**

In `tests/test_data_service.py`, add to **both** `FakeAdapter` and `_StubAdapter`:

```python
    def get_networth_history(self):
        return []

    def get_categories(self):
        return []

    def get_budgets(self, start, end):
        return []
```

In `tests/test_mcp_tools.py`, add the same three methods to `FakeAdapter`.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all prior tests + the new adapter/CSV tests). If any pre-existing test fails, a fake is missing a method — fix it here.

- [ ] **Step 9: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/community_adapter.py \
        financial_mcp/monarch/csv_adapter.py tests/test_community_adapter.py \
        tests/test_csv_adapter.py tests/test_data_service.py tests/test_mcp_tools.py
git commit -m "feat: adapter methods for net-worth history, categories, budgets"
```

---

### Task 5: Net-worth trend — analytics + DataService + tool

**Files:**
- Create: `financial_mcp/analytics/networth.py`
- Test: `tests/test_networth_analytics.py` (new)
- Modify: `financial_mcp/data_service.py` (add `networth_history()`, extend `refresh`)
- Modify: `financial_mcp/mcp_server.py` (add `networth_trend` tool; import `networth`)
- Test: `tests/test_mcp_tools.py` (tool), `tests/test_data_service.py` (cached)

**Interfaces:**
- Consumes: `NetWorthSnapshot` (Task 2), `DataService._get`, `_envelope`, `_jsonify`, `_guard`.
- Produces:
  - `networth.networth_trend(snapshots: list[NetWorthSnapshot], start, end) -> dict`
  - `DataService.networth_history() -> list[NetWorthSnapshot]`
  - MCP tool `networth_trend(start: str, end: str) -> dict`

- [ ] **Step 1: Write the failing analytics tests (`tests/test_networth_analytics.py`)**

```python
import datetime
from decimal import Decimal

from financial_mcp.analytics import networth
from financial_mcp.monarch.models import NetWorthSnapshot

D = datetime.date


def _snaps():
    return [
        NetWorthSnapshot(date=D(2026, 1, 31), net_worth=Decimal("1250000.00")),
        NetWorthSnapshot(date=D(2026, 3, 31), net_worth=Decimal("1262500.55")),
        NetWorthSnapshot(date=D(2026, 6, 30), net_worth=Decimal("1300000.00")),
    ]


def test_endpoints_and_change():
    out = networth.networth_trend(_snaps(), D(2026, 1, 1), D(2026, 6, 30))
    assert out["window"] == {"start": "2026-01-01", "end": "2026-06-30"}
    assert out["start_point"]["net_worth"] == Decimal("1250000.00")
    assert out["end_point"]["net_worth"] == Decimal("1300000.00")
    assert out["change"]["net_worth"] == Decimal("50000.00")
    assert len(out["points"]) == 3


def test_window_filters_points():
    out = networth.networth_trend(_snaps(), D(2026, 2, 1), D(2026, 4, 30))
    assert [p["date"] for p in out["points"]] == ["2026-03-31"]
    assert out["start_point"]["net_worth"] == out["end_point"]["net_worth"]  # single point
    assert out["change"]["net_worth"] == Decimal("0")


def test_empty_window_returns_reason_not_crash():
    out = networth.networth_trend(_snaps(), D(2025, 1, 1), D(2025, 12, 31))
    assert out["start_point"] is None
    assert out["end_point"] is None
    assert out["change"] is None
    assert out["points"] == []
    assert out["reason"] == "no net-worth snapshots in window"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_networth_analytics.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `financial_mcp/analytics/networth.py`**

```python
from __future__ import annotations

import datetime

from financial_mcp.monarch.models import NetWorthSnapshot


def _point(s: NetWorthSnapshot) -> dict:
    return {"date": s.date.isoformat(), "net_worth": s.net_worth}


def networth_trend(
    snapshots: list[NetWorthSnapshot], start: datetime.date, end: datetime.date
) -> dict:
    window = {"start": start.isoformat(), "end": end.isoformat()}
    in_window = sorted(
        (s for s in snapshots if start <= s.date <= end), key=lambda s: s.date
    )
    if not in_window:
        return {
            "window": window,
            "start_point": None,
            "end_point": None,
            "change": None,
            "points": [],
            "reason": "no net-worth snapshots in window",
        }
    sp, ep = in_window[0], in_window[-1]
    return {
        "window": window,
        "start_point": _point(sp),
        "end_point": _point(ep),
        "change": {"net_worth": ep.net_worth - sp.net_worth},
        "points": [_point(s) for s in in_window],
    }
```

- [ ] **Step 4: Add the DataService accessor + refresh wiring (`financial_mcp/data_service.py`)**

Extend the models import to include `NetWorthSnapshot`, then add:

```python
    def networth_history(self) -> list[NetWorthSnapshot]:
        return self._get("networth", self._adapter.get_networth_history, NetWorthSnapshot)
```

In `refresh`, add `"networth"` to the default `types` list and a branch:
```python
            elif t == "networth":
                self.networth_history()
```

- [ ] **Step 5: Add the tool (`financial_mcp/mcp_server.py`)**

Extend the analytics import: `from financial_mcp.analytics import balances, networth, savings, spending`. Add beside `savings_summary`:

```python
    @server.tool()
    def networth_trend(start: str, end: str) -> dict:
        """Net worth at the start and end of a window (YYYY-MM-DD, inclusive) plus the change.

        Returns Monarch's own net-worth series: start_point, end_point, a
        server-computed change (end - start), and every monthly point in the
        window. This is net worth only (assets minus liabilities as one number);
        for WHY it changed (which accounts moved), use balance_delta or
        savings_summary. Use this for "how has my net worth changed" and
        glide-path questions.
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
            data = networth.networth_trend(svc.networth_history(), s, e)
            return _envelope("networth", _jsonify([data])[0], count=len(data["points"]))

        return _guard(_run)
```

- [ ] **Step 6: Write the tool + cache tests**

Append to `tests/test_mcp_tools.py`:

```python
class NetWorthAdapter(FakeAdapter):
    def get_networth_history(self):
        return [
            NetWorthSnapshot(date=datetime.date(2026, 1, 31), net_worth=Decimal("1250000.00")),
            NetWorthSnapshot(date=datetime.date(2026, 6, 30), net_worth=Decimal("1300000.00")),
        ]


def _nw_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(NetWorthAdapter(), cache)


def test_networth_trend_envelope_and_change(tmp_path):
    server = build_server(_nw_service(tmp_path))
    result = _tool(server, "networth_trend")(start="2026-01-01", end="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    assert data["start_point"]["net_worth"] == "1250000.00"   # stringified
    assert data["end_point"]["net_worth"] == "1300000.00"
    assert data["change"]["net_worth"] == "50000.00"
    assert result["count"] == 2


def test_networth_trend_bad_date(tmp_path):
    server = build_server(_nw_service(tmp_path))
    assert _tool(server, "networth_trend")(start="Jan 1", end="2026-06-30") == {
        "error": "start must be YYYY-MM-DD"
    }


def test_networth_trend_start_after_end(tmp_path):
    server = build_server(_nw_service(tmp_path))
    assert _tool(server, "networth_trend")(start="2026-06-30", end="2026-01-01") == {
        "error": "start must not be after end"
    }
```

Ensure `NetWorthSnapshot` is in the `test_mcp_tools.py` models import.

Append to `tests/test_data_service.py` (add `get_networth_history` returning real snapshots to a small stub, or extend `_StubAdapter`):

```python
def test_networth_history_is_cached(tmp_path):
    from financial_mcp.monarch.models import NetWorthSnapshot

    class _NWAdapter(_StubAdapter):
        def __init__(self):
            super().__init__([])
            self.nw_calls = 0

        def get_networth_history(self):
            self.nw_calls += 1
            return [NetWorthSnapshot(date=datetime.date(2026, 6, 30),
                                     net_worth=Decimal("1300000.00"))]

    adapter = _NWAdapter()
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)
    first = svc.networth_history()
    second = svc.networth_history()
    assert first == second
    assert adapter.nw_calls == 1                       # served from cache
    assert second[0].net_worth == Decimal("1300000.00")  # Decimal survives round-trip
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add financial_mcp/analytics/networth.py financial_mcp/data_service.py \
        financial_mcp/mcp_server.py tests/test_networth_analytics.py \
        tests/test_mcp_tools.py tests/test_data_service.py
git commit -m "feat: networth_trend tool + net-worth trend analytics"
```

---

### Task 6: Real categories — group rollup on `category_summary`

**Upgrades a shipped tool.** `spending.category_summary` and the `category_summary` MCP tool change signature and return shape (bare list → `{categories, groups}` object). Backward-compatible at the envelope level (Claude reads `data.categories`).

**Files:**
- Modify: `financial_mcp/analytics/spending.py` (`category_summary` gains a `categories` arg + group rollup)
- Modify: `financial_mcp/data_service.py` (add `categories()`, extend `refresh`)
- Modify: `financial_mcp/mcp_server.py` (`category_summary` tool passes `svc.categories()`, returns the object)
- Test: existing spending-analytics test (update old-shape assertions), `tests/test_mcp_tools.py` (`test_category_summary_tool` — update), `tests/test_data_service.py` (cached)

**Interfaces:**
- Consumes: `Category` (Task 2), existing `spending._group_totals`, `DataService._get`.
- Produces:
  - `spending.category_summary(txns, categories: list[Category]) -> dict` with keys `categories` (rows: `category, group, total, count`) and `groups` (rows: `group, total, count`).
  - `DataService.categories() -> list[Category]`.

- [ ] **Step 1: Locate the existing category_summary assertions**

Run: `grep -rn "category_summary" tests/`
Note every test asserting the OLD shape (a bare list) — they get updated in Step 6. The known tool test is `tests/test_mcp_tools.py::test_category_summary_tool`.

- [ ] **Step 2: Write the failing analytics tests (append to the spending-analytics test file — `tests/test_spending.py` if present, else create it)**

```python
import datetime
from decimal import Decimal

from financial_mcp.analytics import spending
from financial_mcp.monarch.models import Category, Transaction


def _txn(category, amount):
    d = datetime.date(2026, 1, 2)
    return Transaction(id=Transaction.synth_id(d, "M", Decimal(amount), "M"), date=d,
                       merchant="M", category=category, account="A", original_statement="M",
                       notes="", amount=Decimal(amount), tags=[], owner="A")


def _cats():
    return [
        Category(id="c1", name="Groceries", group="Food & Dining"),
        Category(id="c2", name="Restaurants", group="Food & Dining"),
    ]


def test_category_summary_adds_group_and_rolls_up():
    txns = [_txn("Groceries", "-100.00"), _txn("Restaurants", "-40.00"),
            _txn("Groceries", "-60.00")]
    out = spending.category_summary(txns, _cats())
    groc = next(r for r in out["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"
    assert groc["total"] == Decimal("-160.00")
    fd = next(g for g in out["groups"] if g["group"] == "Food & Dining")
    assert fd["total"] == Decimal("-200.00")   # -160 groceries + -40 restaurants
    assert fd["count"] == 3


def test_unmatched_category_is_uncategorized_not_dropped():
    txns = [_txn("Groceries", "-100.00"), _txn("Mystery Merch", "-25.00")]
    out = spending.category_summary(txns, _cats())
    myst = next(r for r in out["categories"] if r["category"] == "Mystery Merch")
    assert myst["group"] is None                       # row group null
    unc = next(g for g in out["groups"] if g["group"] == "Uncategorized")
    assert unc["total"] == Decimal("-25.00")           # bucketed, not dropped
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest -k "category_summary_adds_group or unmatched_category" -v`
Expected: FAIL (`category_summary` takes 1 arg / returns a list).

- [ ] **Step 4: Rewrite `category_summary` in `financial_mcp/analytics/spending.py`**

Add the import `from financial_mcp.monarch.models import Category, Transaction` (extend existing), and replace the current `category_summary`:

```python
def category_summary(txns: list[Transaction], categories: list[Category]) -> dict:
    name_to_group = {c.name: c.group for c in categories}
    base = _group_totals(txns, "category")  # [{category, total, count}], biggest net first

    cat_rows: list[dict] = []
    group_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    group_counts: dict[str, int] = defaultdict(int)
    for row in base:
        group = name_to_group.get(row["category"])
        cat_rows.append({**row, "group": group})
        bucket = group or "Uncategorized"
        group_totals[bucket] += row["total"]
        group_counts[bucket] += row["count"]

    groups = [
        {"group": g, "total": group_totals[g], "count": group_counts[g]}
        for g in group_totals
    ]
    groups.sort(key=lambda r: r["total"])  # biggest net spend first
    return {"categories": cat_rows, "groups": groups}
```

- [ ] **Step 5: Add the DataService accessor + refresh wiring (`financial_mcp/data_service.py`)**

Extend the models import to include `Category`, then add:

```python
    def categories(self) -> list[Category]:
        return self._get("categories", self._adapter.get_categories, Category)
```

In `refresh`, add `"categories"` to the default `types` list and a branch:
```python
            elif t == "categories":
                self.categories()
```

- [ ] **Step 6: Update the `category_summary` tool (`financial_mcp/mcp_server.py`)**

```python
    @server.tool()
    def category_summary() -> dict:
        """Net total and count per category AND per Monarch category group.

        Returns an envelope whose data has `categories` (one row per category
        with its group) and `groups` (group-level subtotals, e.g. Food & Dining =
        Groceries + Restaurants). Categories with no group match roll into
        "Uncategorized". This is the accurate all-time total across every
        transaction. Freshness metadata (as_of, age_hours, stale) is included.
        """
        return _guard(
            lambda: _envelope(
                "transactions",
                _jsonify([spending.category_summary(svc.transactions(), svc.categories())])[0],
                count=len(svc.transactions() and spending.category_summary(
                    svc.transactions(), svc.categories())["categories"]),
            )
        )
```

> Simpler equivalent if the double-call above reads awkwardly — compute once inside a `_run` helper (mirroring `spending_summary`):
```python
    @server.tool()
    def category_summary() -> dict:
        """<same docstring as above>"""
        def _run():
            data = spending.category_summary(svc.transactions(), svc.categories())
            return _envelope("transactions", _jsonify([data])[0], count=len(data["categories"]))
        return _guard(_run)
```
Use the `_run` form.

- [ ] **Step 7: Update the old-shape tool test in `tests/test_mcp_tools.py`**

Replace `test_category_summary_tool` so `FakeAdapter` returns a matching category and the assertion reads the new shape:

```python
def test_category_summary_tool(tmp_path):
    server = build_server(_service(tmp_path))
    result = _tool(server, "category_summary")()
    data = result["data"]
    groc = next(r for r in data["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"   # requires FakeAdapter.get_categories() below
    assert "as_of" in result
```

Update `FakeAdapter.get_categories` in `tests/test_mcp_tools.py` (added as a stub in Task 4) to return the matching category:
```python
    def get_categories(self):
        from financial_mcp.monarch.models import Category
        return [Category(id="c1", name="Groceries", group="Food & Dining")]
```

- [ ] **Step 8: Add the DataService cache test (append to `tests/test_data_service.py`)**

```python
def test_categories_is_cached(tmp_path):
    from financial_mcp.monarch.models import Category

    class _CatAdapter(_StubAdapter):
        def __init__(self):
            super().__init__([])
            self.cat_calls = 0

        def get_categories(self):
            self.cat_calls += 1
            return [Category(id="c1", name="Groceries", group="Food & Dining")]

    adapter = _CatAdapter()
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)
    assert svc.categories() == svc.categories()
    assert adapter.cat_calls == 1
```

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. Confirm no other test still asserts the old bare-list category_summary shape (Step 1's grep).

- [ ] **Step 10: Commit**

```bash
git add financial_mcp/analytics/spending.py financial_mcp/data_service.py \
        financial_mcp/mcp_server.py tests/test_mcp_tools.py tests/test_data_service.py \
        tests/test_spending.py
git commit -m "feat: category group rollup on category_summary"
```

---

### Task 7: Budgets — analytics + DataService passthrough + tool

**Files:**
- Create: `financial_mcp/analytics/budgets.py`
- Test: `tests/test_budgets_analytics.py` (new)
- Modify: `financial_mcp/data_service.py` (add uncached `budgets(start, end)`)
- Modify: `financial_mcp/mcp_server.py` (add `budget_status` tool; import `budgets`)
- Test: `tests/test_mcp_tools.py` (tool)

**Interfaces:**
- Consumes: `BudgetItem`, `Category` (Task 2); `DataService.categories()` (Task 6); `_envelope`, `_jsonify`, `_guard`.
- Produces:
  - `budgets.budget_status(items: list[BudgetItem], categories: list[Category], start, end) -> dict`
  - `DataService.budgets(start, end) -> list[BudgetItem]` (uncached passthrough)
  - MCP tool `budget_status(start: str, end: str) -> dict`

- [ ] **Step 1: Write the failing analytics tests (`tests/test_budgets_analytics.py`)**

```python
import datetime
from decimal import Decimal

from financial_mcp.analytics import budgets
from financial_mcp.monarch.models import BudgetItem, Category

D = datetime.date


def _cats():
    return [
        Category(id="c1", name="Groceries", group="Food & Dining"),
        Category(id="c2", name="Rent", group="Housing"),
    ]


def _items():
    return [
        BudgetItem(category_id="c1", budgeted=Decimal("500.00"),
                   actual=Decimal("-620.00"), remaining=Decimal("-120.00")),   # over
        BudgetItem(category_id="c2", budgeted=Decimal("2000.00"),
                   actual=Decimal("-2000.00"), remaining=Decimal("0.00")),     # on target
    ]


def test_rows_joined_named_and_flagged():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    groc = next(r for r in out["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"
    assert groc["over_budget"] is True
    rent = next(r for r in out["categories"] if r["category"] == "Rent")
    assert rent["over_budget"] is False


def test_worst_over_budget_sorted_first():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"][0]["category"] == "Groceries"   # remaining -120 < 0


def test_totals_rolled_up():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    t = out["totals"]
    assert t["budgeted"] == Decimal("2500.00")
    assert t["actual"] == Decimal("-2620.00")
    assert t["remaining"] == Decimal("-120.00")
    assert t["over_budget"] is True


def test_unknown_category_id_falls_back_to_id():
    items = [BudgetItem(category_id="cX", budgeted=Decimal("50.00"),
                        actual=Decimal("0.00"), remaining=Decimal("50.00"))]
    out = budgets.budget_status(items, _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"][0]["category"] == "cX"   # id shown, not dropped
    assert out["categories"][0]["group"] is None


def test_empty_budget_zeroed_totals_no_crash():
    out = budgets.budget_status([], _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"] == []
    assert out["totals"] == {"budgeted": Decimal("0"), "actual": Decimal("0"),
                             "remaining": Decimal("0"), "over_budget": False}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_budgets_analytics.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `financial_mcp/analytics/budgets.py`**

```python
from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.monarch.models import BudgetItem, Category


def budget_status(
    items: list[BudgetItem],
    categories: list[Category],
    start: datetime.date,
    end: datetime.date,
) -> dict:
    by_id = {c.id: c for c in categories}
    rows: list[dict] = []
    for it in items:
        c = by_id.get(it.category_id)
        rows.append({
            "category": c.name if c else it.category_id,
            "group": c.group if c else None,
            "budgeted": it.budgeted,
            "actual": it.actual,
            "remaining": it.remaining,
            "over_budget": it.remaining < 0,
        })
    rows.sort(key=lambda r: r["remaining"])  # most-negative remaining (worst) first

    total_budgeted = sum((it.budgeted for it in items), Decimal("0"))
    total_actual = sum((it.actual for it in items), Decimal("0"))
    total_remaining = sum((it.remaining for it in items), Decimal("0"))
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "categories": rows,
        "totals": {
            "budgeted": total_budgeted,
            "actual": total_actual,
            "remaining": total_remaining,
            "over_budget": total_remaining < 0,
        },
    }
```

- [ ] **Step 4: Add the DataService passthrough (`financial_mcp/data_service.py`)**

Extend the models import to include `BudgetItem`, then add (beside `cashflow`, the other uncached window call):

```python
    def budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]:
        # Never cached — window-parameterized, mirrors cashflow.
        return self._adapter.get_budgets(start, end)
```

- [ ] **Step 5: Add the tool (`financial_mcp/mcp_server.py`)**

Extend the analytics import: `from financial_mcp.analytics import balances, budgets, networth, savings, spending`. Add beside `networth_trend`:

```python
    @server.tool()
    def budget_status(start: str, end: str) -> dict:
        """Per-category budgeted vs. actual for a month window (YYYY-MM-DD, inclusive).

        The red/green scorecard: each budgeted category with budgeted, actual,
        remaining, and an over_budget flag (worst-over-budget first), plus a
        rolled-up totals block. The numbers are Monarch's own budget figures,
        passed through as-is. Use this for "how am I doing on my budget" and
        "which categories am I over on" questions.
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
            data = budgets.budget_status(svc.budgets(s, e), svc.categories(), s, e)
            return _envelope("categories", _jsonify([data])[0], count=len(data["categories"]))

        return _guard(_run)
```

> Freshness note: the envelope reports the `categories` cache (the tool's cached input — budgets itself is live). This mirrors `savings_summary` reporting `balance_history`.

- [ ] **Step 6: Write the tool tests (append to `tests/test_mcp_tools.py`)**

```python
class BudgetAdapter(FakeAdapter):
    def get_budgets(self, start, end):
        from financial_mcp.monarch.models import BudgetItem
        return [
            BudgetItem(category_id="c1", budgeted=Decimal("500.00"),
                       actual=Decimal("-620.00"), remaining=Decimal("-120.00")),
        ]

    def get_categories(self):
        from financial_mcp.monarch.models import Category
        return [Category(id="c1", name="Groceries", group="Food & Dining")]


def _budget_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(BudgetAdapter(), cache)


def test_budget_status_envelope_and_flags(tmp_path):
    server = build_server(_budget_service(tmp_path))
    result = _tool(server, "budget_status")(start="2026-06-01", end="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    groc = next(r for r in data["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"
    assert groc["budgeted"] == "500.00"          # stringified Decimal
    assert groc["over_budget"] is True
    assert data["totals"]["remaining"] == "-120.00"
    assert data["totals"]["over_budget"] is True


def test_budget_status_bad_date(tmp_path):
    server = build_server(_budget_service(tmp_path))
    assert _tool(server, "budget_status")(start="June", end="2026-06-30") == {
        "error": "start must be YYYY-MM-DD"
    }


def test_budget_status_start_after_end(tmp_path):
    server = build_server(_budget_service(tmp_path))
    assert _tool(server, "budget_status")(start="2026-06-30", end="2026-06-01") == {
        "error": "start must not be after end"
    }


def test_budget_status_error_on_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(BudgetAdapter):
        def get_budgets(self, start, end):
            raise SessionExpiredError("expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "budget_status")(start="2026-06-01", end="2026-06-30")
    assert "financial-mcp-login" in result["error"]
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add financial_mcp/analytics/budgets.py financial_mcp/data_service.py \
        financial_mcp/mcp_server.py tests/test_budgets_analytics.py tests/test_mcp_tools.py
git commit -m "feat: budget_status tool + budgets analytics"
```

---

### Task 8: Refresh-tool docstring + live end-to-end verification (INTERACTIVE)

**Run interactively against the real org — like Task 1. Closes goal #4 (accuracy vs Monarch UI ground truth).**

**Files:**
- Modify: `financial_mcp/mcp_server.py` (`refresh_cache` docstring lists the new data types)
- Modify: `docs/specs/2026-08-10-monarch-mcp-tier1-breadth-design.md` (Status: Shipped + live-proven; record observed numbers)

- [ ] **Step 1: Update the `refresh_cache` docstring** so the enumerated data types include the new cached ones:

```python
        copy. data_type is one of: transactions, balances, accounts,
        balance_history, networth, categories.
```

- [ ] **Step 2: Confirm the suite is green**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 3: Live-verify in Claude Desktop (fresh chat, after Cmd+Q restart)** against the real synced org:
  - **Net worth:** "How has my net worth changed year-to-date 2026?" → cross-check `start_point` / `end_point` / `change` against the Monarch net-worth graph endpoints.
  - **Budgets:** "How am I doing on my budget this month, and which categories am I over on?" → cross-check per-category budgeted/actual and the over-budget flags against the Monarch budget screen.
  - **Categories:** "What did I spend by category group over the last 90 days?" → confirm group subtotals (e.g. Food & Dining) match summing the member categories in Monarch, and that nothing lands wrongly in Uncategorized.
  Record the observed numbers.

- [ ] **Step 4: Update the spec** — set `Status: Shipped + live-proven (2026-08-10)` and record the observed net-worth endpoints, one budget over/under example, and one group subtotal that matched the UI.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py docs/specs/2026-08-10-monarch-mcp-tier1-breadth-design.md
git commit -m "docs: Tier 1 breadth shipped + live-proven; refresh_cache lists new types"
```

---

## Self-Review

**1. Spec coverage:**
- §1 net-worth trend → Tasks 2,3,4,5. Budgets → Tasks 2,3,4,7. Real categories → Tasks 2,3,4,6. ✓
- §3.1 endpoints+change+points → Task 5 analytics + tool. ✓
- §3.2 per-category budgeted/actual/remaining/over + totals, Monarch's numbers as-is, id-join → Task 4 adapter (id + amounts) + Task 7 analytics (join, sort, totals). ✓
- §3.3 group rollup, backward-compatible, Uncategorized bucket, object shape → Task 6. ✓
- §4 error handling (guard, date strings, CSV raises, empty windows) → Tasks 4,5,6,7 tests. ✓ Caching: net-worth+categories cached, budgets uncached (deviation flagged in Global Constraints). ✓ `refresh_cache` + refresh() updated → Tasks 5,6,8. ✓
- §5 live probe → Task 1; §4 live accuracy → Task 8. ✓
- §6 boundaries reflected in tool docstrings (net-worth-only routes "why" to balance_delta; budgets numbers as-is; Uncategorized) → Tasks 5,6,7. ✓

**2. Placeholder scan:** No TBD/TODO. Every code step has concrete content. Task 1/8 are explicitly interactive (verbatim-values-not-applicable — they record live findings). ✓

**3. Type consistency:** `NetWorthSnapshot(date, net_worth)`, `Category(id, name, group)`, `BudgetItem(category_id, budgeted, actual, remaining)` used identically in models, adapters, analytics, tools, tests. `category_summary(txns, categories) -> {categories, groups}` consistent across analytics (Task 6), tool (Task 6), tests. `budget_status(items, categories, start, end)` consistent (Task 7). DataService: `networth_history()` cached, `categories()` cached, `budgets(start, end)` uncached — matches tool call sites. `_envelope` data_type keys used: `"networth"` (Task 5), `"transactions"` (Task 6, category_summary reads txns), `"categories"` (Task 7, budgets reports the categories cache). ✓

**Note on category_summary count:** Step 6 uses the `_run` form computing the summary once — count = `len(data["categories"])`. ✓
