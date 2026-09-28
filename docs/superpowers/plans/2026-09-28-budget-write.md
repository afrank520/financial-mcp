# Budget Write (`set_budget`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gated, reversible `set_budget` MCP tool that changes the planned monthly budget amount for one already-budgeted category in one month, behind the existing write-gate → dry-run → confirm → undo safety flow.

**Architecture:** A new create-or-update GraphQL mutation (`Common_UpdateBudgetItem`) is wrapped by a new `CommunityAdapter.update_budget_item` method and a thin `DataService` passthrough. Three pure helpers in `writes.py` (amount normalization, month parsing, confirm-token) support a new `set_budget` tool in `mcp_server.py`. The tool reads the current amount via the existing `svc.budgets()` live call, refuses unbudgeted categories, records a `"set_budget"` undo op, and is reversed by a new case in the `undo_last` dispatch.

**Tech Stack:** Python 3.12+, FastMCP (`mcp<2`), aiohttp GraphQL client, sqlite undo journal, pytest. Money handled as `decimal.Decimal`.

**Spec:** `docs/specs/2026-09-28-budget-write-design.md`

## Global Constraints

- **Never print to stdout** in `financial_mcp/` server code — it corrupts the MCP stdio channel. No `print()`; use the module `logger` if logging is needed.
- **Fail-closed everywhere:** unknown/ambiguous category, unbudgeted target, negative amount, or a confirm-token mismatch → return a dict with an `error` (or refusal) and write nothing.
- **Write-gate:** the tool must be inert unless `write_mode` ∈ {`safe`, `full`}. First line of the tool body: `disabled = _writes_enabled(); if disabled: return {"error": disabled}`.
- **Money as `Decimal`:** normalize amounts to two decimal places; compare Monarch's echoed amount to the requested amount with a one-cent tolerance (`abs(saved - amt) <= Decimal("0.01")`).
- **Captured mutation shape (verbatim, DevTools-confirmed 2026-09-28):** op name `Common_UpdateBudgetItem`, input `UpdateOrCreateBudgetItemMutationInput` with fields `categoryId` (str), `startDate` (str `YYYY-MM-01`), `timeframe` (`"month"`), `amount` (number), `applyToFuture` (bool). It returns `updateOrCreateBudgetItem.budgetItem.plannedCashFlowAmount` → verify-by-response.
- **Scope default:** `apply_to_future` defaults to `False` (this month only); the dry-run always returns a `scope_prompt` so the this-month-vs-ongoing choice is conscious. The confirm token binds `apply_to_future`, so switching scope yields a different token.
- **Unbudgeted = absent:** "unbudgeted" is defined as the category id not appearing in the month's `svc.budgets()` rows. (`$0` is a valid existing budget, not "unbudgeted".) This assumption is verified in Task 3's live prove-first step.
- Match existing patterns exactly: mirror `create_category_rule` (tool shape), `update_transaction_rule` (undo-with-JSON-old_value), and `_update_rule_input`/`compute_rule_update_token` (helpers).
- Run the full suite with `.venv/bin/python -m pytest` after each task; it must stay green.

---

### Task 1: Pure write helpers in `writes.py`

**Files:**
- Modify: `financial_mcp/writes.py` (add three functions near the other `compute_*_token` helpers, ~line 232)
- Test: `tests/test_writes.py` (append)

**Interfaces:**
- Consumes: `WriteError` (already defined at `writes.py:12`), `Decimal` and `datetime` (already imported at top of `writes.py`).
- Produces:
  - `normalize_budget_amount(amount) -> Decimal` — quantized to cents; raises `WriteError` on negative.
  - `parse_budget_month(value: str) -> datetime.date` — first-of-month date; accepts `YYYY-MM` or `YYYY-MM-DD`; raises `WriteError` on bad format.
  - `compute_budget_token(category_id: str, start_date: str, amount, apply_to_future: bool) -> str` — 16-hex content hash binding all four.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_writes.py`:

```python
import datetime
from decimal import Decimal

import pytest

from financial_mcp import writes
from financial_mcp.writes import WriteError


def test_normalize_budget_amount_quantizes_to_cents():
    assert writes.normalize_budget_amount(250) == Decimal("250.00")
    assert writes.normalize_budget_amount(19.9) == Decimal("19.90")
    assert writes.normalize_budget_amount("12.005") == Decimal("12.00")  # rounds to cents


def test_normalize_budget_amount_rejects_negative():
    with pytest.raises(WriteError) as exc:
        writes.normalize_budget_amount(-5)
    assert "non-negative" in str(exc.value)


def test_parse_budget_month_accepts_year_month():
    assert writes.parse_budget_month("2026-09") == datetime.date(2026, 9, 1)


def test_parse_budget_month_accepts_full_date_and_normalizes_to_first():
    assert writes.parse_budget_month("2026-09-17") == datetime.date(2026, 9, 1)


def test_parse_budget_month_rejects_garbage():
    with pytest.raises(WriteError):
        writes.parse_budget_month("September")


def test_compute_budget_token_is_stable_and_scope_sensitive():
    a = writes.compute_budget_token("c1", "2026-09-01", Decimal("250.00"), False)
    b = writes.compute_budget_token("c1", "2026-09-01", Decimal("250.00"), False)
    assert a == b and len(a) == 16
    # flipping scope changes the token (anti-replay across scopes)
    assert writes.compute_budget_token("c1", "2026-09-01", Decimal("250.00"), True) != a
    # different amount changes the token
    assert writes.compute_budget_token("c1", "2026-09-01", Decimal("300.00"), False) != a
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_writes.py -k budget -v`
Expected: FAIL with `AttributeError: module 'financial_mcp.writes' has no attribute 'normalize_budget_amount'`.

- [ ] **Step 3: Implement the helpers**

In `financial_mcp/writes.py`, after `compute_category_rule_token` (around line 232), add:

```python
def normalize_budget_amount(amount) -> Decimal:
    """Coerce a caller-supplied amount to a cents-quantized Decimal. Fail-closed
    on negatives (v1 budgets expense categories, which are non-negative)."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    if value < 0:
        raise WriteError("amount must be non-negative")
    return value


def parse_budget_month(value: str) -> datetime.date:
    """Parse 'YYYY-MM' or 'YYYY-MM-DD' to the FIRST of that month. Fail-closed."""
    text = (value or "").strip()
    for fmt in ("%Y-%m", "%Y-%m-%d"):
        try:
            d = datetime.datetime.strptime(text, fmt).date()
            return d.replace(day=1)
        except ValueError:
            continue
    raise WriteError(f"month must be 'YYYY-MM' or 'YYYY-MM-DD', got {value!r}")


def compute_budget_token(
    category_id: str, start_date: str, amount, apply_to_future: bool
) -> str:
    """Content hash of the exact budget change (category+month+amount+scope), in a
    namespace distinct from the other write tokens so it can't be cross-replayed."""
    key = f"{category_id}#budget#{start_date}|{amount}|{int(bool(apply_to_future))}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_writes.py -k budget -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/writes.py tests/test_writes.py
git commit -m "feat(budget-write): add amount/month/token helpers for set_budget

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Adapter + DataService `update_budget_item`

**Files:**
- Modify: `financial_mcp/monarch/queries.py` (add `UPDATE_BUDGET_ITEM` constant at end)
- Modify: `financial_mcp/monarch/community_adapter.py` (import the query; add `update_budget_item` method)
- Modify: `financial_mcp/monarch/adapter.py` (add abstract method)
- Modify: `financial_mcp/monarch/csv_adapter.py` (add raising stub)
- Modify: `financial_mcp/data_service.py` (add passthrough)
- Test: `tests/test_budget_write_adapter.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `CommunityAdapter.update_budget_item(category_id: str, amount, start_date: str, apply_to_future: bool) -> Decimal | None` — returns the saved `plannedCashFlowAmount` as `Decimal`, or `None` if Monarch didn't apply it.
  - `DataService.update_budget_item(category_id: str, amount, start_date: str, apply_to_future: bool) -> Decimal | None` — passthrough.
  - `MonarchAdapter.update_budget_item(...)` abstract method (same signature).

- [ ] **Step 1: Write the failing adapter tests**

Create `tests/test_budget_write_adapter.py`:

```python
from decimal import Decimal

import pytest

from financial_mcp.monarch.community_adapter import CommunityAdapter


def _capture_adapter(response):
    """Adapter whose fake transport records the last (op, variables) and returns `response`."""
    calls = []

    async def fake_transport(op, query, variables):
        calls.append((op, variables))
        return response

    ad = CommunityAdapter({"session_id": "s", "csrftoken": "c"}, transport=fake_transport)
    return ad, calls


def test_update_budget_item_sends_captured_shape_and_returns_saved():
    ad, calls = _capture_adapter(
        {"updateOrCreateBudgetItem": {"budgetItem": {"id": "b1", "plannedCashFlowAmount": 250.0}}}
    )
    saved = ad.update_budget_item("c1", Decimal("250.00"), "2026-09-01", False)
    assert saved == Decimal("250.0")
    op, variables = calls[0]
    assert op == "Common_UpdateBudgetItem"
    assert variables["input"] == {
        "categoryId": "c1",
        "startDate": "2026-09-01",
        "timeframe": "month",
        "amount": 250.0,
        "applyToFuture": False,
    }


def test_update_budget_item_all_future_flag_passes_through():
    ad, calls = _capture_adapter(
        {"updateOrCreateBudgetItem": {"budgetItem": {"id": "b1", "plannedCashFlowAmount": 300.0}}}
    )
    ad.update_budget_item("c1", Decimal("300.00"), "2026-09-01", True)
    assert calls[0][1]["input"]["applyToFuture"] is True


def test_update_budget_item_returns_none_when_no_item():
    ad, _ = _capture_adapter({"updateOrCreateBudgetItem": {"budgetItem": None}})
    assert ad.update_budget_item("c1", Decimal("250.00"), "2026-09-01", False) is None


def test_csv_update_budget_item_raises():
    from financial_mcp.monarch.csv_adapter import CsvAdapter

    ad = CsvAdapter(transactions_path=None, balances_path=None)
    with pytest.raises(RuntimeError):
        ad.update_budget_item("c1", Decimal("250.00"), "2026-09-01", False)
```

> Note: check `CsvAdapter.__init__`'s real signature in `financial_mcp/monarch/csv_adapter.py` and match it in the last test (the constructor args may differ; use whatever the existing CSV tests use, e.g. see `tests/test_csv_adapter.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_budget_write_adapter.py -v`
Expected: FAIL — `AttributeError: 'CommunityAdapter' object has no attribute 'update_budget_item'` (and an abstract-method / import error).

- [ ] **Step 3a: Add the query constant**

Append to `financial_mcp/monarch/queries.py`:

```python
UPDATE_BUDGET_ITEM = """
mutation Common_UpdateBudgetItem($input: UpdateOrCreateBudgetItemMutationInput!) {
  updateOrCreateBudgetItem(input: $input) {
    budgetItem {
      id
      plannedCashFlowAmount
      __typename
    }
    __typename
  }
}
"""
```

- [ ] **Step 3b: Add the abstract method**

In `financial_mcp/monarch/adapter.py`, after the `update_transaction_rule` abstract method (before `source_name`), add:

```python
    @abc.abstractmethod
    def update_budget_item(self, category_id: str, amount, start_date: str,
                           apply_to_future: bool): ...
```

- [ ] **Step 3c: Implement in `CommunityAdapter`**

In `financial_mcp/monarch/community_adapter.py`, add `UPDATE_BUDGET_ITEM` to the `from financial_mcp.monarch.queries import (...)` block (keep alphabetical order — it goes just before `UPDATE_CATEGORY`). Then add this method at the end of the class (after `update_transaction_rule`):

```python
    def update_budget_item(self, category_id, amount, start_date, apply_to_future) -> Decimal | None:
        """Set the planned monthly budget for a category (create-or-update).
        Returns the saved plannedCashFlowAmount (Decimal) on success, or None if
        Monarch didn't apply it. Verify-by-response: the mutation echoes the
        saved amount, so no re-read is needed."""
        data = self._run(self._transport(
            "Common_UpdateBudgetItem", UPDATE_BUDGET_ITEM,
            {"input": {
                "categoryId": category_id,
                "startDate": start_date,
                "timeframe": "month",
                "amount": float(amount),
                "applyToFuture": bool(apply_to_future),
            }}))
        item = ((data.get("updateOrCreateBudgetItem") or {}).get("budgetItem")) or {}
        saved = item.get("plannedCashFlowAmount")
        return None if saved is None else Decimal(str(saved))
```

- [ ] **Step 3d: Add the CSV raising stub**

In `financial_mcp/monarch/csv_adapter.py`, after `update_transaction_rule` (line ~152), add:

```python
    def update_budget_item(self, category_id: str, amount, start_date: str, apply_to_future: bool):
        raise RuntimeError("writes are not available in CSV mode")
```

- [ ] **Step 3e: Add the DataService passthrough**

In `financial_mcp/data_service.py`, after `update_transaction_rule` (line ~183), add:

```python
    def update_budget_item(self, category_id: str, amount, start_date: str,
                           apply_to_future: bool):
        # Budgets are never cached (see budgets()), so no cache to invalidate.
        return self._adapter.update_budget_item(
            category_id, amount, start_date, apply_to_future)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_budget_write_adapter.py -v`
Expected: PASS (4 tests).

Also run the whole suite to catch the new abstract method breaking any test adapter:
Run: `.venv/bin/python -m pytest -q`
Expected: PASS. If a test double subclassing `MonarchAdapter` now errors as abstract, that double is in the failing test's file — add an `update_budget_item` stub to it (returning `None` or raising `NotImplementedError`, matching that file's convention). The main `FakeAdapter` in `tests/test_mcp_tools.py` gets its stub in Task 3, so if only `test_mcp_tools.py` fails here, that is expected and Task 3 fixes it — but prefer to add the `FakeAdapter` stub now (see Task 3 Step 3a) so the suite is green at every commit.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/queries.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/adapter.py financial_mcp/monarch/csv_adapter.py financial_mcp/data_service.py tests/test_budget_write_adapter.py
git commit -m "feat(budget-write): add update_budget_item adapter + service passthrough

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The `set_budget` MCP tool

**Files:**
- Modify: `financial_mcp/mcp_server.py` (add `import calendar`; add the `set_budget` tool near the other write tools, e.g. after `create_category_rule` ends at line ~408)
- Modify: `financial_mcp/cache/undo_journal.py` (add `"set_budget"` to `REVERSIBLE_OPS`, line 11)
- Modify: `tests/test_mcp_tools.py` (add a budget-server harness + tool tests; add `FakeAdapter.update_budget_item` + `FakeAdapter.get_budgets` budget-bearing default is NOT wanted globally — use a subclass, see below)
- Test: covered in `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `writes.normalize_budget_amount`, `writes.parse_budget_month`, `writes.compute_budget_token` (Task 1); `svc.update_budget_item`, `svc.budgets` (Task 2); `writes.resolve_category`, `_writes_enabled`, `_guard`, `journal.record`, `journal.mark_succeeded` (existing).
- Produces: the `set_budget` tool. Records undo ops with `op_type="set_budget"`, `target_id=category_id`, `old_value`/`new_value` = JSON `{"amount","start_date","apply_to_future"}`, `label_old`/`label_new` = `"<name> <YYYY-MM> $<amount>"`. (The undo dispatch that consumes these is Task 4.)

- [ ] **Step 1: Write the failing tool tests**

Add to `tests/test_mcp_tools.py`. First, near the other adapters (after `LiveAdapter`, ~line 132), add a budget-bearing adapter and server helper:

```python
class _BudgetAdapter(LiveAdapter):
    """Live-source adapter with one budgeted category (c1 'Groceries', $200) and a
    recording update_budget_item. get_budgets returns c1 so set_budget sees an
    existing budget; category c2 has no budget row (the 'unbudgeted' case)."""

    def __init__(self):
        self.budget_calls = []
        self._saved = {}  # category_id -> Decimal currently saved

    def get_categories(self):
        from financial_mcp.monarch.models import Category
        return [
            Category(id="c1", name="Groceries", group="Food & Dining"),
            Category(id="c2", name="Skiing", group="Fun"),
        ]

    def get_budgets(self, start, end):
        from financial_mcp.monarch.models import BudgetItem
        current = self._saved.get("c1", Decimal("200.00"))
        return [BudgetItem(category_id="c1", budgeted=current,
                           actual=Decimal("0"), remaining=current)]

    def update_budget_item(self, category_id, amount, start_date, apply_to_future):
        self.budget_calls.append((category_id, Decimal(str(amount)), start_date, apply_to_future))
        saved = Decimal(str(amount))
        self._saved[category_id] = saved
        return saved  # echo back what was asked (happy path)


def _budget_server(tmp_path, write_mode="safe"):
    from financial_mcp.cache.undo_journal import UndoJournal
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(_BudgetAdapter(), cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode=write_mode, batch_cap=200, journal=journal)
    return svc, journal, server
```

Then the tests:

```python
def test_set_budget_disabled_when_write_mode_off(tmp_path):
    _svc, _journal, server = _budget_server(tmp_path, write_mode="off")
    out = _tool(server, "set_budget")("Groceries", 250, month="2026-09")
    assert "error" in out and "disabled" in out["error"]


def test_set_budget_unknown_category_refused(tmp_path):
    _svc, _journal, server = _budget_server(tmp_path, write_mode="safe")
    out = _tool(server, "set_budget")("Nope", 250, month="2026-09")
    assert "error" in out and "Nope" in out["error"]


def test_set_budget_unbudgeted_refused(tmp_path):
    _svc, _journal, server = _budget_server(tmp_path, write_mode="safe")
    out = _tool(server, "set_budget")("Skiing", 300, month="2026-09")
    assert "error" in out and "coming later" in out["error"]


def test_set_budget_negative_refused(tmp_path):
    _svc, _journal, server = _budget_server(tmp_path, write_mode="safe")
    out = _tool(server, "set_budget")("Groceries", -5, month="2026-09")
    assert "error" in out and "non-negative" in out["error"]


def test_set_budget_dry_run_returns_token_and_scope_prompt(tmp_path):
    svc, _journal, server = _budget_server(tmp_path, write_mode="safe")
    out = _tool(server, "set_budget")("Groceries", 250, month="2026-09")
    assert out["dry_run"] is True
    assert out["current_amount"] == 250.0 or out["current_amount"] == 200.0
    assert out["current_amount"] == 200.0  # existing budget
    assert out["new_amount"] == 250.0
    assert out["apply_to_future"] is False
    assert out["scope_prompt"] and "only" in out["scope_prompt"]
    assert len(out["confirm_token"]) == 16
    assert svc._adapter.budget_calls == []  # nothing written


def test_set_budget_scope_prompt_names_ongoing_when_all_future(tmp_path):
    _svc, _journal, server = _budget_server(tmp_path, write_mode="safe")
    out = _tool(server, "set_budget")("Groceries", 250, month="2026-09", apply_to_future=True)
    assert "FUTURE" in out["scope_prompt"]
    # flipping scope changes the token vs this-month-only
    other = _tool(server, "set_budget")("Groceries", 250, month="2026-09")
    assert out["confirm_token"] != other["confirm_token"]


def test_set_budget_wrong_token_stays_dry_run(tmp_path):
    svc, _journal, server = _budget_server(tmp_path, write_mode="safe")
    out = _tool(server, "set_budget")("Groceries", 250, month="2026-09", confirm_token="wrong")
    assert out["dry_run"] is True
    assert svc._adapter.budget_calls == []


def test_set_budget_confirm_writes_and_records_undo(tmp_path):
    svc, journal, server = _budget_server(tmp_path, write_mode="safe")
    dry = _tool(server, "set_budget")("Groceries", 250, month="2026-09")
    out = _tool(server, "set_budget")("Groceries", 250, month="2026-09",
                                      confirm_token=dry["confirm_token"])
    assert out["updated"] is True
    assert out["old_amount"] == 200.0 and out["new_amount"] == 250.0
    assert svc._adapter.budget_calls == [("c1", Decimal("250.00"), "2026-09-01", False)]
    # an undoable batch was recorded for set_budget
    batch = journal.last_undoable_batch()
    assert batch is not None
    rows = journal.undoable_rows(batch)
    assert rows[0]["op_type"] == "set_budget" and rows[0]["target_id"] == "c1"


def test_set_budget_full_mode_writes_without_token(tmp_path):
    svc, _journal, server = _budget_server(tmp_path, write_mode="full")
    out = _tool(server, "set_budget")("Groceries", 275, month="2026-09")
    assert out["updated"] is True
    assert svc._adapter.budget_calls == [("c1", Decimal("275.00"), "2026-09-01", False)]


def test_set_budget_mismatch_no_undo_recorded(tmp_path):
    svc, journal, server = _budget_server(tmp_path, write_mode="full")
    # make Monarch echo a different amount than requested
    svc._adapter.update_budget_item = lambda cid, amt, sd, af: Decimal("1.00")
    out = _tool(server, "set_budget")("Groceries", 250, month="2026-09")
    assert out["updated"] is False
    assert journal.last_undoable_batch() is None


def test_set_budget_defaults_to_current_month(tmp_path, monkeypatch):
    import financial_mcp.mcp_server as ms
    # pin "today" so the default month is deterministic
    class _FixedDate(datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 15)
    monkeypatch.setattr(ms.datetime, "date", _FixedDate)
    svc, _journal, server = _budget_server(tmp_path, write_mode="safe")
    out = _tool(server, "set_budget")("Groceries", 250)  # no month
    assert out["month"] == "2026-09"
```

> The `_FixedDate` monkeypatch replaces `datetime.date` inside the `mcp_server` module. If that proves brittle in the harness, an acceptable alternative is to assert only that the returned `month` matches `datetime.date.today().strftime("%Y-%m")` — the point is that an omitted month resolves to the current month.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k set_budget -v`
Expected: FAIL — `KeyError: 'set_budget'` from `_tool` (the tool isn't registered yet).

- [ ] **Step 3a: Add the `FakeAdapter.update_budget_item` stub**

So the whole suite stays green, in `tests/test_mcp_tools.py` add to `FakeAdapter` (after `update_transaction_rule`, ~line 113):

```python
    def update_budget_item(self, category_id, amount, start_date, apply_to_future):
        return None
```

- [ ] **Step 3b: Add `"set_budget"` to `REVERSIBLE_OPS`**

In `financial_mcp/cache/undo_journal.py` line 11, change:

```python
REVERSIBLE_OPS = ("recategorize", "rename_category", "delete_category", "rename_merchant", "create_rule", "create_category_rule", "update_rule", "set_budget")
```

- [ ] **Step 3c: Add `import calendar`**

In `financial_mcp/mcp_server.py`, add `import calendar` to the stdlib imports at the top (after `import datetime`, before `import json`).

- [ ] **Step 3d: Implement the tool**

In `financial_mcp/mcp_server.py`, after `create_category_rule`'s `return _guard(_run)` (line ~408) and before the `_rule_label` helper (line ~410), add:

```python
    @server.tool()
    def set_budget(
        category: str, amount: float, month: str | None = None,
        apply_to_future: bool = False, confirm_token: str | None = None,
    ) -> dict:
        """Change the planned MONTHLY budget for an EXISTING budgeted category.

        Sets `category`'s planned amount for `month` (YYYY-MM or YYYY-MM-DD; default
        = the current month) to `amount`. Only changes a category that ALREADY has a
        budget for that month — adding a budget to an unbudgeted category, or removing
        one, is not supported yet and is refused clearly. `apply_to_future` defaults to
        False (this month only); pass True to also change all future months. IMPORTANT:
        budget changes are often meant to be ongoing, so the dry-run returns a
        `scope_prompt` — ask the user "just this month, or ongoing?" before confirming.
        In 'safe' mode this is two-phase: call without confirm_token to preview (nothing
        is written) and get a confirm_token, then call again with it to apply. In 'full'
        mode it applies directly. Reversible via undo_last (restores the previous amount
        at the same scope). All amounts are dollars.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}

        def _run():
            try:
                amt = writes.normalize_budget_amount(amount)
            except writes.WriteError as exc:
                return {"error": str(exc)}
            if month is None:
                first = datetime.date.today().replace(day=1)
            else:
                try:
                    first = writes.parse_budget_month(month)
                except writes.WriteError as exc:
                    return {"error": str(exc)}
            start_date = first.isoformat()          # YYYY-MM-01
            month_label = first.strftime("%Y-%m")
            last = first.replace(day=calendar.monthrange(first.year, first.month)[1])

            cats = svc.categories()
            try:
                target = writes.resolve_category(category, cats)
            except writes.WriteError as exc:
                return {"error": str(exc)}

            rows = svc.budgets(first, last)
            current = next((r.budgeted for r in rows if r.category_id == target.id), None)
            if current is None:
                return {"error": (
                    f"No existing budget for {target.name!r} in {month_label} to change "
                    f"— adding new budgets is coming later.")}

            token = writes.compute_budget_token(target.id, start_date, amt, apply_to_future)
            scope_prompt = (
                (f"This will change only {month_label}." if not apply_to_future
                 else f"This will change {month_label} and ALL FUTURE months.")
                + " Budget changes are often meant to be ongoing — confirm whether to "
                  "apply to this month only, or to this and all future months.")

            if write_mode == "safe" and (confirm_token is None or confirm_token != token):
                return {
                    "dry_run": True,
                    "category": target.name,
                    "group": target.group,
                    "month": month_label,
                    "current_amount": float(current),
                    "new_amount": float(amt),
                    "apply_to_future": apply_to_future,
                    "scope_prompt": scope_prompt,
                    "confirm_token": token,
                    "mode": write_mode,
                    "note": "no budget change made yet",
                }

            saved = svc.update_budget_item(target.id, amt, start_date, apply_to_future)
            if saved is None or abs(saved - amt) > Decimal("0.01"):
                return {"updated": False, "reason": "Monarch did not apply the change"}

            batch_id = uuid.uuid4().hex
            old_json = json.dumps({"amount": str(current), "start_date": start_date,
                                   "apply_to_future": apply_to_future})
            new_json = json.dumps({"amount": str(amt), "start_date": start_date,
                                   "apply_to_future": apply_to_future})
            label_old = f"{target.name} {month_label} ${current}"
            label_new = f"{target.name} {month_label} ${amt}"
            journal.record(batch_id, "set_budget", target.id, old_json, new_json,
                           label_old, label_new)
            journal.mark_succeeded(batch_id, target.id)
            return {
                "updated": True,
                "category": target.name,
                "month": month_label,
                "old_amount": float(current),
                "new_amount": float(saved),
                "apply_to_future": apply_to_future,
                "undo_hint": "call undo_last to restore the previous amount",
            }

        return _guard(_run)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k set_budget -v`
Expected: PASS (11 tests).

Run the full suite: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py financial_mcp/cache/undo_journal.py tests/test_mcp_tools.py
git commit -m "feat(budget-write): add gated set_budget tool with scope prompt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Undo dispatch + history + docs

**Files:**
- Modify: `financial_mcp/mcp_server.py` (`undo_last` dispatch + failure branch, ~lines 1224-1255)
- Modify: `financial_mcp/cache/undo_journal.py` (`history()` summary, ~line 172)
- Modify: `tests/test_mcp_tools.py` (undo round-trip tests, both scopes)
- Modify: `CHANGELOG.md` (`[Unreleased]` entry)
- Modify: `README.md` (write-layer bullet)

**Interfaces:**
- Consumes: journal rows with `op_type="set_budget"` and JSON `old_value` (Task 3); `svc.update_budget_item` (Task 2).
- Produces: reversal behavior for `set_budget` in `undo_last`; a history summary line.

- [ ] **Step 1: Write the failing undo tests**

Add to `tests/test_mcp_tools.py` (reusing `_budget_server`):

```python
def test_undo_set_budget_restores_previous_amount_this_month(tmp_path):
    svc, _journal, server = _budget_server(tmp_path, write_mode="full")
    _tool(server, "set_budget")("Groceries", 250, month="2026-09")          # 200 -> 250
    assert svc._adapter._saved["c1"] == Decimal("250.00")
    out = _tool(server, "undo_last")()
    assert out["reversed"] == 1 and out["failed"] == 0
    # restored to the old amount at this-month scope
    assert svc._adapter.budget_calls[-1] == ("c1", Decimal("200.00"), "2026-09-01", False)
    assert svc._adapter._saved["c1"] == Decimal("200.00")


def test_undo_set_budget_mirrors_all_future_scope(tmp_path):
    svc, _journal, server = _budget_server(tmp_path, write_mode="full")
    _tool(server, "set_budget")("Groceries", 250, month="2026-09", apply_to_future=True)
    _tool(server, "undo_last")()
    # the compensating write is also all-future
    assert svc._adapter.budget_calls[-1] == ("c1", Decimal("200.00"), "2026-09-01", True)


def test_undo_history_includes_set_budget_summary(tmp_path):
    _svc, journal, server = _budget_server(tmp_path, write_mode="full")
    _tool(server, "set_budget")("Groceries", 250, month="2026-09")
    hist = _tool(server, "undo_history")()["history"]
    assert any("Budget" in h["summary"] for h in hist)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k "undo_set_budget or set_budget_summary" -v`
Expected: FAIL — undo reports `reversed: 0`/`failed: 1` because the `else: ok = False` branch doesn't reverse `set_budget`; the history summary has no `"Budget"`.

- [ ] **Step 3a: Add the undo dispatch case**

In `financial_mcp/mcp_server.py` `undo_last`, in the reversal `if/elif` chain, after the `update_rule` branch (line ~1224-1228) and before `else:`, add:

```python
                elif r["op_type"] == "set_budget":
                    old = json.loads(r["old_value"])
                    old_amt = Decimal(old["amount"])
                    saved = svc.update_budget_item(
                        r["target_id"], old_amt, old["start_date"], old["apply_to_future"])
                    ok = saved is not None and abs(saved - old_amt) <= Decimal("0.01")
```

Then in the failure-reporting `if/elif` chain (line ~1235-1255), after the `update_rule` failure branch and before the final `else:`, add:

```python
                    elif r["op_type"] == "set_budget":
                        failures.append({"category_id": r["target_id"],
                                         "reason": "Monarch did not restore the previous amount"})
```

- [ ] **Step 3b: Add the history summary line**

In `financial_mcp/cache/undo_journal.py` `history()`, in the `if/elif` chain on `op_type` (line ~162-175), after the `update_rule` branch and before `else:`, add:

```python
            elif op_type == "set_budget":
                summary = f"Budget: {old_n} → {new_n} ×{n}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k "undo_set_budget or set_budget_summary" -v`
Expected: PASS (3 tests).

Run the full suite: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Update the `undo_last` and `undo_history` docstrings**

In `financial_mcp/mcp_server.py`, extend the `undo_last` docstring (line ~1192) and `undo_history` docstring (line ~1271) to mention budget changes, e.g. append "…or budget change" to the list of reversible operations. This is a doc-only edit; no test.

- [ ] **Step 6: Update CHANGELOG and README**

In `CHANGELOG.md`, under `## [Unreleased]` → `### Added` (create the heading if absent), add:

```markdown
- **Budget write** — a gated `set_budget` tool sets an existing category's monthly
  budget behind the dry-run → confirm → undo safety flow, defaulting to this-month-only
  and prompting for this-month-vs-ongoing scope.
```

In `README.md`, in the "Maintain your money — safely" list (around line 44-49), add a bullet:

```markdown
- Set the monthly budget for an existing category (this month or ongoing)
```

And in the Roadmap section, update the "Goal and budget *editing*" line (line ~112) to note budget editing has partially landed (changing existing amounts), while adding/removing budgets and goals remain planned.

- [ ] **Step 7: Commit**

```bash
git add financial_mcp/mcp_server.py financial_mcp/cache/undo_journal.py tests/test_mcp_tools.py CHANGELOG.md README.md
git commit -m "feat(budget-write): reverse set_budget in undo_last + docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Live prove-first (before merge, on-branch)

After all four tasks pass and the whole-branch review is clean, prove the round-trip against real Monarch data in Claude Desktop (write_mode temporarily `safe`), mirroring every prior write's discipline:

1. `set_budget("Phone", <a test value>, month=<current month>)` → verify the dry-run shows the real current Phone amount + a scope_prompt.
2. Confirm with the token → verify in the Monarch app that Phone's budget changed for this month only.
3. **Verify the "unbudgeted = absent" assumption:** call `set_budget` on a category you know has no budget this month → it must refuse with "coming later" (not silently write). If Monarch instead returns a zero-amount row for unbudgeted categories, the refusal logic needs revisiting before merge — this is the one design assumption that only live data can confirm.
4. `undo_last` → verify Phone is back to its exact original amount.

## Self-Review

**1. Spec coverage:**
- Change existing amount, one category, one month → Task 3 tool. ✓
- Captured `Common_UpdateBudgetItem` shape → Task 2 query + adapter. ✓
- Category by name, fail-closed → `resolve_category` in Task 3. ✓
- Unbudgeted refusal → Task 3 (`current is None`). ✓
- Negative refusal → Task 1 `normalize_budget_amount` + Task 3 test. ✓
- Default this-month, override apply_to_future → Task 3. ✓
- Scope prompt (user's added requirement) → Task 3 `scope_prompt` + tests. ✓
- Token binds scope → Task 1 `compute_budget_token` + tests. ✓
- Verify-by-response, mismatch → no undo → Task 3. ✓
- Undo mirrors scope → Task 4 dispatch + tests. ✓
- Month default = current month, override → Task 3 + test. ✓
- All 13 spec test cases → distributed across Tasks 1, 3, 4. ✓
- CHANGELOG/README rollout → Task 4. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has real code. The two "check the real signature" notes (CsvAdapter constructor; `_FixedDate` fallback) are explicit verification instructions with a concrete fallback, not placeholders.

**3. Type consistency:** `update_budget_item(category_id, amount, start_date, apply_to_future) -> Decimal | None` is identical across base/community/csv/fake/data_service and both call sites (tool + undo). `compute_budget_token(category_id, start_date, amount, apply_to_future)` argument order matches between definition (Task 1) and both uses (Task 3 tool). `BudgetItem.budgeted`/`.category_id` match the model (`models.py:113-117`). Journal `record(batch_id, op_type, target_id, old_value, new_value, label_old, label_new)` positional order matches `undo_journal.py:66`.
