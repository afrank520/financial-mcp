# Plan 3.0: Date-Bounded Spending Summary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a date-bounded spending summary that returns a server-computed, penny-exact total/count/charge-refund breakdown for a date window, so date-range questions never require Claude to hand-sum transaction rows.

**Architecture:** One pure analytics function (`date_range_summary`) in `financial_mcp/analytics/spending.py`, wrapped by one MCP tool (`spending_summary`) in `financial_mcp/mcp_server.py`. The tool parses/validates ISO dates, delegates the math to the analytics function, and wraps the result in the existing freshness envelope. No new dependencies, no cache/schema changes; reads the same cached transactions every other tool uses.

**Tech Stack:** Python 3.14, Pydantic models, `Decimal` money, FastMCP tools, pytest. Existing helpers reused: `spending.search()`, `mcp_server._guard`, `_envelope`, `_jsonify`.

**Source spec:** `docs/specs/2026-07-23-monarch-mcp-plan-3.0-date-bounded-summary-design.md`

## Global Constraints

- **Money is `Decimal`** everywhere in computation; serialized to **string** for JSON transport (never float). — verbatim from project convention.
- **Sign convention:** negative amount = charge/spend, positive amount = refund/credit (consistent with existing `monthly_spending`).
- **`total` is presented as a POSITIVE net-spend figure** (`gross_charges − refunds`) to match Monarch's "Total spending" display exactly. This intentionally differs from `category_summary`/`merchant_summary`, whose `total` is a signed raw sum — this tool exists to mirror the Monarch spending screen.
- **Every data tool returns the freshness envelope** `{as_of, age_hours, stale, count, data}` via `_envelope`, and is wrapped in `_guard` so auth errors become the friendly re-login dict. Error dicts (`{"error": ...}`) have keys disjoint from the envelope — never both.
- **Date window is inclusive on both ends:** `start <= t.date <= end`. Transactions store `date` only (no timestamp), so no timezone handling.
- **No new dependencies. No cache type. No adapter changes.**
- **Must stay `ruff` + `black` clean; full pytest suite green** before finishing.
- **Known boundary (do not implement here):** no transfer/hidden-transaction exclusion. Merchant/category-filtered windows match Monarch exactly; unfiltered totals may include transfers.

---

### Task 1: `date_range_summary` analytics function

**Files:**
- Modify: `financial_mcp/analytics/spending.py` (add one function)
- Test: `tests/test_spending.py` (add fixture + tests; extend existing import)

**Interfaces:**
- Consumes: `search()` from the same module (existing), `Transaction` from `financial_mcp.monarch.models` (existing).
- Produces:
  ```python
  def date_range_summary(
      txns: list[Transaction],
      start: datetime.date,
      end: datetime.date,
      *,
      merchant: str | None = None,
      category: str | None = None,
  ) -> dict
  ```
  Returns a dict with exactly these keys (all money values are `Decimal`):
  `total` (Decimal, positive net spend), `gross_charges` (Decimal, positive), `refunds` (Decimal, positive), `count` (int), `charge_count` (int), `refund_count` (int).

- [ ] **Step 1: Write the failing tests**

Add to the import at the top of `tests/test_spending.py` (it currently imports `category_summary, merchant_summary, monthly_spending, search`):

```python
from financial_mcp.analytics.spending import (
    category_summary,
    date_range_summary,
    merchant_summary,
    monthly_spending,
    search,
)
```

Append to `tests/test_spending.py` (the existing `_txn(merchant, category, amount, month=1, day=1, year=2026)` helper is reused via its `month`/`day` params):

```python
# --- Amazon Jul 19-22 2026 accuracy fixture (ground truth: Monarch UI) ---
# 21 charges + 3 refunds = 24 txns; gross 784.65, refunds 58.86, net 725.79.
AMAZON_WINDOW = [
    # July 19 — 3 charges
    _txn("Amazon", "Shopping", "-8.22", month=7, day=19),
    _txn("Amazon", "Shopping", "-14.11", month=7, day=19),
    _txn("Amazon", "Shopping", "-8.58", month=7, day=19),
    # July 20 — 5 charges
    _txn("Amazon", "Shopping", "-190.58", month=7, day=20),
    _txn("Amazon", "Shopping", "-24.99", month=7, day=20),
    _txn("Amazon", "Shopping", "-20.76", month=7, day=20),
    _txn("Amazon", "Shopping", "-132.58", month=7, day=20),
    _txn("Amazon", "Shopping", "-51.57", month=7, day=20),
    # July 21 — 10 charges
    _txn("Amazon", "Shopping", "-23.55", month=7, day=21),
    _txn("Amazon", "Shopping", "-21.85", month=7, day=21),
    _txn("Amazon", "Shopping", "-17.02", month=7, day=21),
    _txn("Amazon", "Shopping", "-29.60", month=7, day=21),
    _txn("Amazon", "Shopping", "-26.18", month=7, day=21),
    _txn("Amazon", "Shopping", "-20.94", month=7, day=21),
    _txn("Amazon", "Shopping", "-20.93", month=7, day=21),
    _txn("Amazon", "Shopping", "-19.87", month=7, day=21),
    _txn("Amazon", "Shopping", "-18.84", month=7, day=21),
    _txn("Amazon", "Shopping", "-23.56", month=7, day=21),
    # July 21 — 3 refunds
    _txn("Amazon", "Shopping", "14.11", month=7, day=21),
    _txn("Amazon", "Shopping", "14.90", month=7, day=21),
    _txn("Amazon", "Shopping", "29.85", month=7, day=21),
    # July 22 — 3 charges
    _txn("Amazon", "Shopping", "-20.28", month=7, day=22),
    _txn("Amazon", "Shopping", "-14.65", month=7, day=22),
    _txn("Amazon", "Shopping", "-75.99", month=7, day=22),
]

# Noise that must be excluded by the date window or the merchant filter.
NOISE = [
    _txn("Costco", "Groceries", "-50.00", month=7, day=20),   # in window, wrong merchant
    _txn("Amazon", "Shopping", "-999.00", month=7, day=18),   # right merchant, before window
    _txn("Amazon", "Shopping", "-999.00", month=7, day=23),   # right merchant, after window
]

JUL19 = datetime.date(2026, 7, 19)
JUL22 = datetime.date(2026, 7, 22)


def test_date_range_summary_matches_monarch_to_the_penny():
    # Headline Goal #4 accuracy test — must equal the Monarch UI numbers.
    s = date_range_summary(
        AMAZON_WINDOW + NOISE, JUL19, JUL22, merchant="amazon"
    )
    assert s["total"] == Decimal("725.79")      # matches Monarch "Total spending"
    assert s["count"] == 24                       # matches Monarch txn count
    assert s["gross_charges"] == Decimal("784.65")
    assert s["refunds"] == Decimal("58.86")
    assert s["charge_count"] == 21
    assert s["refund_count"] == 3


def test_date_range_summary_inclusive_boundaries():
    # A txn exactly on start and one exactly on end are included; neighbors excluded.
    txns = [
        _txn("Amazon", "Shopping", "-10.00", month=7, day=18),  # day before start
        _txn("Amazon", "Shopping", "-1.00", month=7, day=19),   # on start
        _txn("Amazon", "Shopping", "-2.00", month=7, day=22),   # on end
        _txn("Amazon", "Shopping", "-10.00", month=7, day=23),  # day after end
    ]
    s = date_range_summary(txns, JUL19, JUL22)
    assert s["count"] == 2
    assert s["total"] == Decimal("3.00")


def test_date_range_summary_category_filter():
    txns = [
        _txn("Store A", "Groceries", "-40.00", month=7, day=20),
        _txn("Store B", "Shopping", "-60.00", month=7, day=20),
    ]
    s = date_range_summary(txns, JUL19, JUL22, category="grocer")
    assert s["count"] == 1
    assert s["total"] == Decimal("40.00")


def test_date_range_summary_empty_window_is_zeros():
    s = date_range_summary(AMAZON_WINDOW, datetime.date(2026, 1, 1), datetime.date(2026, 1, 2))
    assert s == {
        "total": Decimal("0"),
        "gross_charges": Decimal("0"),
        "refunds": Decimal("0"),
        "count": 0,
        "charge_count": 0,
        "refund_count": 0,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_spending.py -k date_range -v`
Expected: FAIL — `ImportError: cannot import name 'date_range_summary'` (collection error before the four tests run).

- [ ] **Step 3: Write the implementation**

Add to `financial_mcp/analytics/spending.py`. It needs `datetime` — add `import datetime` at the top (the module currently imports only `defaultdict` and `Decimal`). Place the function after `monthly_spending` and before `search` is fine, or at the end; put it at the end of the file:

```python
def date_range_summary(
    txns: list[Transaction],
    start: datetime.date,
    end: datetime.date,
    *,
    merchant: str | None = None,
    category: str | None = None,
) -> dict:
    """Server-computed spending summary over an inclusive [start, end] date window.

    Reuses search() for merchant/category filtering, then computes exact
    Decimal totals so callers never sum rows by hand.

    total is a POSITIVE net-spend figure (gross_charges - refunds), matching
    Monarch's "Total spending" display. Sign convention: negative amount =
    charge, positive = refund.
    """
    in_window = [t for t in txns if start <= t.date <= end]
    filtered = search(in_window, merchant=merchant, category=category)
    charges = [t for t in filtered if t.amount < 0]
    refunds = [t for t in filtered if t.amount > 0]
    gross_charges = -sum((t.amount for t in charges), Decimal("0"))
    refund_total = sum((t.amount for t in refunds), Decimal("0"))
    return {
        "total": gross_charges - refund_total,
        "gross_charges": gross_charges,
        "refunds": refund_total,
        "count": len(filtered),
        "charge_count": len(charges),
        "refund_count": len(refunds),
    }
```

Add the import at the top of the file:

```python
from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from financial_mcp.monarch.models import Transaction
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_spending.py -v`
Expected: PASS — the four new `date_range_*` tests plus all pre-existing spending tests green.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/analytics/spending.py tests/test_spending.py
git commit -m "feat: date_range_summary analytics — penny-exact date-bounded totals

Server-computed net/gross/refund breakdown over an inclusive date window,
reusing search() for merchant/category filters. Headline accuracy test:
Amazon Jul 19-22 2026 = \$725.79 net / 24 txns, matching the Monarch UI.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `spending_summary` MCP tool + `_envelope` generalization

**Files:**
- Modify: `financial_mcp/mcp_server.py` (generalize `_envelope`; add `spending_summary` tool; add `import datetime`)
- Test: `tests/test_mcp_tools.py` (add tool tests)

**Interfaces:**
- Consumes: `date_range_summary` from Task 1 (signature above); existing `_guard`, `_envelope`, `_jsonify`, `svc.transactions()`, `svc.freshness()`.
- Produces MCP tool:
  ```python
  def spending_summary(
      start_date: str,          # "YYYY-MM-DD"
      end_date: str,            # "YYYY-MM-DD"
      merchant: str | None = None,
      category: str | None = None,
  ) -> dict
  ```
  On success returns the freshness envelope with `data` = the jsonify'd summary dict and top-level `count` = the summary's transaction count. On bad input returns `{"error": "..."}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_tools.py`. It already defines `FakeAdapter`, `_service`, `_tool`, and imports `SqliteCache`, `CacheTTLs`, `DataService`, `build_server`, `Transaction`, `Decimal`, `datetime`. Add a small in-window adapter and the tests:

```python
class WindowAdapter(FakeAdapter):
    """Two Amazon txns inside Jul 2026 — one charge, one refund."""

    def get_transactions(self):
        return [
            Transaction(
                id=Transaction.synth_id(
                    datetime.date(2026, 7, 20), "Amazon", Decimal("-100.00"), "AMZN"
                ),
                date=datetime.date(2026, 7, 20),
                merchant="Amazon",
                category="Shopping",
                account="Checking",
                original_statement="AMZN",
                notes="",
                amount=Decimal("-100.00"),
                tags=[],
                owner="A",
            ),
            Transaction(
                id=Transaction.synth_id(
                    datetime.date(2026, 7, 21), "Amazon", Decimal("30.00"), "AMZN"
                ),
                date=datetime.date(2026, 7, 21),
                merchant="Amazon",
                category="Shopping",
                account="Checking",
                original_statement="AMZN",
                notes="",
                amount=Decimal("30.00"),
                tags=[],
                owner="A",
            ),
        ]


def _window_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(WindowAdapter(), cache)


def test_spending_summary_envelope_and_math(tmp_path):
    server = build_server(_window_service(tmp_path))
    result = _tool(server, "spending_summary")(
        start_date="2026-07-19", end_date="2026-07-22", merchant="amazon"
    )
    # Freshness envelope keys present.
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    assert result["count"] == 2
    data = result["data"]
    # Money serialized as strings, computed exactly.
    assert data["total"] == "70.00"          # 100.00 charge - 30.00 refund
    assert data["gross_charges"] == "100.00"
    assert data["refunds"] == "30.00"
    assert data["charge_count"] == 1
    assert data["refund_count"] == 1


def test_spending_summary_bad_start_date(tmp_path):
    server = build_server(_window_service(tmp_path))
    result = _tool(server, "spending_summary")(start_date="July 19", end_date="2026-07-22")
    assert result == {"error": "start_date must be YYYY-MM-DD"}


def test_spending_summary_bad_end_date(tmp_path):
    server = build_server(_window_service(tmp_path))
    result = _tool(server, "spending_summary")(start_date="2026-07-19", end_date="nope")
    assert result == {"error": "end_date must be YYYY-MM-DD"}


def test_spending_summary_start_after_end(tmp_path):
    server = build_server(_window_service(tmp_path))
    result = _tool(server, "spending_summary")(start_date="2026-07-25", end_date="2026-07-22")
    assert result == {"error": "start_date must not be after end_date"}


def test_spending_summary_returns_error_on_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(FakeAdapter):
        def get_transactions(self):
            raise SessionExpiredError("expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "spending_summary")(
        start_date="2026-07-19", end_date="2026-07-22"
    )
    assert "financial-mcp-login" in result["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k spending_summary -v`
Expected: FAIL — `KeyError: 'spending_summary'` from `_tool()` (the tool is not registered yet).

- [ ] **Step 3: Generalize `_envelope`**

In `financial_mcp/mcp_server.py`, replace the current `_envelope` definition:

```python
    def _envelope(data_type: str, rows: list[dict]) -> dict:
        """Wrap tool rows with freshness metadata so Claude can reason about age.

        Read freshness AFTER the rows are fetched (the fetch populates the
        cache), so as_of reflects the data being returned.
        """
        f = svc.freshness(data_type)
        age_s = f["age_seconds"]
        return {
            "as_of": f["as_of"],
            "age_hours": None if age_s is None else round(age_s / 3600, 1),
            "stale": not f["fresh"],
            "count": len(rows),
            "data": rows,
        }
```

with this backward-compatible version (list callers keep `count = len(data)`; the summary tool passes an explicit `count`):

```python
    def _envelope(data_type: str, data, count: int | None = None) -> dict:
        """Wrap tool data with freshness metadata so Claude can reason about age.

        Read freshness AFTER the data is fetched (the fetch populates the
        cache), so as_of reflects the data being returned. `data` is normally a
        list of rows (count defaults to len); pass an explicit count when data
        is a single aggregate object.
        """
        f = svc.freshness(data_type)
        age_s = f["age_seconds"]
        return {
            "as_of": f["as_of"],
            "age_hours": None if age_s is None else round(age_s / 3600, 1),
            "stale": not f["fresh"],
            "count": len(data) if count is None else count,
            "data": data,
        }
```

- [ ] **Step 4: Add the `spending_summary` tool and the `datetime` import**

Add `import datetime` to the top of `financial_mcp/mcp_server.py` (it currently imports only `from decimal import Decimal` under `from __future__ import annotations`):

```python
from __future__ import annotations

import datetime
from decimal import Decimal
```

Add the tool inside `build_server`, immediately after the `monthly_spending` tool and before `cache_status`:

```python
    @server.tool()
    def spending_summary(
        start_date: str,
        end_date: str,
        merchant: str | None = None,
        category: str | None = None,
    ) -> dict:
        """Exact server-computed spending total for a date window (YYYY-MM-DD).

        Use this for ANY "how much did I spend between/from date X to Y"
        question instead of summing search_transactions rows yourself. Dates are
        inclusive on both ends. Optionally filter by merchant/category substring.

        Returns an envelope whose data has: total (net spend, positive, matches
        Monarch's "Total spending"), gross_charges (excludes refunds), refunds,
        count, charge_count, refund_count. Note: an UNFILTERED window may include
        internal transfers; a merchant/category filter matches Monarch exactly.
        """
        try:
            start = datetime.date.fromisoformat(start_date)
        except ValueError:
            return {"error": "start_date must be YYYY-MM-DD"}
        try:
            end = datetime.date.fromisoformat(end_date)
        except ValueError:
            return {"error": "end_date must be YYYY-MM-DD"}
        if start > end:
            return {"error": "start_date must not be after end_date"}

        def _run():
            summary = spending.date_range_summary(
                svc.transactions(), start, end, merchant=merchant, category=category
            )
            return _envelope("transactions", _jsonify([summary])[0], count=summary["count"])

        return _guard(_run)
```

Note: `_jsonify([summary])[0]` reuses the existing module-level `_jsonify` (which takes a `list[dict]`) to convert the summary's `Decimal` fields to strings while leaving the int counts intact.

- [ ] **Step 5: Run the new tool tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k spending_summary -v`
Expected: PASS — all five `spending_summary` tests green.

- [ ] **Step 6: Run the FULL suite + lint to catch regressions**

The `_envelope` change touches every data tool, so run everything:

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all tests green, including the pre-existing envelope tests (`test_get_accounts_envelope_carries_freshness`, `test_get_transactions_limit`) which assert `count`.

Run: `.venv/bin/ruff check . && .venv/bin/black --check .`
Expected: clean. If `black` reports files would be reformatted, run `.venv/bin/black .` and re-run the suite.

- [ ] **Step 7: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: spending_summary MCP tool — date-bounded, freshness-wrapped

Parses/validates ISO dates (friendly error dict on bad input or start>end),
delegates to date_range_summary, and wraps the aggregate in the freshness
envelope. Generalizes _envelope to accept an explicit count for single-object
payloads (list callers unchanged). Steers Claude here for date-range spend
questions so it stops hand-summing rows.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Post-Implementation (manual, after merge)

Not tasks — reminders for the human:
- **Cmd+Q + reopen Claude Desktop**, then a **fresh chat**, to load the new tool.
- **Live re-verify:** ask "How much did I spend at Amazon from July 19 through July 22, 2026?" — Claude should call `spending_summary` and report the total with no hand-arithmetic. Compare against the Monarch UI ($725.79 / 24) once more on real data.

## Self-Review

**1. Spec coverage:**
- §2.1 analytics function (filters, inclusive window, six fields, sign convention, empty→zeros) → Task 1. ✓
- §2.2 MCP tool (ISO parse, error dicts for bad date + start>end, envelope wrap, `_guard`, steering docstring, unfiltered-transfer note) → Task 2. ✓
- §2.3 testing (analytics unit tests, boundary, filters, empty; tool envelope/validation/auth; headline Amazon accuracy fixture) → Task 1 (accuracy + analytics) + Task 2 (tool). ✓
- §2.4 out-of-scope (no amount filters, no relative windows, no cache type) → honored; none added. ✓
- §4 known boundary → surfaced in the tool docstring and Global Constraints. ✓
- §3 North Star map → vision only, nothing to implement. ✓ (correctly no task)

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows complete code and exact commands. ✓

**3. Type consistency:** `date_range_summary` signature and its six return keys are identical in the Task 1 interface block, the test asserts, and the implementation. Task 2 consumes exactly that signature and those keys (`summary["count"]`). `_envelope`'s new `count` param is used consistently. Field names (`total`, `gross_charges`, `refunds`, `count`, `charge_count`, `refund_count`) match across spec, tests, and code. ✓
