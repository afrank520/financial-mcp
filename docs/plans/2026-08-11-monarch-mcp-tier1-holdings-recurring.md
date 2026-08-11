# Tier-1 Holdings + Recurring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two read-only MCP tools — `holdings_summary` (current investment positions + portfolio totals) and `recurring_summary` (windowed recurring/subscription items + expected total) — completing Tier-1 breadth.

**Architecture:** Follows the existing one-directional layering: Monarch GraphQL → `MonarchAdapter` → `DataService` (+ SQLite cache) → pure analytics → MCP tool. Holdings fans out one `Web_GetHoldings` query per investment account (the `get_balance_history` fan-out shape) and is cached 1h; recurring is one date-bounded `Web_GetUpcomingRecurringTransactionItems` call and is an uncached pass-through per window (the `budget_status` shape).

**Tech Stack:** Python 3.14, Pydantic v2 with `Decimal`, FastMCP over stdio, pytest. Spec: `docs/specs/2026-08-11-monarch-mcp-tier1-holdings-recurring-design.md`.

## Global Constraints

- **Nothing invented.** Every emitted number is Monarch's own or a plain sum / subtraction of Monarch's own numbers (the `over_budget` / budget-totals precedent). No return %, no allocation %, no monthly-normalized estimate.
- **`gain_loss = value − cost_basis`** per position, both Monarch's own. The `gain_loss` key is ALWAYS present; it is `null` when `cost_basis is None` (never omitted, never guessed).
- **Holdings totals:** basis-less positions count toward total `value` but are EXCLUDED from total `cost_basis` and total `gain_loss`. Empty portfolio → `positions: []`, `totals: {}` (no zeroed block).
- **Recurring:** `total_expected` is a plain sum of window item amounts — NOT normalized. Empty window → `items: []`, `total_expected: "0"`.
- **Sorts:** holdings positions by `value` descending; recurring items by `date` ascending.
- **Price fallback:** `Holding.price` = Monarch's `currentPrice`, falling back to `closingPrice` when `currentPrice` is null (a selection between two Monarch fields, not a computation).
- **Holdings fan-out** selects only accounts with `holdingsCount > 0` (a field already in `GET_ACCOUNTS`).
- **Holdings cache TTL = 1 hour.** Recurring is uncached. **No `SCHEMA_VERSION` bump** (both are new types).
- **Methodology strings are byte-exact** (locked below); each travels on its tool's result.
  - Holdings: `Values, cost basis, and day-change are Monarch's own. gain_loss is value minus cost basis (Monarch's own numbers); positions with no cost basis are excluded from the cost-basis and gain_loss totals but still counted in total value.`
  - Recurring: `Amounts and frequencies are Monarch's own recurring-stream data. total_expected sums the amounts of all recurring items in the window (Monarch's own per-instance amounts); it is a sum, not a normalized monthly estimate.`
- **Decimal transport:** analytics return `Decimal`; tools pass results through `_jsonify` (already recurses dicts AND lists) so nested Decimals become strings.
- **Test command:** `.venv/bin/python -m pytest`. Run individual tests with `.venv/bin/python -m pytest tests/path::name -v`.

---

## File Structure

- `financial_mcp/monarch/models.py` — MODIFY: add `Holding`, `RecurringItem`.
- `financial_mcp/monarch/queries.py` — MODIFY: add `GET_HOLDINGS`, `GET_RECURRING`.
- `financial_mcp/monarch/adapter.py` — MODIFY: add `get_holdings`, `get_recurring` abstract methods.
- `financial_mcp/monarch/community_adapter.py` — MODIFY: implement both (live).
- `financial_mcp/monarch/csv_adapter.py` — MODIFY: implement both (raise, offline-unavailable).
- `financial_mcp/analytics/holdings.py` — CREATE: `holdings_summary`.
- `financial_mcp/analytics/recurring.py` — CREATE: `recurring_summary`.
- `financial_mcp/data_service.py` — MODIFY: `holdings()` cached, `recurring()` uncached, `Holding` in TypeVar, `refresh()` wiring.
- `financial_mcp/config.py` — MODIFY: `CacheTTLs.holdings` → `1 * HOUR`.
- `financial_mcp/mcp_server.py` — MODIFY: `holdings_summary` + `recurring_summary` tools, `refresh_cache` docstring.
- Tests: `tests/test_community_adapter.py`, `tests/test_holdings_analytics.py` (new), `tests/test_recurring_analytics.py` (new), `tests/test_data_service.py`, `tests/test_mcp_tools.py`.

---

### Task 1: Live query-probe (INTERACTIVE — collapse into Task 7)

**Status:** This task cannot run offline. Per the tier1-breadth and budget-split precedent, it is **collapsed into Task 7's live verification**. Build Tasks 2–6 on the monarchmoney v0.1.15 reference-lib queries (verified present in `.venv`), which degrade safely: unknown fields map to `None`, and the recurring `$filters` variable IS referenced in the query body (so it is not the GET_BUDGETS unused-variable case). Task 7's live run IS the probe that confirms `totalValue`/`basis` come through, the `frequency` strings, that `holdingsCount > 0` reliably identifies investment accounts, and that the recurring query is accepted with `filters: null`.

- [ ] No offline work. Proceed to Task 2.

---

### Task 2: Models — `Holding` + `RecurringItem`

**Files:**
- Modify: `financial_mcp/monarch/models.py`
- Test: `tests/test_holdings_analytics.py` (new — model round-trip lives here alongside its analytics)

**Interfaces:**
- Produces: `Holding(account: str, ticker: str|None, name: str, quantity: Decimal, price: Decimal, value: Decimal, cost_basis: Decimal|None, day_change_dollars: Decimal|None, day_change_percent: Decimal|None)` and `RecurringItem(date: datetime.date, merchant: str, amount: Decimal, frequency: str|None, account: str|None, category: str|None, is_past: bool)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_holdings_analytics.py` with just this test for now:
```python
import datetime
from decimal import Decimal

from financial_mcp.monarch.models import Holding, RecurringItem


def test_holding_and_recurring_models_construct():
    h = Holding(
        account="Brokerage", ticker="VTI", name="Vanguard Total Stock Market ETF",
        quantity=Decimal("120.5"), price=Decimal("268.40"), value=Decimal("32341.20"),
        cost_basis=Decimal("25000.00"),
        day_change_dollars=Decimal("180.75"), day_change_percent=Decimal("0.56"),
    )
    assert h.cost_basis == Decimal("25000.00")
    # nullable fields default-safe
    h2 = Holding(
        account="IRA", ticker=None, name="Manual Gold",
        quantity=Decimal("1"), price=Decimal("0"), value=Decimal("1900.00"),
        cost_basis=None, day_change_dollars=None, day_change_percent=None,
    )
    assert h2.ticker is None and h2.cost_basis is None

    r = RecurringItem(
        date=datetime.date(2026, 6, 3), merchant="Netflix", amount=Decimal("-15.99"),
        frequency="monthly", account="Credit Card", category="Entertainment", is_past=True,
    )
    assert r.date == datetime.date(2026, 6, 3)
    assert r.frequency == "monthly"
    r2 = RecurringItem(
        date=datetime.date(2026, 6, 4), merchant="Unknown", amount=Decimal("0"),
        frequency=None, account=None, category=None, is_past=False,
    )
    assert r2.account is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_holdings_analytics.py::test_holding_and_recurring_models_construct -v`
Expected: FAIL with `ImportError: cannot import name 'Holding'`.

- [ ] **Step 3: Write minimal implementation**

Append to `financial_mcp/monarch/models.py` (after `BudgetItem`):
```python
class Holding(BaseModel):
    account: str                          # displayName of the investment account
    ticker: str | None = None             # None for manual/unlisted holdings
    name: str
    quantity: Decimal
    price: Decimal                         # Monarch's currentPrice (fallback: closingPrice)
    value: Decimal                         # Monarch's own totalValue (pass-through)
    cost_basis: Decimal | None = None      # Monarch's basis; None when Monarch has none
    day_change_dollars: Decimal | None = None    # Monarch's securityPriceChangeDollars
    day_change_percent: Decimal | None = None    # Monarch's securityPriceChangePercent


class RecurringItem(BaseModel):
    date: datetime.date                    # this instance's date
    merchant: str
    amount: Decimal                        # this instance's amount (Monarch's own, signed)
    frequency: str | None = None           # stream frequency: "monthly" | "weekly" | ...
    account: str | None = None             # displayName being charged
    category: str | None = None
    is_past: bool                          # Monarch's isPast
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_holdings_analytics.py::test_holding_and_recurring_models_construct -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/models.py tests/test_holdings_analytics.py
git commit -m "feat: Holding + RecurringItem models"
```

---

### Task 3: Query constants — `GET_HOLDINGS` + `GET_RECURRING`

**Files:**
- Modify: `financial_mcp/monarch/queries.py`
- Test: `tests/test_community_adapter.py` (assert the constants exist and carry required fields)

**Interfaces:**
- Produces: module-level strings `GET_HOLDINGS`, `GET_RECURRING` importable from `financial_mcp.monarch.queries`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_community_adapter.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_community_adapter.py::test_holdings_and_recurring_query_constants_present -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'GET_HOLDINGS'`.

- [ ] **Step 3: Write minimal implementation**

Append to `financial_mcp/monarch/queries.py`:
```python
GET_HOLDINGS = """
  query Web_GetHoldings($input: PortfolioInput) {
    portfolio(input: $input) {
      aggregateHoldings {
        edges {
          node {
            id
            quantity
            basis
            totalValue
            securityPriceChangeDollars
            securityPriceChangePercent
            security {
              id
              name
              type
              ticker
              typeDisplay
              currentPrice
              closingPrice
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
  }
"""

GET_RECURRING = """
  query Web_GetUpcomingRecurringTransactionItems($startDate: Date!, $endDate: Date!, $filters: RecurringTransactionFilter) {
    recurringTransactionItems(startDate: $startDate, endDate: $endDate, filters: $filters) {
      stream {
        id
        frequency
        amount
        isApproximate
        merchant { id name __typename }
        __typename
      }
      date
      isPast
      transactionId
      amount
      amountDiff
      category { id name __typename }
      account { id displayName __typename }
      __typename
    }
  }
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_community_adapter.py::test_holdings_and_recurring_query_constants_present -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/queries.py tests/test_community_adapter.py
git commit -m "feat: GET_HOLDINGS + GET_RECURRING query constants"
```

---

### Task 4: Adapter methods — ABC + CommunityAdapter + CsvAdapter + all fakes

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (add 2 abstract methods)
- Modify: `financial_mcp/monarch/community_adapter.py` (implement both, live)
- Modify: `financial_mcp/monarch/csv_adapter.py` (implement both, raise)
- Modify: `tests/test_mcp_tools.py` (`FakeAdapter`), `tests/test_data_service.py` (`FakeAdapter` + `_StubAdapter`) — add the two methods so they stay instantiable
- Test: `tests/test_community_adapter.py`

**Interfaces:**
- Consumes: `GET_HOLDINGS`, `GET_RECURRING` (Task 3); `Holding`, `RecurringItem` (Task 2); existing `GET_ACCOUNTS`.
- Produces: `MonarchAdapter.get_holdings() -> list[Holding]` and `MonarchAdapter.get_recurring(start: datetime.date, end: datetime.date) -> list[RecurringItem]`, implemented on `CommunityAdapter`; `CsvAdapter` raises `RuntimeError`.

**IMPORTANT:** Adding abstract methods to `MonarchAdapter` makes every existing subclass un-instantiable until it implements them. This task MUST update `CsvAdapter` and all three test fakes (`tests/test_mcp_tools.py::FakeAdapter`, `tests/test_data_service.py::FakeAdapter`, `tests/test_data_service.py::_StubAdapter`) in the same commit, or the whole suite fails to collect. The `Partial` subclass in `tests/test_adapter_interface.py` intentionally stays abstract — do NOT change it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_community_adapter.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_community_adapter.py::test_get_holdings_fans_out_over_accounts_with_holdings tests/test_community_adapter.py::test_get_recurring_maps_items -v`
Expected: FAIL with `AttributeError: 'CommunityAdapter' object has no attribute 'get_holdings'`.

- [ ] **Step 3a: Extend the ABC**

In `financial_mcp/monarch/adapter.py`, add the imports `Holding, RecurringItem` to the models import, and add after `get_budgets`:
```python
    @abc.abstractmethod
    def get_holdings(self) -> list[Holding]: ...

    @abc.abstractmethod
    def get_recurring(self, start: datetime.date, end: datetime.date) -> list[RecurringItem]: ...
```

- [ ] **Step 3b: Implement on CommunityAdapter**

In `financial_mcp/monarch/community_adapter.py`: add `GET_HOLDINGS, GET_RECURRING` to the queries import and `Holding, RecurringItem` to the models import. Add these methods to the class:
```python
    def get_holdings(self) -> list[Holding]:
        accts = self._run(self._transport("GetAccounts", GET_ACCOUNTS, {}))
        today = self._today().isoformat()
        out: list[Holding] = []
        for a in accts.get("accounts", []):
            if not (a.get("holdingsCount") or 0):
                continue
            account_name = a.get("displayName", "")
            variables = {"input": {
                "accountIds": [a["id"]],
                "startDate": today,
                "endDate": today,
                "includeHiddenHoldings": True,
            }}
            data = self._run(self._transport("Web_GetHoldings", GET_HOLDINGS, variables))
            port = (data.get("portfolio") or {})
            edges = ((port.get("aggregateHoldings") or {}).get("edges")) or []
            for edge in edges:
                node = edge.get("node") or {}
                sec = node.get("security") or {}
                price = sec.get("currentPrice")
                if price is None:
                    price = sec.get("closingPrice")
                basis = node.get("basis")
                dcd = node.get("securityPriceChangeDollars")
                dcp = node.get("securityPriceChangePercent")
                out.append(Holding(
                    account=account_name,
                    ticker=sec.get("ticker"),
                    name=sec.get("name") or "",
                    quantity=Decimal(str(node.get("quantity") or 0)),
                    price=Decimal(str(price or 0)),
                    value=Decimal(str(node.get("totalValue") or 0)),
                    cost_basis=None if basis is None else Decimal(str(basis)),
                    day_change_dollars=None if dcd is None else Decimal(str(dcd)),
                    day_change_percent=None if dcp is None else Decimal(str(dcp)),
                ))
        return out

    def get_recurring(self, start: datetime.date, end: datetime.date) -> list[RecurringItem]:
        variables = {"startDate": start.isoformat(), "endDate": end.isoformat(), "filters": None}
        data = self._run(
            self._transport("Web_GetUpcomingRecurringTransactionItems", GET_RECURRING, variables)
        )
        out: list[RecurringItem] = []
        for it in data.get("recurringTransactionItems") or []:
            stream = it.get("stream") or {}
            merchant = (stream.get("merchant") or {}).get("name") or ""
            out.append(RecurringItem(
                date=datetime.date.fromisoformat(it["date"]),
                merchant=merchant,
                amount=Decimal(str(it.get("amount") or 0)),
                frequency=stream.get("frequency"),
                account=(it.get("account") or {}).get("displayName"),
                category=(it.get("category") or {}).get("name"),
                is_past=bool(it.get("isPast")),
            ))
        return out
```

- [ ] **Step 3c: Implement on CsvAdapter (raise)**

In `financial_mcp/monarch/csv_adapter.py`, add after `get_budgets`:
```python
    def get_holdings(self) -> list[Holding]:
        raise RuntimeError("holdings are not available in CSV mode")

    def get_recurring(self, start: datetime.date, end: datetime.date) -> list[RecurringItem]:
        raise RuntimeError("recurring transactions are not available in CSV mode")
```
Add `Holding, RecurringItem` to the models import in that file.

- [ ] **Step 3d: Update the three test fakes**

In `tests/test_mcp_tools.py::FakeAdapter` add:
```python
    def get_holdings(self):
        return []

    def get_recurring(self, start, end):
        return []
```
In `tests/test_data_service.py::FakeAdapter` add the identical two methods. In `tests/test_data_service.py::_StubAdapter` add the identical two methods (single-line bodies matching that class's style, e.g. `def get_holdings(self): return []`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_community_adapter.py tests/test_data_service.py tests/test_mcp_tools.py tests/test_adapter_interface.py -q`
Expected: PASS (all fakes instantiate; the two new adapter tests pass; `test_adapter_interface` still sees `Partial` as abstract).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/csv_adapter.py tests/test_community_adapter.py tests/test_data_service.py tests/test_mcp_tools.py
git commit -m "feat: get_holdings (fan-out) + get_recurring adapter methods"
```

---

### Task 5: Holdings — analytics + DataService (cached, TTL 1h) + `holdings_summary` tool

**Files:**
- Create: `financial_mcp/analytics/holdings.py`
- Modify: `financial_mcp/config.py` (`CacheTTLs.holdings` → `1 * HOUR`)
- Modify: `financial_mcp/data_service.py` (`holdings()`, `Holding` in TypeVar + import, `refresh()` wiring)
- Modify: `financial_mcp/mcp_server.py` (`holdings_summary` tool, import `holdings` analytics, `refresh_cache` docstring adds `holdings`)
- Test: `tests/test_holdings_analytics.py`, `tests/test_data_service.py`, `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `Holding` (Task 2); `svc.holdings()` returns `list[Holding]`.
- Produces: `holdings.holdings_summary(holdings: list[Holding]) -> dict` with keys `positions` (list), `totals` (dict, `{}` when empty), `methodology` (str); `DataService.holdings() -> list[Holding]`; MCP tool `holdings_summary() -> dict`.

- [ ] **Step 1: Write the failing analytics test**

Add to `tests/test_holdings_analytics.py`:
```python
from financial_mcp.analytics import holdings as holdings_analytics


def _h(account, name, value, basis, ticker="X"):
    return Holding(
        account=account, ticker=ticker, name=name,
        quantity=Decimal("1"), price=Decimal("1"), value=Decimal(value),
        cost_basis=None if basis is None else Decimal(basis),
        day_change_dollars=None, day_change_percent=None,
    )


def test_holdings_summary_gain_loss_totals_and_sort():
    out = holdings_analytics.holdings_summary([
        _h("Brokerage", "Small", "1000", "800"),
        _h("Brokerage", "Big", "5000", "4000"),
        _h("IRA", "NoBasis", "300", None, ticker=None),
    ])
    # sorted by value desc
    assert [p["name"] for p in out["positions"]] == ["Big", "Small", "NoBasis"]
    # per-position gain_loss = value - cost_basis; null when no basis (key present)
    big = out["positions"][0]
    assert big["gain_loss"] == Decimal("1000")
    nob = out["positions"][2]
    assert nob["gain_loss"] is None
    assert "gain_loss" in nob
    # totals: value includes ALL; basis/gain_loss EXCLUDE the basis-less position
    assert out["totals"]["value"] == Decimal("6300")
    assert out["totals"]["cost_basis"] == Decimal("4800")
    assert out["totals"]["gain_loss"] == Decimal("1200")
    assert out["methodology"].startswith("Values, cost basis, and day-change are Monarch's own.")


def test_holdings_summary_empty_has_no_totals_block():
    out = holdings_analytics.holdings_summary([])
    assert out["positions"] == []
    assert out["totals"] == {}
    assert "methodology" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_holdings_analytics.py -v`
Expected: FAIL with `ImportError`/`ModuleNotFoundError` for `financial_mcp.analytics.holdings`.

- [ ] **Step 3: Write the analytics module**

Create `financial_mcp/analytics/holdings.py`:
```python
from __future__ import annotations

from decimal import Decimal

from financial_mcp.monarch.models import Holding

METHODOLOGY = (
    "Values, cost basis, and day-change are Monarch's own. gain_loss is value "
    "minus cost basis (Monarch's own numbers); positions with no cost basis are "
    "excluded from the cost-basis and gain_loss totals but still counted in total value."
)


def holdings_summary(holdings: list[Holding]) -> dict:
    positions: list[dict] = []
    for h in holdings:
        gain_loss = None if h.cost_basis is None else h.value - h.cost_basis
        positions.append({
            "account": h.account,
            "ticker": h.ticker,
            "name": h.name,
            "quantity": h.quantity,
            "price": h.price,
            "value": h.value,
            "cost_basis": h.cost_basis,
            "gain_loss": gain_loss,
            "day_change_dollars": h.day_change_dollars,
            "day_change_percent": h.day_change_percent,
        })
    positions.sort(key=lambda p: p["value"], reverse=True)

    totals: dict = {}
    if holdings:
        with_basis = [h for h in holdings if h.cost_basis is not None]
        totals = {
            "value": sum((h.value for h in holdings), Decimal("0")),
            "cost_basis": sum((h.cost_basis for h in with_basis), Decimal("0")),
            "gain_loss": sum((h.value - h.cost_basis for h in with_basis), Decimal("0")),
        }

    return {"positions": positions, "totals": totals, "methodology": METHODOLOGY}
```

- [ ] **Step 4: Run analytics test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_holdings_analytics.py -v`
Expected: PASS.

- [ ] **Step 5: Wire DataService (cached) — write the failing test**

Add to `tests/test_data_service.py`:
```python
def test_holdings_is_cached(tmp_path):
    from financial_mcp.monarch.models import Holding

    class _HAdapter(_StubAdapter):
        def __init__(self):
            super().__init__([])
            self.holdings_calls = 0
        def get_holdings(self):
            self.holdings_calls += 1
            return [Holding(account="B", ticker="VTI", name="VTI",
                            quantity=Decimal("1"), price=Decimal("10"), value=Decimal("10"),
                            cost_basis=Decimal("8"))]

    adapter = _HAdapter()
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)
    first = svc.holdings()
    second = svc.holdings()          # within TTL -> served from cache
    assert adapter.holdings_calls == 1
    assert second[0].value == Decimal("10")   # round-trips through JSON cache


def test_cachettls_holdings_is_one_hour():
    from financial_mcp.config import HOUR
    assert CacheTTLs().holdings == 1 * HOUR
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_data_service.py::test_holdings_is_cached tests/test_data_service.py::test_cachettls_holdings_is_one_hour -v`
Expected: FAIL (`AttributeError: 'DataService' object has no attribute 'holdings'` and TTL assertion).

- [ ] **Step 7: Implement DataService + config**

In `financial_mcp/config.py`, change `holdings: int = 12 * HOUR` to `holdings: int = 1 * HOUR`.

In `financial_mcp/data_service.py`: add `Holding` to the models import; extend the TypeVar to
```python
T = TypeVar("T", Account, Balance, BalanceSnapshot, Transaction, NetWorthSnapshot, Category, Holding)
```
add the accessor after `categories()`:
```python
    def holdings(self) -> list[Holding]:
        return self._get("holdings", self._adapter.get_holdings, Holding)
```
and wire `refresh()`: add `"holdings"` to the default `types` list and add the dispatch branch:
```python
            elif t == "holdings":
                self.holdings()
```

- [ ] **Step 8: Run DataService tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_data_service.py -q`
Expected: PASS.

- [ ] **Step 9: Add the tool — write the failing test**

Add to `tests/test_mcp_tools.py` (extend the module's `FakeAdapter.get_holdings` to return real positions is not needed globally; instead use a dedicated adapter). Add:
```python
def test_holdings_summary_tool_envelope_and_totals(tmp_path):
    from financial_mcp.monarch.models import Holding

    class _HAdapter(FakeAdapter):
        def get_holdings(self):
            return [
                Holding(account="Brokerage", ticker="VTI", name="VTI",
                        quantity=Decimal("2"), price=Decimal("100"), value=Decimal("200"),
                        cost_basis=Decimal("150"),
                        day_change_dollars=Decimal("3"), day_change_percent=Decimal("1.5")),
                Holding(account="IRA", ticker=None, name="Manual",
                        quantity=Decimal("1"), price=Decimal("50"), value=Decimal("50"),
                        cost_basis=None),
            ]

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(_HAdapter(), cache)
    server = build_server(svc)
    data = _tool(server, "holdings_summary")()
    assert data["count"] == 2
    body = data["data"]
    assert body["positions"][0]["ticker"] == "VTI"        # biggest value first
    # Decimals arrive as strings
    assert body["totals"]["value"] == "250"
    assert body["totals"]["cost_basis"] == "150"
    assert body["totals"]["gain_loss"] == "50"
    assert isinstance(body["positions"][0]["value"], str)
    assert body["positions"][1]["gain_loss"] is None      # basis-less, key present, null
    assert "methodology" in body
```

- [ ] **Step 10: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py::test_holdings_summary_tool_envelope_and_totals -v`
Expected: FAIL (`KeyError`/tool not found — `holdings_summary` not registered).

- [ ] **Step 11: Implement the tool**

In `financial_mcp/mcp_server.py`: add `holdings` to the analytics import line (`from financial_mcp.analytics import balances, budgets, holdings, networth, savings, spending`). Register the tool (place it after `budget_status`):
```python
    @server.tool()
    def holdings_summary() -> dict:
        """Current investment holdings: each position plus portfolio totals.

        Returns an envelope whose data has `positions` (one per holding:
        account, ticker, name, quantity, price, value, cost_basis, gain_loss,
        day_change_dollars, day_change_percent), `totals` (portfolio value,
        cost_basis, gain_loss), and a `methodology` string. gain_loss is
        value − cost_basis (Monarch's own numbers) and is null when Monarch has
        no cost basis for a position; such positions count toward total value
        but not toward total cost_basis/gain_loss. All figures are Monarch's
        own — no returns %, allocation %, or other derived ratios. Positions are
        largest-value first. Surface the methodology when discussing the numbers.
        """
        def _run():
            data = holdings.holdings_summary(svc.holdings())
            return _envelope("holdings", _jsonify([data])[0], count=len(data["positions"]))
        return _guard(_run)
```
Update the `refresh_cache` docstring's data-type list to include `holdings`:
```
        balance_history, networth, categories, holdings.
```

- [ ] **Step 12: Run the tool test + full suite**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py::test_holdings_summary_tool_envelope_and_totals -v && .venv/bin/python -m pytest -q`
Expected: PASS; whole suite green.

- [ ] **Step 13: Commit**

```bash
git add financial_mcp/analytics/holdings.py financial_mcp/config.py financial_mcp/data_service.py financial_mcp/mcp_server.py tests/test_holdings_analytics.py tests/test_data_service.py tests/test_mcp_tools.py
git commit -m "feat: holdings_summary analytics + cached DataService + MCP tool"
```

---

### Task 6: Recurring — analytics + DataService (uncached) + `recurring_summary` tool

**Files:**
- Create: `financial_mcp/analytics/recurring.py`
- Modify: `financial_mcp/data_service.py` (`recurring()` uncached pass-through)
- Modify: `financial_mcp/mcp_server.py` (`recurring_summary` tool, import `recurring` analytics)
- Test: `tests/test_recurring_analytics.py` (new), `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `RecurringItem` (Task 2); `svc.recurring(start, end)` returns `list[RecurringItem]`.
- Produces: `recurring.recurring_summary(items: list[RecurringItem], start: datetime.date, end: datetime.date) -> dict` with keys `window`, `items` (list, date-ascending), `total_expected` (Decimal), `methodology` (str); `DataService.recurring(start, end) -> list[RecurringItem]`; MCP tool `recurring_summary(start: str, end: str) -> dict`.

- [ ] **Step 1: Write the failing analytics test**

Create `tests/test_recurring_analytics.py`:
```python
import datetime
from decimal import Decimal

from financial_mcp.analytics import recurring as recurring_analytics
from financial_mcp.monarch.models import RecurringItem


def _r(day, merchant, amount):
    return RecurringItem(
        date=datetime.date(2026, 6, day), merchant=merchant, amount=Decimal(amount),
        frequency="monthly", account="Card", category="Subs", is_past=day < 15,
    )


def test_recurring_summary_sorts_by_date_and_sums():
    out = recurring_analytics.recurring_summary(
        [_r(20, "Spotify", "-9.99"), _r(3, "Netflix", "-15.99")],
        datetime.date(2026, 6, 1), datetime.date(2026, 6, 30),
    )
    assert [i["merchant"] for i in out["items"]] == ["Netflix", "Spotify"]  # date asc
    assert out["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    assert out["total_expected"] == Decimal("-25.98")
    assert out["items"][0]["is_past"] is True
    assert out["methodology"].startswith("Amounts and frequencies are Monarch's own")


def test_recurring_summary_empty_window():
    out = recurring_analytics.recurring_summary(
        [], datetime.date(2026, 6, 1), datetime.date(2026, 6, 30)
    )
    assert out["items"] == []
    assert out["total_expected"] == Decimal("0")
    assert "methodology" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_recurring_analytics.py -v`
Expected: FAIL with `ModuleNotFoundError` for `financial_mcp.analytics.recurring`.

- [ ] **Step 3: Write the analytics module**

Create `financial_mcp/analytics/recurring.py`:
```python
from __future__ import annotations

import datetime
from decimal import Decimal

from financial_mcp.monarch.models import RecurringItem

METHODOLOGY = (
    "Amounts and frequencies are Monarch's own recurring-stream data. "
    "total_expected sums the amounts of all recurring items in the window "
    "(Monarch's own per-instance amounts); it is a sum, not a normalized monthly estimate."
)


def recurring_summary(
    items: list[RecurringItem], start: datetime.date, end: datetime.date
) -> dict:
    rows = [
        {
            "date": it.date.isoformat(),
            "merchant": it.merchant,
            "amount": it.amount,
            "frequency": it.frequency,
            "account": it.account,
            "category": it.category,
            "is_past": it.is_past,
        }
        for it in items
    ]
    rows.sort(key=lambda r: r["date"])
    total = sum((it.amount for it in items), Decimal("0"))
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "items": rows,
        "total_expected": total,
        "methodology": METHODOLOGY,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_recurring_analytics.py -v`
Expected: PASS.

- [ ] **Step 5: Wire DataService (uncached)**

In `financial_mcp/data_service.py` add `RecurringItem` to the models import and add after `budgets()`:
```python
    def recurring(self, start: datetime.date, end: datetime.date) -> list[RecurringItem]:
        # Never cached — window-parameterized, mirrors cashflow/budgets.
        return self._adapter.get_recurring(start, end)
```
(No `refresh()` change — recurring is uncached.)

- [ ] **Step 6: Add the tool — write the failing test**

Add to `tests/test_mcp_tools.py`:
```python
def test_recurring_summary_tool_envelope(tmp_path):
    from financial_mcp.monarch.models import RecurringItem

    class _RAdapter(FakeAdapter):
        def get_recurring(self, start, end):
            return [
                RecurringItem(date=datetime.date(2026, 6, 20), merchant="Spotify",
                              amount=Decimal("-9.99"), frequency="monthly",
                              account="Card", category="Subs", is_past=False),
                RecurringItem(date=datetime.date(2026, 6, 3), merchant="Netflix",
                              amount=Decimal("-15.99"), frequency="monthly",
                              account="Card", category="Subs", is_past=True),
            ]

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(_RAdapter(), cache)
    server = build_server(svc)
    data = _tool(server, "recurring_summary")("2026-06-01", "2026-06-30")
    assert data["count"] == 2
    body = data["data"]
    assert [i["merchant"] for i in body["items"]] == ["Netflix", "Spotify"]  # date asc
    assert body["total_expected"] == "-25.98"        # Decimal -> string
    assert isinstance(body["items"][0]["amount"], str)
    assert "methodology" in body


def test_recurring_summary_tool_rejects_bad_dates(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(FakeAdapter(), cache)
    server = build_server(svc)
    assert _tool(server, "recurring_summary")("nope", "2026-06-30") == {"error": "start must be YYYY-MM-DD"}
    assert _tool(server, "recurring_summary")("2026-06-30", "2026-06-01")["error"] == "start must not be after end"
```

- [ ] **Step 7: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py::test_recurring_summary_tool_envelope tests/test_mcp_tools.py::test_recurring_summary_tool_rejects_bad_dates -v`
Expected: FAIL (tool not registered).

- [ ] **Step 8: Implement the tool**

In `financial_mcp/mcp_server.py`: add `recurring` to the analytics import (`from financial_mcp.analytics import balances, budgets, holdings, networth, recurring, savings, spending`). Register after `holdings_summary`:
```python
    @server.tool()
    def recurring_summary(start: str, end: str) -> dict:
        """Upcoming/past recurring (subscription) items in a window (YYYY-MM-DD, inclusive).

        Returns an envelope whose data has `window`, `items` (one per recurring
        instance: date, merchant, amount, frequency, account, category, is_past),
        a `total_expected` (plain sum of the window's item amounts — Monarch's own
        signed amounts, NOT a normalized monthly figure), and a `methodology`
        string. Amounts and frequencies are Monarch's own; nothing is normalized
        or invented. Items are date-ascending (what is hitting next). Fetched live
        each call. Use this for "what subscriptions/recurring charges are coming
        up" and "how much of my month is already committed" questions.
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
            data = recurring.recurring_summary(svc.recurring(s, e), s, e)
            # Uncached live pull; borrow the `transactions` cache type's freshness
            # for the envelope (its natural cached sibling), matching how
            # spending_summary/budget_status report freshness for uncached tools.
            return _envelope("transactions", _jsonify([data])[0], count=len(data["items"]))
        return _guard(_run)
```

- [ ] **Step 9: Run the tool tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -q && .venv/bin/python -m pytest -q`
Expected: PASS; whole suite green.

- [ ] **Step 10: Commit**

```bash
git add financial_mcp/analytics/recurring.py financial_mcp/data_service.py financial_mcp/mcp_server.py tests/test_recurring_analytics.py tests/test_mcp_tools.py
git commit -m "feat: recurring_summary analytics + uncached DataService + MCP tool"
```

---

### Task 7: Live verification (INTERACTIVE) + mark spec shipped

**Files:**
- Modify: `docs/specs/2026-08-11-monarch-mcp-tier1-holdings-recurring-design.md` (Status → Shipped; §4 live results)

This runs with the user in Claude Desktop against the real org. It is also the Task-1 field/enum probe.

- [ ] **Step 1:** In a fresh Claude Desktop chat: `refresh_cache("holdings")`, then run `holdings_summary`. Confirm: total `value` matches Monarch's Investments UI within rounding; a couple of positions' `gain_loss` = value − cost_basis; basis-less/manual holdings show `gain_loss: null` and are excluded from `totals.cost_basis`/`gain_loss` but present in `totals.value`; positions are largest-first; `holdingsCount > 0` picked up all investment accounts (nothing missing, nothing non-investment included).
- [ ] **Step 2:** Run `recurring_summary` for a month (e.g. 2026-06-01 → 2026-06-30). Confirm: the query is accepted with `filters: null` (no HTTP 400); items list matches Monarch's Recurring page (merchant, amount, frequency, account); `total_expected` equals the sum of listed amounts; items are date-ascending; `is_past` matches which have already occurred.
- [ ] **Step 3:** If either live call errors, diagnose per the GET_BUDGETS 400 lesson (a parse-location 400 = Monarch rejecting our query document). Fix in `queries.py`/`community_adapter.py`, add a regression test, re-run the suite, re-verify.
- [ ] **Step 4:** Flip the spec Status to `✅ Shipped + live-proven (2026-08-11)` and record the live numbers in a new §4 results block (mirroring the budget-split spec's §4).
- [ ] **Step 5: Commit**
```bash
git add docs/specs/2026-08-11-monarch-mcp-tier1-holdings-recurring-design.md
git commit -m "docs: mark holdings + recurring spec Shipped + live-proven"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 Holdings query/model/adapter/analytics → Tasks 2, 3, 4, 5. ✓
- §3.2 Recurring query/model/adapter/analytics → Tasks 2, 3, 4, 6. ✓
- §3.3 tools + docstrings + `_jsonify` → Tasks 5, 6. ✓
- §4 caching (holdings 1h own type; recurring uncached; no SCHEMA bump), error handling (shared date strings; `_guard`), testing (adapter null-safety, analytics honesty calls, tool envelope, live verify) → Tasks 5, 6, 7. ✓
- §5 boundaries (basis-less handling; total_expected is a sum; snapshot-not-history) → encoded in analytics + docstrings. ✓
- Fan-out selects `holdingsCount > 0` → Task 4. ✓

**2. Placeholder scan:** No TBD/TODO. Task 1 is explicitly a no-op collapsed into Task 7 (the interactive live probe), not a placeholder. Every code step carries real code.

**3. Type consistency:** `Holding`/`RecurringItem` field names identical across models (Task 2), adapter mapping (Task 4), analytics (Tasks 5/6), and tool tests. `svc.holdings()` / `svc.recurring(s, e)` names match between DataService (Tasks 5/6) and tools (Tasks 5/6). Methodology strings byte-match the Global Constraints and the analytics modules. `_envelope("holdings", …)` uses the cache type registered via the existing `CacheTTLs.holdings` field (Task 5); `recurring_summary` borrows `"transactions"` (documented). No dangling references.
