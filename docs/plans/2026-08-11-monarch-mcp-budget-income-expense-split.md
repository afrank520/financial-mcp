# budget_status income/expense/transfer split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `budget_status` totals by Monarch's own category-group type (income/expense/transfer), relabel the income "over budget" flag as `favorable`, and return a `methodology` string — so the tool encodes Monarch's semantics and the narration can't mingle income into "spend."

**Architecture:** Thin enrichment of one existing path: add `group.type` to `GetCategories` → `Category.group_type` → the categories map `budget_status` already joins → per-row `type` + split totals + methodology. No new tool, no cache migration.

**Tech Stack:** Python 3.14, Pydantic v2 (`Decimal`), FastMCP over stdio, pytest. Live Monarch via `CommunityAdapter` (GraphQL + cookie auth); offline via stubs/`CsvAdapter`.

## Global Constraints

- **Nothing invented.** Every value stays Monarch's own. `group.type` is a label Monarch already assigns; `favorable` is a *relabel* of the existing `remaining < 0` comparison, not new math.
- **Live-probe before trusting reference-lib shapes.** The exact `group.type` enum strings AND the `favorable = remaining < 0` sign are confirmed live (Task 1) before the analytics layer locks them. (The balance-history / GET_BUDGETS 400 lesson.)
- **`methodology` string is byte-exact** — the literal below is the single source of truth; copy it verbatim.
- **Backward-compatible model:** `Category.group_type` defaults to `None`. No SCHEMA_VERSION bump — a warm old-shape `categories` cache reads `group_type` as `None` (→ `unknown` type) until the next `refresh_cache`.
- **Single-month by design** (unchanged): `get_budgets` maps `months[0]`.
- **The canonical methodology string (verbatim):**
  `Totals are split by Monarch's own income/expense/transfer category type. For expense categories, over_budget means you spent more than planned. For income, favorable means you earned more than planned — not overspending. Transfers are internal money movement, shown for completeness.`

---

## File Structure

- `financial_mcp/monarch/queries.py` — add `type` to `GET_CATEGORIES` group selection (Task 2).
- `financial_mcp/monarch/models.py` — `Category` gains `group_type: str | None = None` (Task 2).
- `financial_mcp/monarch/community_adapter.py` — `get_categories()` maps `group_type` (Task 2).
- `financial_mcp/analytics/budgets.py` — `budget_status` tags rows by type, splits totals, adds methodology (Task 3).
- `financial_mcp/mcp_server.py` — `budget_status` tool docstring updated to describe the split + methodology (Task 4).
- Tests: `tests/test_community_adapter.py` (Task 2), `tests/test_budgets_analytics.py` (Task 3, includes updating existing assertions for the new shape), `tests/test_mcp_tools.py` (Task 4).

---

## Task 1: Live-probe the category group `type` enum (INTERACTIVE)

**Files:** none (verification only). Run with the user against the live org.

**Why:** The design assumes `group.type` returns `income` / `expense` / `transfer` and that income's favorable case is `remaining < 0`. Both are drawn from the reference lib + prior live data, not re-probed. Confirm before the analytics layer trusts them.

- [ ] **Step 1:** With the user, add `type` to the `GetCategories` group selection in a scratch probe (or inspect a raw `categories` payload in Claude Desktop via a temporary query) and record the DISTINCT `group.type` string values the real org returns.
- [ ] **Step 2:** From the June 2026 budget data, confirm the sign: for an income category that earned MORE than planned (e.g. Paychecks), confirm Monarch's `remainingAmount` is NEGATIVE (so `favorable = remaining < 0` is correct). Record one real (planned, actual, remaining) triple for an income row.
- [ ] **Step 3:** Write the confirmed enum values + the income-sign result into the spec (§3.1 / §3.3) and this plan's Task 3 before implementing. If the enum strings differ from `income`/`expense`/`transfer`, update the analytics mapping constants in Task 3 accordingly.

**Deliverable:** confirmed enum strings + income-sign, recorded in the spec. No code committed in this task.

---

## Task 2: Query + model + adapter — surface `group_type`

**Files:**
- Modify: `financial_mcp/monarch/queries.py` (GET_CATEGORIES)
- Modify: `financial_mcp/monarch/models.py` (Category)
- Modify: `financial_mcp/monarch/community_adapter.py` (get_categories)
- Test: `tests/test_community_adapter.py`

**Interfaces:**
- Produces: `Category.group_type: str | None` — consumed by `budget_status` (Task 3) via the categories map.

- [ ] **Step 1: Update the adapter test to expect `group_type`**

In `tests/test_community_adapter.py`, `test_get_categories_maps_group` (~line 470), extend the stub payload and assertions:

```python
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
```

- [ ] **Step 2: Run it — verify it fails**

Run: `.venv/bin/python -m pytest tests/test_community_adapter.py::test_get_categories_maps_group -v`
Expected: FAIL (`Category` has no `group_type`, and/or query lacks `type`).

- [ ] **Step 3: Add `group_type` to the model**

In `financial_mcp/monarch/models.py`, `Category`:

```python
class Category(BaseModel):
    id: str
    name: str
    group: str | None = None
    group_type: str | None = None   # Monarch's own: "income" | "expense" | "transfer"
```

- [ ] **Step 4: Add `type` to the query**

In `financial_mcp/monarch/queries.py`, `GET_CATEGORIES`, add `type` to the group selection:

```python
GET_CATEGORIES = """
  query GetCategories {
    categories {
      id
      name
      group { id name type __typename }
      __typename
    }
  }
"""
```

- [ ] **Step 5: Map `group_type` in the adapter**

In `financial_mcp/monarch/community_adapter.py`, `get_categories()`:

```python
    def get_categories(self) -> list[Category]:
        data = self._run(self._transport("GetCategories", GET_CATEGORIES, {}))
        return [
            Category(
                id=c["id"],
                name=c["name"],
                group=(c.get("group") or {}).get("name"),
                group_type=(c.get("group") or {}).get("type"),
            )
            for c in data.get("categories") or []
        ]
```

- [ ] **Step 6: Run the adapter test — verify it passes**

Run: `.venv/bin/python -m pytest tests/test_community_adapter.py::test_get_categories_maps_group -v`
Expected: PASS

- [ ] **Step 7: Run the full suite (nothing else should break — `group_type` defaults to None)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add financial_mcp/monarch/queries.py financial_mcp/monarch/models.py financial_mcp/monarch/community_adapter.py tests/test_community_adapter.py
git commit -m "feat: surface Monarch category group.type as Category.group_type"
```

---

## Task 3: Analytics — split `budget_status` totals by type

**Files:**
- Modify: `financial_mcp/analytics/budgets.py`
- Test: `tests/test_budgets_analytics.py`

**Interfaces:**
- Consumes: `BudgetItem` (unchanged), `Category.group_type` (Task 2).
- Produces: `budget_status(items, categories, start, end) -> dict` with per-row `type`, income rows carrying `favorable` instead of `over_budget`, `totals` split into per-type sub-blocks, and a `methodology` string.

**Type mapping (from Task 1 live-probe — default values below; correct if Task 1 found different strings):** `expense`, `income`, `transfer`. Any category id absent from the map, or with `group_type is None`, or an unrecognized value → `"unknown"`.

- [ ] **Step 1: Update the existing tests for the new shape (they assert the OLD flat totals)**

The current `test_budgets_analytics.py` asserts a flat `totals` dict and rows without `type`. Update `_cats()` to carry `group_type`, and rewrite the three shape-dependent tests. Replace `_cats()` and the affected tests:

```python
def _cats():
    return [
        Category(id="c1", name="Groceries", group="Food & Dining", group_type="expense"),
        Category(id="c2", name="Rent", group="Housing", group_type="expense"),
    ]


def test_rows_joined_named_typed_and_flagged():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    groc = next(r for r in out["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"
    assert groc["type"] == "expense"
    assert groc["over_budget"] is True
    rent = next(r for r in out["categories"] if r["category"] == "Rent")
    assert rent["over_budget"] is False


def test_totals_split_by_type_expense_bucket():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    exp = out["totals"]["expense"]
    assert exp["budgeted"] == Decimal("2500.00")
    assert exp["actual"] == Decimal("-2620.00")
    assert exp["remaining"] == Decimal("-120.00")
    assert exp["over_budget"] is True
    # income/transfer/unknown buckets absent when no such rows
    assert set(out["totals"]) == {"expense"}


def test_methodology_present_and_exact():
    out = budgets.budget_status(_items(), _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["methodology"] == (
        "Totals are split by Monarch's own income/expense/transfer category type. "
        "For expense categories, over_budget means you spent more than planned. "
        "For income, favorable means you earned more than planned — not overspending. "
        "Transfers are internal money movement, shown for completeness."
    )
```

Update `test_unknown_category_id_falls_back_to_id` to also assert `type == "unknown"` and the `unknown` bucket, and rewrite `test_empty_budget_zeroed_totals_no_crash` to expect `totals == {}`:

```python
def test_unknown_category_id_falls_back_to_id_and_type():
    items = [BudgetItem(category_id="cX", budgeted=Decimal("50.00"),
                        actual=Decimal("0.00"), remaining=Decimal("50.00"))]
    out = budgets.budget_status(items, _cats(), D(2026, 6, 1), D(2026, 6, 30))
    row = out["categories"][0]
    assert row["category"] == "cX"      # id shown, not dropped
    assert row["group"] is None
    assert row["type"] == "unknown"
    assert row["over_budget"] is False  # literal; not claimed favorable
    assert out["totals"]["unknown"]["remaining"] == Decimal("50.00")


def test_empty_budget_no_totals_blocks():
    out = budgets.budget_status([], _cats(), D(2026, 6, 1), D(2026, 6, 30))
    assert out["categories"] == []
    assert out["totals"] == {}
    assert "methodology" in out
```

Add a dedicated income test:

```python
def test_income_row_flagged_favorable_when_over_earned():
    cats = [Category(id="i1", name="Paychecks", group="Income", group_type="income")]
    # earned MORE than planned -> Monarch remaining < 0 -> favorable
    items = [BudgetItem(category_id="i1", budgeted=Decimal("45000.00"),
                        actual=Decimal("45836.00"), remaining=Decimal("-836.00"))]
    out = budgets.budget_status(items, cats, D(2026, 6, 1), D(2026, 6, 30))
    row = out["categories"][0]
    assert row["type"] == "income"
    assert "over_budget" not in row
    assert row["favorable"] is True
    inc = out["totals"]["income"]
    assert inc["favorable"] is True
    assert "over_budget" not in inc
```

Keep `test_worst_over_budget_sorted_first` as-is (sort is unchanged).

- [ ] **Step 2: Run the tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_budgets_analytics.py -v`
Expected: the updated/new tests FAIL (old `budget_status` returns a flat `totals`, no `type`, no `methodology`).

- [ ] **Step 3: Rewrite `budget_status`**

Replace `financial_mcp/analytics/budgets.py` with:

```python
from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.monarch.models import BudgetItem, Category

METHODOLOGY = (
    "Totals are split by Monarch's own income/expense/transfer category type. "
    "For expense categories, over_budget means you spent more than planned. "
    "For income, favorable means you earned more than planned — not overspending. "
    "Transfers are internal money movement, shown for completeness."
)

_KNOWN_TYPES = {"income", "expense", "transfer"}


def _row_flag(row_type: str, remaining: Decimal) -> dict:
    """Type-appropriate flag: income -> favorable, everything else -> over_budget."""
    neg = remaining < 0
    return {"favorable": neg} if row_type == "income" else {"over_budget": neg}


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
        gt = c.group_type if c else None
        row_type = gt if gt in _KNOWN_TYPES else "unknown"
        row = {
            "category": c.name if c else it.category_id,
            "group": c.group if c else None,
            "type": row_type,
            "budgeted": it.budgeted,
            "actual": it.actual,
            "remaining": it.remaining,
        }
        row.update(_row_flag(row_type, it.remaining))
        rows.append(row)
    rows.sort(key=lambda r: r["remaining"])  # most-negative remaining (worst) first

    # Split totals by type; a type with no rows produces no sub-block.
    totals: dict[str, dict] = {}
    by_type: dict[str, list[BudgetItem]] = {}
    for it in items:
        c = by_id.get(it.category_id)
        gt = c.group_type if c else None
        t = gt if gt in _KNOWN_TYPES else "unknown"
        by_type.setdefault(t, []).append(it)
    for t, its in by_type.items():
        tb = sum((i.budgeted for i in its), Decimal("0"))
        ta = sum((i.actual for i in its), Decimal("0"))
        tr = sum((i.remaining for i in its), Decimal("0"))
        block = {"budgeted": tb, "actual": ta, "remaining": tr}
        block.update(_row_flag(t, tr))
        totals[t] = block

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "categories": rows,
        "totals": totals,
        "methodology": METHODOLOGY,
    }
```

- [ ] **Step 4: Run the budget tests — verify they pass**

Run: `.venv/bin/python -m pytest tests/test_budgets_analytics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/analytics/budgets.py tests/test_budgets_analytics.py
git commit -m "feat: split budget_status totals by Monarch category type + methodology"
```

---

## Task 4: Tool docstring + tool-layer test

**Files:**
- Modify: `financial_mcp/mcp_server.py` (`budget_status` tool docstring; note `count` still = len categories)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `budget_status` analytics (Task 3). The tool wrapper itself is unchanged in signature — only the docstring and a tool-layer shape test.

- [ ] **Step 1: Fix the currently-failing tool-layer test + fake, then extend coverage**

After Task 3, the suite has ONE red test: `tests/test_mcp_tools.py::test_budget_status_envelope_and_flags`. It asserts the OLD flat totals shape (`data["totals"]["remaining"]`, `data["totals"]["over_budget"]`) and its `BudgetAdapter` fake returns a `Category` with NO `group_type` (so the row is now `type: "unknown"`, not `expense`). Both must change.

**(a) Give the fake a typed expense category AND an income category.** In `tests/test_mcp_tools.py`, update the `BudgetAdapter` (around line 558-570) so `get_budgets` returns one expense row and one income row, and `get_categories` types them:

```python
class BudgetAdapter(FakeAdapter):
    def get_budgets(self, start, end):
        from financial_mcp.monarch.models import BudgetItem
        return [
            BudgetItem(category_id="c1", budgeted=Decimal("500.00"),
                       actual=Decimal("-620.00"), remaining=Decimal("-120.00")),   # expense, over
            BudgetItem(category_id="i1", budgeted=Decimal("45000.00"),
                       actual=Decimal("45836.00"), remaining=Decimal("-836.00")),  # income, favorable
        ]

    def get_categories(self):
        from financial_mcp.monarch.models import Category
        return [
            Category(id="c1", name="Groceries", group="Food & Dining", group_type="expense"),
            Category(id="i1", name="Paychecks", group="Income", group_type="income"),
        ]
```

**(b) Rewrite `test_budget_status_envelope_and_flags` to the new split shape** (replace the whole test body from `data = result["data"]` onward):

```python
def test_budget_status_envelope_and_flags(tmp_path):
    server = build_server(_budget_service(tmp_path))
    result = _tool(server, "budget_status")(start="2026-06-01", end="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    # totals split by Monarch type; stringified Decimals (proves _jsonify recursed)
    assert set(data["totals"]) <= {"expense", "income", "transfer", "unknown"}
    assert data["totals"]["expense"]["remaining"] == "-120.00"
    assert data["totals"]["expense"]["over_budget"] is True
    assert isinstance(data["totals"]["expense"]["budgeted"], str)
    # income bucket uses favorable, never over_budget
    assert data["totals"]["income"]["favorable"] is True
    assert "over_budget" not in data["totals"]["income"]
    # expense row keeps over_budget; income row uses favorable
    groc = next(r for r in data["categories"] if r["category"] == "Groceries")
    assert groc["type"] == "expense" and groc["over_budget"] is True
    pay = next(r for r in data["categories"] if r["category"] == "Paychecks")
    assert pay["type"] == "income" and "over_budget" not in pay and pay["favorable"] is True
    # methodology travels with the result
    assert data["methodology"].startswith("Totals are split by Monarch's own")
```

**(c) Add the unrecognized-non-None group_type coverage** (closes the Task-3 review gap — the "unknown" bucket is currently only exercised via an absent id, not a real-but-unrecognized type string). Add to `tests/test_budgets_analytics.py`:

```python
def test_unrecognized_group_type_falls_into_unknown_bucket():
    cats = [Category(id="x1", name="Odd", group="Weird", group_type="loan")]
    items = [BudgetItem(category_id="x1", budgeted=Decimal("10.00"),
                        actual=Decimal("-30.00"), remaining=Decimal("-20.00"))]
    out = budgets.budget_status(items, cats, D(2026, 6, 1), D(2026, 6, 30))
    row = out["categories"][0]
    assert row["type"] == "unknown"          # unrecognized string, not income
    assert row["over_budget"] is True        # literal flag, never favorable
    assert "favorable" not in row
    assert out["totals"]["unknown"]["remaining"] == Decimal("-20.00")
```

- [ ] **Step 2: Run it — verify the red test now passes and coverage is added**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k budget tests/test_budgets_analytics.py -v`
Expected: all pass (the previously-red `test_budget_status_envelope_and_flags` is green under the new shape; the new unrecognized-type test passes).

- [ ] **Step 3: Update the tool docstring**

In `financial_mcp/mcp_server.py`, `budget_status`, replace the docstring to describe the split + methodology:

```python
    def budget_status(start: str, end: str) -> dict:
        """Per-category budgeted vs. actual for a month window (YYYY-MM-DD, inclusive).

        The red/green scorecard: each budgeted category with budgeted, actual,
        remaining, a `type` (Monarch's own income/expense/transfer), and a flag —
        `over_budget` for expense/transfer, `favorable` for income (earning more
        than planned is good, not overspending). `totals` is split into per-type
        sub-blocks (expense/income/transfer/unknown), so "how much did I overspend"
        reads off totals.expense alone — income is never mingled in. A `methodology`
        string explains the split; surface it when discussing the numbers. All
        figures are Monarch's own, passed through as-is.
        """
```

- [ ] **Step 4: Run the tool tests — verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k budget -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py tests/test_budgets_analytics.py
git commit -m "feat: budget_status tool docstring for type split + tool-layer shape test"
```

---

## Task 5: Live accuracy verification (INTERACTIVE)

**Files:** none (verification only). Run with the user in Claude Desktop against the live org after cmd-Q to load the new build.

- [ ] **Step 1:** `refresh_cache("categories")` to pull the new `group.type` field into the cache (old cache reads as `unknown` until refreshed).
- [ ] **Step 2:** Run `budget_status` for June 2026. Confirm: income rows (Paychecks, Interest) have `type: "income"` and `favorable`, land in `totals.income`; expense rows in `totals.expense`; the "Transfer" row in `totals.transfer`. Confirm nothing lands in `unknown` that shouldn't.
- [ ] **Step 3:** Confirm the narration now reads "overspent" off `totals.expense` only, not the mingled total — the original problem is closed.
- [ ] **Step 4:** Record observed results in the spec §4 live-check, flip spec Status to Shipped + live-proven, and note any category that surfaced as `unknown` (a real Monarch data gap, surfaced honestly).

---

## Self-Review (done at plan-write time)

- **Spec coverage:** query field (Task 2), model field (Task 2), per-row type (Task 3), favorable relabel (Task 3), split totals (Task 3), methodology (Task 3), docstring (Task 4), unknown bucket (Task 3), live-probe of enum+sign (Task 1), empty→`{}` (Task 3), live verify (Task 5). All spec sections covered.
- **Placeholder scan:** none — every code step has literal content; the methodology string is byte-exact in Global Constraints and Task 3.
- **Type consistency:** `Category.group_type` (Task 2) is the exact name consumed in Task 3; `_KNOWN_TYPES` / `"unknown"` used consistently; flag helper `_row_flag` reused for rows and totals so the favorable/over_budget rule can't drift between them.
- **Return-shape breakage handled explicitly:** Task 3 Step 1 rewrites the three existing tests that assert the old flat totals (`test_totals_rolled_up` → split; `test_empty_budget_zeroed_totals_no_crash` → `{}`; `_cats()` gains `group_type`). This is the "intentional shape change, must be tested" item.
