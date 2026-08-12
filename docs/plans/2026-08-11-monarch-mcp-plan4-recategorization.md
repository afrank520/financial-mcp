# Plan 4 Slice 1: Transaction Recategorization (writes + multi-level undo) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Monarch MCP's first mutating capability — single + bulk transaction recategorization — behind a safety gate, with a local multi-level undo net.

**Architecture:** One-directional layering, same as reads. A pure-logic `writes.py` (gate rules, fail-closed category name→id resolution, filter matching, confirm-token hashing) sits above a new adapter mutation method (`recategorize_transaction`, via the existing generic GraphQL transport) and a new `UndoJournal` (separate SQLite `write_journal` table, compensating-write undo). Four new MCP tools orchestrate them.

**Tech Stack:** Python 3.14, Pydantic v2, `Decimal` money, FastMCP over stdio, SQLite cache, `aiohttp` transport, pytest. Spec: `docs/specs/2026-08-11-monarch-mcp-plan4-recategorization-design.md`.

## Global Constraints

Copied verbatim from the spec — every task's requirements implicitly include these:

- **Modes:** `write_mode: Literal["off","safe","full"]`; shipped default `safe` (from `Config`); `off` recommended when sharing. In `safe`: single recategorization one-shots; **bulk requires dry-run → `confirm_token` handshake**. In `full`: bulk one-shots too. **All writes are capped at `batch_cap` and journaled regardless of mode.** `off` refuses every write before any resolution or network call.
- **`confirm_token` = deterministic hash of the exact resolved change-set** (sorted transaction ids + target category id). Validates ONLY the identical operation previewed; a mismatch (data shifted, or a minted token) → refuse and re-preview. Never a random opaque stamp.
- **Fail-closed category name→id resolution:** exact case-insensitive match → proceed; no match → refuse with the valid category-name list; ambiguous (duplicate names) → refuse showing both with their groups. **Never create-on-the-fly, never fuzzy-guess.**
- **Bulk filter reuses the `search_transactions` filter** — `merchant`, `category`, `min_amount`, `max_amount` — plus an optional date window (`start`/`end`, `YYYY-MM-DD`). Zero new query surface.
- **Over-cap → refuse** with the count ("narrow the filter or raise batch_cap"). **No truncation.**
- **Monarch mutation input is `{"id": transaction_id, "category": category_id}`** (the field is `category`, NOT `categoryId`). A 200 whose response did not apply the target category is a **failed** row, not a changed one.
- **Undo = compensating write, not time-travel;** multi-level; journal in a separate `write_journal` table (no `SCHEMA_VERSION` coupling with the read cache). Only `succeeded` rows with a resolvable old category id are undoable.
- **Partial bulk failure:** no rollback (Monarch has none) — return an honest mixed result `{changed, failed, failures[]}`; only successes journaled.
- **Nothing invented; nothing journaled if auth fails.** `CsvAdapter` write raises `RuntimeError("writes are not available in CSV mode")`.
- **Tests offline/deterministic — no live mutations in the suite.** Live-verify interactively at ship.
- **No `SCHEMA_VERSION` bump** to the read cache.

---

## File Structure

- **Create `financial_mcp/writes.py`** — pure logic: `WriteError`, `resolve_category`, `compute_confirm_token`, `match_transactions`. No I/O.
- **Create `financial_mcp/cache/undo_journal.py`** — `UndoJournal`: own SQLite connection to `db_path`, `write_journal` table, record/mark/query/history.
- **Modify `financial_mcp/monarch/adapter.py`** — add abstract `recategorize_transaction`.
- **Modify `financial_mcp/monarch/queries.py`** — add `UPDATE_TRANSACTION_CATEGORY` mutation constant.
- **Modify `financial_mcp/monarch/community_adapter.py`** — implement `recategorize_transaction` (verifies applied category).
- **Modify `financial_mcp/monarch/csv_adapter.py`** — `recategorize_transaction` raises the CSV-mode RuntimeError.
- **Modify `financial_mcp/data_service.py`** — add `recategorize(transaction_id, category_id)` that delegates to the adapter and invalidates the `transactions` cache.
- **Modify `financial_mcp/mcp_server.py`** — extend `build_server` signature; add four tools.
- **Modify `financial_mcp/main.py`** — construct `UndoJournal`, pass `write_mode`/`batch_cap`/`journal` into `build_server`.
- **Tests:** `tests/test_writes.py`, `tests/test_undo_journal.py`, `tests/test_recategorize_adapter.py`, and additions to `tests/test_mcp_tools.py`, `tests/test_data_service.py`.

---

## Task 1: Pure write-logic module (`writes.py`)

**Files:**
- Create: `financial_mcp/writes.py`
- Test: `tests/test_writes.py`

**Interfaces:**
- Consumes: `financial_mcp.monarch.models.Category`, `.Transaction`; `financial_mcp.analytics.spending.search`.
- Produces:
  - `class WriteError(Exception)` — user-facing message in `str(exc)`.
  - `resolve_category(name: str, categories: list[Category]) -> Category` — exact case-insensitive; raises `WriteError` on none/ambiguous.
  - `compute_confirm_token(transaction_ids: list[str], target_category_id: str) -> str` — deterministic 16-char hex.
  - `match_transactions(txns: list[Transaction], *, merchant: str | None = None, category: str | None = None, min_amount: Decimal | None = None, max_amount: Decimal | None = None, start: datetime.date | None = None, end: datetime.date | None = None) -> list[Transaction]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_writes.py
import datetime
from decimal import Decimal

import pytest

from financial_mcp import writes
from financial_mcp.monarch.models import Category, Transaction


def _cat(id, name, group=None):
    return Category(id=id, name=name, group=group)


def _txn(id, merchant, category, amount, date):
    return Transaction(
        id=id, date=date, merchant=merchant, category=category, account="Checking",
        original_statement=merchant, notes="", amount=Decimal(amount), tags=[], owner="me",
    )


CATS = [_cat("1", "Shopping", "Discretionary"), _cat("2", "Groceries", "Food")]


def test_resolve_category_exact_case_insensitive():
    assert writes.resolve_category("shopping", CATS).id == "1"


def test_resolve_category_no_match_refuses_with_valid_list():
    with pytest.raises(writes.WriteError) as exc:
        writes.resolve_category("Restaurants", CATS)
    msg = str(exc.value)
    assert "Restaurants" in msg and "Shopping" in msg and "Groceries" in msg


def test_resolve_category_ambiguous_refuses_with_groups():
    cats = [_cat("1", "Auto", "Transport"), _cat("2", "Auto", "Insurance")]
    with pytest.raises(writes.WriteError) as exc:
        writes.resolve_category("Auto", cats)
    m = str(exc.value)
    assert "Transport" in m and "Insurance" in m


def test_confirm_token_is_deterministic_and_order_independent():
    a = writes.compute_confirm_token(["t2", "t1"], "cat9")
    b = writes.compute_confirm_token(["t1", "t2"], "cat9")
    assert a == b


def test_confirm_token_changes_with_set_or_target():
    base = writes.compute_confirm_token(["t1", "t2"], "cat9")
    assert base != writes.compute_confirm_token(["t1", "t2", "t3"], "cat9")
    assert base != writes.compute_confirm_token(["t1", "t2"], "cat8")


def test_match_transactions_reuses_search_and_date_window():
    txns = [
        _txn("a", "Amazon", "Uncategorized", "-10", datetime.date(2026, 6, 3)),
        _txn("b", "Amazon", "Uncategorized", "-99", datetime.date(2026, 6, 4)),
        _txn("c", "Whole Foods", "Groceries", "-20", datetime.date(2026, 6, 5)),
        _txn("d", "Amazon", "Uncategorized", "-10", datetime.date(2026, 7, 1)),
    ]
    out = writes.match_transactions(
        txns, merchant="amazon", max_amount=Decimal("-5"),
        start=datetime.date(2026, 6, 1), end=datetime.date(2026, 6, 30),
    )
    assert {t.id for t in out} == {"a", "b"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_writes.py -q`
Expected: FAIL — `ModuleNotFoundError: financial_mcp.writes` / attribute errors.

- [ ] **Step 3: Write the implementation**

```python
# financial_mcp/writes.py
from __future__ import annotations

import datetime
import hashlib
from decimal import Decimal

from financial_mcp.analytics.spending import search
from financial_mcp.monarch.models import Category, Transaction


class WriteError(Exception):
    """A write cannot proceed (fail-closed). str(exc) is the user-facing message."""


def resolve_category(name: str, categories: list[Category]) -> Category:
    """Exact case-insensitive name -> Category. Fail-closed: never creates, never fuzzy."""
    matches = [c for c in categories if c.name.lower() == name.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        valid = ", ".join(sorted(c.name for c in categories))
        raise WriteError(
            f"No category named {name!r}. Pick an existing category: {valid}"
        )
    groups = "; ".join(f"{c.name} (group: {c.group})" for c in matches)
    raise WriteError(
        f"Category name {name!r} is ambiguous — {len(matches)} match: {groups}. "
        "Disambiguate before writing."
    )


def compute_confirm_token(transaction_ids: list[str], target_category_id: str) -> str:
    """Deterministic content hash of the exact change-set. Order-independent."""
    key = "|".join(sorted(transaction_ids)) + "->" + target_category_id
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def match_transactions(
    txns: list[Transaction],
    *,
    merchant: str | None = None,
    category: str | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    start: datetime.date | None = None,
    end: datetime.date | None = None,
) -> list[Transaction]:
    """Bulk selection: optional date window then the proven search() filter."""
    result = txns
    if start is not None:
        result = [t for t in result if t.date >= start]
    if end is not None:
        result = [t for t in result if t.date <= end]
    return search(
        result, merchant=merchant, category=category,
        min_amount=min_amount, max_amount=max_amount,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_writes.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/writes.py tests/test_writes.py
git commit -m "feat: writes.py — fail-closed category resolution, confirm-token hash, bulk match"
```

---

## Task 2: Adapter mutation — `recategorize_transaction`

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (add abstract method after `get_recurring`)
- Modify: `financial_mcp/monarch/queries.py` (add `UPDATE_TRANSACTION_CATEGORY`)
- Modify: `financial_mcp/monarch/community_adapter.py` (implement)
- Modify: `financial_mcp/monarch/csv_adapter.py` (raise CSV-mode error)
- Test: `tests/test_recategorize_adapter.py`
- Modify tests: any `FakeAdapter`/`_StubAdapter` in `tests/test_mcp_tools.py` and `tests/test_data_service.py` need the new method to stay instantiable (see Task 4/6 — add there if not needed yet, but the ABC gains an abstractmethod now, so update every concrete test adapter in THIS task).

**Interfaces:**
- Produces on `MonarchAdapter`: `recategorize_transaction(self, transaction_id: str, category_id: str) -> bool` — returns `True` if the mutation response confirms the target category applied; `False` on a 200-but-no-change.
- `UPDATE_TRANSACTION_CATEGORY: str` — GraphQL mutation string, operation name `Web_TransactionDrawerUpdateTransaction`.

**Context — the mutation shape (verified against the reference lib):** input is `{"id": transaction_id, "category": category_id}`. The response returns `updateTransaction.transaction { id category { id } }`; verify `transaction.category.id == category_id`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recategorize_adapter.py
import pytest

from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter


class _Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __call__(self, operation, query, variables):
        self.calls.append((operation, query, variables))
        return self.response


def _adapter(response):
    t = _Transport(response)
    a = CommunityAdapter(
        session={"session_id": "s", "csrftoken": "c"},
        service_name="svc",
        transport=t,
    )
    return a, t


def test_recategorize_sends_category_input_and_confirms_applied():
    a, t = _adapter({"updateTransaction": {"transaction": {"id": "tx1", "category": {"id": "cat9"}}}})
    assert a.recategorize_transaction("tx1", "cat9") is True
    op, _query, variables = t.calls[0]
    assert op == "Web_TransactionDrawerUpdateTransaction"
    assert variables["input"] == {"id": "tx1", "category": "cat9"}


def test_recategorize_200_but_no_change_returns_false():
    # Monarch accepted the call but the category did not change to the target.
    a, t = _adapter({"updateTransaction": {"transaction": {"id": "tx1", "category": {"id": "OLD"}}}})
    assert a.recategorize_transaction("tx1", "cat9") is False


def test_csv_adapter_recategorize_refuses():
    csv = CsvAdapter("unused_txns.csv", "unused_balances.csv")
    with pytest.raises(RuntimeError, match="writes are not available in CSV mode"):
        csv.recategorize_transaction("tx1", "cat9")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_recategorize_adapter.py -q`
Expected: FAIL — `recategorize_transaction` not defined / `TypeError: Can't instantiate abstract class`.

- [ ] **Step 3a: Add the abstract method** (`financial_mcp/monarch/adapter.py`, after the `get_recurring` abstractmethod, before `source_name`)

```python
    @abc.abstractmethod
    def recategorize_transaction(self, transaction_id: str, category_id: str) -> bool: ...
```

- [ ] **Step 3b: Add the mutation constant** (`financial_mcp/monarch/queries.py`, appended)

```python
UPDATE_TRANSACTION_CATEGORY = """
mutation Web_TransactionDrawerUpdateTransaction($input: UpdateTransactionMutationInput!) {
  updateTransaction(input: $input) {
    transaction {
      id
      category { id __typename }
      __typename
    }
    __typename
  }
}
"""
```

- [ ] **Step 3c: Implement on `CommunityAdapter`** (add method; import `UPDATE_TRANSACTION_CATEGORY` in the queries import block)

```python
    def recategorize_transaction(self, transaction_id: str, category_id: str) -> bool:
        data = self._run(
            self._transport(
                "Web_TransactionDrawerUpdateTransaction",
                UPDATE_TRANSACTION_CATEGORY,
                {"input": {"id": transaction_id, "category": category_id}},
            )
        )
        txn = (data.get("updateTransaction") or {}).get("transaction") or {}
        applied = (txn.get("category") or {}).get("id")
        return applied == category_id
```

- [ ] **Step 3d: Implement the refusal on `CsvAdapter`**

```python
    def recategorize_transaction(self, transaction_id: str, category_id: str) -> bool:
        raise RuntimeError("writes are not available in CSV mode")
```

- [ ] **Step 3e: Update every concrete test adapter** so the suite still instantiates. In `tests/test_mcp_tools.py` and `tests/test_data_service.py`, add to each `FakeAdapter`/`_StubAdapter` class:

```python
    def recategorize_transaction(self, transaction_id, category_id):
        # tests that exercise writes override/inspect this; default records the call
        self.recategorized = getattr(self, "recategorized", [])
        self.recategorized.append((transaction_id, category_id))
        return True
```
(Leave `tests/test_adapter_interface.py::Partial` intentionally abstract — do not implement there.)

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — new adapter tests green, all prior tests still green (no behavior change to reads).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/ tests/test_recategorize_adapter.py tests/test_mcp_tools.py tests/test_data_service.py
git commit -m "feat: recategorize_transaction adapter mutation (verifies applied category) + CSV refusal"
```

---

## Task 3: Undo journal (`UndoJournal`)

**Files:**
- Create: `financial_mcp/cache/undo_journal.py`
- Test: `tests/test_undo_journal.py`

**Interfaces:**
- Produces:
  - `class UndoJournal.__init__(self, db_path: str, clock: Callable[[], float] = time.time)` — creates the `write_journal` table if absent.
  - `record(self, batch_id: str, transaction_id: str, old_category_id: str | None, old_category_name: str, new_category_id: str, new_category_name: str) -> None` — inserts a row with `succeeded=0`, `undone=0`, next monotonic `seq`, timestamp from clock. Called **before** the mutation fires.
  - `mark_succeeded(self, batch_id: str, transaction_id: str) -> None` — sets `succeeded=1` for that row.
  - `last_undoable_batch(self) -> str | None` — most recent `batch_id` (max seq) that has at least one row with `succeeded=1 AND undone=0 AND old_category_id IS NOT NULL`.
  - `undoable_rows(self, batch_id: str) -> list[dict]` — that batch's rows with `succeeded=1 AND undone=0 AND old_category_id IS NOT NULL`, each `{transaction_id, old_category_id, old_category_name, new_category_name}`.
  - `mark_undone(self, batch_id: str) -> None` — sets `undone=1` for all rows of the batch.
  - `history(self, limit: int = 20) -> list[dict]` — newest-first, one entry per batch: `{batch_id, when, count, summary, undone}` where `summary` = e.g. `"Uncategorized→Shopping ×3"` (uses the batch's most common old→new names) and `undone` = all rows undone.

**Context:** separate SQLite connection to the same `db_path`; a distinct table `write_journal`; NOT touched by `SqliteCache` and NOT subject to `SCHEMA_VERSION`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_undo_journal.py
from financial_mcp.cache.undo_journal import UndoJournal


def _journal(tmp_path):
    return UndoJournal(str(tmp_path / "cache.db"), clock=lambda: 1000.0)


def test_record_then_mark_succeeded_makes_row_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "tx1", "old1", "Uncategorized", "new1", "Shopping")
    assert j.last_undoable_batch() is None  # not yet succeeded
    j.mark_succeeded("b1", "tx1")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows == [{
        "transaction_id": "tx1", "old_category_id": "old1",
        "old_category_name": "Uncategorized", "new_category_name": "Shopping",
    }]


def test_row_without_old_id_is_not_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "tx1", None, "Ambiguous", "new1", "Shopping")
    j.mark_succeeded("b1", "tx1")
    assert j.last_undoable_batch() is None


def test_multi_level_undo_steps_back_through_batches(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "tx1", "old1", "A", "new1", "Shopping"); j.mark_succeeded("b1", "tx1")
    j.record("b2", "tx2", "old2", "B", "new2", "Groceries"); j.mark_succeeded("b2", "tx2")
    assert j.last_undoable_batch() == "b2"
    j.mark_undone("b2")
    assert j.last_undoable_batch() == "b1"
    j.mark_undone("b1")
    assert j.last_undoable_batch() is None


def test_history_is_newest_first_with_summary_and_undone_flag(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "tx1", "o", "Uncategorized", "n", "Shopping"); j.mark_succeeded("b1", "tx1")
    j.record("b1", "tx2", "o", "Uncategorized", "n", "Shopping"); j.mark_succeeded("b1", "tx2")
    j.record("b2", "tx3", "o", "Dining", "n", "Groceries"); j.mark_succeeded("b2", "tx3")
    hist = j.history()
    assert [h["batch_id"] for h in hist] == ["b2", "b1"]
    assert hist[1]["count"] == 2
    assert hist[1]["summary"] == "Uncategorized→Shopping ×2"
    assert hist[0]["undone"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_undo_journal.py -q`
Expected: FAIL — module/class missing.

- [ ] **Step 3: Write the implementation**

```python
# financial_mcp/cache/undo_journal.py
from __future__ import annotations

import datetime
import sqlite3
import time
from collections import Counter
from collections.abc import Callable


class UndoJournal:
    """Local, compensating-write undo log for mutating tools. Lives in its own
    `write_journal` table in the cache DB — never touched by SqliteCache and not
    subject to SCHEMA_VERSION."""

    def __init__(self, db_path: str, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS write_journal (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                old_category_id TEXT,
                old_category_name TEXT NOT NULL,
                new_category_id TEXT NOT NULL,
                new_category_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0
            )
            """)
        self._conn.commit()

    def record(self, batch_id, transaction_id, old_category_id,
               old_category_name, new_category_id, new_category_name) -> None:
        ts = datetime.datetime.fromtimestamp(
            self._clock(), tz=datetime.timezone.utc
        ).isoformat()
        self._conn.execute(
            """INSERT INTO write_journal
               (batch_id, transaction_id, old_category_id, old_category_name,
                new_category_id, new_category_name, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, transaction_id, old_category_id, old_category_name,
             new_category_id, new_category_name, ts),
        )
        self._conn.commit()

    def mark_succeeded(self, batch_id, transaction_id) -> None:
        self._conn.execute(
            "UPDATE write_journal SET succeeded=1 WHERE batch_id=? AND transaction_id=?",
            (batch_id, transaction_id),
        )
        self._conn.commit()

    def last_undoable_batch(self) -> str | None:
        row = self._conn.execute(
            """SELECT batch_id FROM write_journal
               WHERE succeeded=1 AND undone=0 AND old_category_id IS NOT NULL
               ORDER BY seq DESC LIMIT 1"""
        ).fetchone()
        return row[0] if row else None

    def undoable_rows(self, batch_id) -> list[dict]:
        rows = self._conn.execute(
            """SELECT transaction_id, old_category_id, old_category_name, new_category_name
               FROM write_journal
               WHERE batch_id=? AND succeeded=1 AND undone=0 AND old_category_id IS NOT NULL
               ORDER BY seq""",
            (batch_id,),
        ).fetchall()
        return [
            {"transaction_id": r[0], "old_category_id": r[1],
             "old_category_name": r[2], "new_category_name": r[3]}
            for r in rows
        ]

    def mark_undone(self, batch_id) -> None:
        self._conn.execute(
            "UPDATE write_journal SET undone=1 WHERE batch_id=?", (batch_id,)
        )
        self._conn.commit()

    def history(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT batch_id, transaction_id, old_category_name, new_category_name,
                      timestamp, undone, MAX(seq) AS mseq
               FROM write_journal GROUP BY batch_id ORDER BY mseq DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for batch_id, _tx, _on, _nn, _ts, _undone, _mseq in rows:
            detail = self._conn.execute(
                """SELECT old_category_name, new_category_name, timestamp, undone
                   FROM write_journal WHERE batch_id=? ORDER BY seq""",
                (batch_id,),
            ).fetchall()
            pairs = Counter((d[0], d[1]) for d in detail)
            (old_n, new_n), n = pairs.most_common(1)[0]
            out.append({
                "batch_id": batch_id,
                "when": detail[0][2],
                "count": len(detail),
                "summary": f"{old_n}→{new_n} ×{n}",
                "undone": all(d[3] == 1 for d in detail),
            })
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_undo_journal.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/cache/undo_journal.py tests/test_undo_journal.py
git commit -m "feat: UndoJournal — write_journal table, record/mark/undo, multi-level history"
```

---

## Task 4: Single recategorization tool + wiring

**Files:**
- Modify: `financial_mcp/data_service.py` (add `recategorize`)
- Modify: `financial_mcp/mcp_server.py` (extend `build_server`; add `recategorize_transaction` tool + write-gate helper)
- Modify: `financial_mcp/main.py` (construct `UndoJournal`, pass write config)
- Modify tests: `tests/test_mcp_tools.py`, `tests/test_data_service.py`

**Interfaces:**
- Consumes: `writes.resolve_category`, `writes.WriteError`; `DataService.transactions()`, `.categories()`; `UndoJournal`.
- Produces:
  - `DataService.recategorize(self, transaction_id: str, category_id: str) -> bool` — delegates to `self._adapter.recategorize_transaction`, then `self._cache.invalidate("transactions")`, returns the adapter's bool.
  - `build_server(svc: DataService, *, write_mode: str = "off", batch_cap: int = 200, journal: "UndoJournal | None" = None) -> FastMCP` — new keyword-only params; default `write_mode="off"` so existing read-only tests need no change (production passes `Config`'s `safe`).
  - MCP tool `recategorize_transaction(transaction_id: str, category: str) -> dict`.

**Context:** the shipped default lives in `Config.write_mode` (already `"safe"`); `build_server`'s own param default is `"off"` purely so existing tests that call `build_server(svc)` keep passing (writes present but refuse). Single write resolves the **new** category name→id (fail-closed) and the transaction's **current** category name→id for the undo journal; if the current category name is unresolvable/ambiguous, journal `old_category_id=None` (the row records but is not undoable — an honest edge).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_mcp_tools.py`; reuse its existing FakeAdapter + a real `UndoJournal` on a tmp db)

```python
def test_recategorize_off_mode_refuses(tmp_path):
    svc = _service_with(...)  # existing helper; adapter has one txn "tx1" category "Uncategorized"
    from financial_mcp.cache.undo_journal import UndoJournal
    from financial_mcp.mcp_server import build_server
    server = build_server(svc, write_mode="off", batch_cap=200,
                          journal=UndoJournal(str(tmp_path / "j.db")))
    tool = _get_tool(server, "recategorize_transaction")
    out = tool(transaction_id="tx1", category="Shopping")
    assert "disabled" in out["error"]


def test_recategorize_safe_mode_one_shot_records_and_invalidates(tmp_path):
    # adapter has txn "tx1" (category "Uncategorized"); categories include Uncategorized+Shopping
    ...
    server = build_server(svc, write_mode="safe", batch_cap=200, journal=journal)
    tool = _get_tool(server, "recategorize_transaction")
    out = tool(transaction_id="tx1", category="Shopping")
    assert out["changed"] == 1 and out["to"] == "Shopping"
    assert adapter.recategorized == [("tx1", "<shopping-id>")]
    assert journal.last_undoable_batch() == out["batch_id"]


def test_recategorize_bad_category_refuses_with_valid_list(tmp_path):
    out = tool(transaction_id="tx1", category="Nonexistent")
    assert "Nonexistent" in out["error"] and "Shopping" in out["error"]
```

(Use the file's existing tool-extraction and service-construction helpers; if none exists, extract a tool via `server._tool_manager` the same way current tests do.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL — `build_server` has no `write_mode` kwarg / tool missing.

- [ ] **Step 3a: `DataService.recategorize`** (`financial_mcp/data_service.py`)

```python
    def recategorize(self, transaction_id: str, category_id: str) -> bool:
        applied = self._adapter.recategorize_transaction(transaction_id, category_id)
        # A write makes the cached transactions stale — force a re-pull next read.
        self._cache.invalidate("transactions")
        return applied
```

- [ ] **Step 3b: Extend `build_server` + add the gate helper + single tool** (`financial_mcp/mcp_server.py`)

Change the signature:
```python
def build_server(svc: DataService, *, write_mode: str = "off",
                 batch_cap: int = 200, journal=None) -> FastMCP:
```
Add, inside `build_server` (near `_guard`), a helper and a `uuid` import at top of file:
```python
    def _writes_enabled() -> str | None:
        if write_mode == "off":
            return "writes are disabled. Set write_mode to 'safe' or 'full' to enable."
        return None
```
Add the tool:
```python
    @server.tool()
    def recategorize_transaction(transaction_id: str, category: str) -> dict:
        """Recategorize ONE transaction to an existing category (by name).

        Fail-closed: the category name must match an existing Monarch category
        exactly (case-insensitive) or the call refuses with the valid list. In
        write_mode 'off' this refuses; in 'safe'/'full' it executes one-shot and
        is reversible via undo_last. All figures pass through to Monarch as-is.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}

        def _run():
            cats = svc.categories()
            try:
                target = writes.resolve_category(category, cats)
            except writes.WriteError as exc:
                return {"error": str(exc)}
            txn = next((t for t in svc.transactions() if t.id == transaction_id), None)
            if txn is None:
                return {"error": f"No transaction with id {transaction_id!r}."}
            old_matches = [c for c in cats if c.name.lower() == txn.category.lower()]
            old_id = old_matches[0].id if len(old_matches) == 1 else None
            batch_id = uuid.uuid4().hex
            journal.record(batch_id, txn.id, old_id, txn.category, target.id, target.name)
            applied = svc.recategorize(txn.id, target.id)
            if applied:
                journal.mark_succeeded(batch_id, txn.id)
                return {"changed": 1, "from": txn.category, "to": target.name,
                        "batch_id": batch_id, "undo_hint": "call undo_last to reverse"}
            return {"changed": 0, "failed": 1,
                    "failures": [{"transaction_id": txn.id, "reason": "Monarch did not apply the category"}]}

        return _guard(_run)
```
Add `import uuid` and `from financial_mcp import writes` to the imports.

- [ ] **Step 3c: Wire `main.py`**

```python
    from financial_mcp.cache.undo_journal import UndoJournal
    journal = UndoJournal(cfg.db_path)
    server = build_server(svc, write_mode=cfg.write_mode, batch_cap=cfg.batch_cap, journal=journal)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — new single-write tests green; all existing tests green (build_server's `off` default keeps read tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/data_service.py financial_mcp/mcp_server.py financial_mcp/main.py tests/
git commit -m "feat: recategorize_transaction tool (single) + write-mode gate + journal wiring"
```

---

## Task 5: Bulk recategorization tool (`recategorize_matching`)

**Files:**
- Modify: `financial_mcp/mcp_server.py` (add `recategorize_matching` tool)
- Modify: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `writes.resolve_category`, `writes.match_transactions`, `writes.compute_confirm_token`, `writes.WriteError`; `DataService.transactions/categories/recategorize`; `UndoJournal`; module-level `_jsonify`.
- Produces: MCP tool
  `recategorize_matching(to_category: str, merchant: str | None = None, category: str | None = None, min_amount: float | None = None, max_amount: float | None = None, start: str | None = None, end: str | None = None, confirm_token: str | None = None) -> dict`.

**Context — gate behavior:**
- `off` → refuse.
- Resolve `to_category` (fail-closed). Parse `start`/`end` (reuse the `"start must be YYYY-MM-DD"` / `"end must be YYYY-MM-DD"` / `"start must not be after end"` strings). Match transactions.
- Empty match → `{"would_change": 0, "message": "no transactions matched"}`.
- Match count > `batch_cap` → `{"error": "N transactions match, over the batch cap of C. Narrow the filter or raise batch_cap."}`.
- Compute `token = compute_confirm_token([t.id ...], target.id)`.
- **`safe` + no `confirm_token`** → dry-run preview (below). **`safe` + `confirm_token`** → if `confirm_token != token` refuse+re-preview; else execute. **`full`** → execute directly (still capped).
- Execute: per matched txn, resolve old id (unambiguous else None), `journal.record`, `svc.recategorize`, `mark_succeeded` on applied; collect `changed`/`failed`/`failures`. One `batch_id` for the whole call.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_mcp_tools.py`)

```python
def test_bulk_safe_dryrun_previews_without_mutating(tmp_path):
    # 2 Amazon txns "Uncategorized"; categories incl Shopping
    server = build_server(svc, write_mode="safe", batch_cap=200, journal=journal)
    tool = _get_tool(server, "recategorize_matching")
    out = tool(to_category="Shopping", merchant="Amazon")
    assert out["dry_run"] is True and out["would_change"] == 2
    assert out["resolved_category"]["name"] == "Shopping"
    assert out["sample"][0]["current_category"] == "Uncategorized"
    assert "confirm_token" in out
    assert adapter.recategorized == []  # nothing mutated


def test_bulk_confirm_token_executes_and_journals(tmp_path):
    preview = tool(to_category="Shopping", merchant="Amazon")
    out = tool(to_category="Shopping", merchant="Amazon", confirm_token=preview["confirm_token"])
    assert out["changed"] == 2 and out["failed"] == 0
    assert journal.last_undoable_batch() == out["batch_id"]


def test_bulk_stale_token_refused_and_repreviewed(tmp_path):
    out = tool(to_category="Shopping", merchant="Amazon", confirm_token="deadbeefdeadbeef")
    assert out.get("dry_run") is True  # re-preview
    assert adapter.recategorized == []


def test_bulk_over_cap_refuses(tmp_path):
    server = build_server(svc, write_mode="safe", batch_cap=1, journal=journal)
    tool = _get_tool(server, "recategorize_matching")
    out = tool(to_category="Shopping", merchant="Amazon")
    assert "over the batch cap" in out["error"]
    assert adapter.recategorized == []


def test_bulk_empty_match(tmp_path):
    out = tool(to_category="Shopping", merchant="ZZZ-nothing")
    assert out["would_change"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL — `recategorize_matching` not defined.

- [ ] **Step 3: Implement the tool** (`financial_mcp/mcp_server.py`)

```python
    @server.tool()
    def recategorize_matching(
        to_category: str,
        merchant: str | None = None,
        category: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        start: str | None = None,
        end: str | None = None,
        confirm_token: str | None = None,
    ) -> dict:
        """Bulk-recategorize every transaction matching a filter to one category.

        Filter mirrors search_transactions (merchant/category substrings +
        amount range) plus an optional YYYY-MM-DD date window. In 'safe' mode
        this is two-phase: call without confirm_token to preview (nothing
        mutates) and get a confirm_token, then call again with that token to
        execute. In 'full' mode it executes directly. Over the batch cap it
        refuses (no truncation). Reversible via undo_last.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}

        s = e = None
        if start is not None:
            try:
                s = datetime.date.fromisoformat(start)
            except ValueError:
                return {"error": "start must be YYYY-MM-DD"}
        if end is not None:
            try:
                e = datetime.date.fromisoformat(end)
            except ValueError:
                return {"error": "end must be YYYY-MM-DD"}
        if s and e and s > e:
            return {"error": "start must not be after end"}

        def _run():
            cats = svc.categories()
            try:
                target = writes.resolve_category(to_category, cats)
            except writes.WriteError as exc:
                return {"error": str(exc)}
            matched = writes.match_transactions(
                svc.transactions(), merchant=merchant, category=category,
                min_amount=None if min_amount is None else Decimal(str(min_amount)),
                max_amount=None if max_amount is None else Decimal(str(max_amount)),
                start=s, end=e,
            )
            if not matched:
                return {"would_change": 0, "message": "no transactions matched"}
            if len(matched) > batch_cap:
                return {"error": f"{len(matched)} transactions match, over the batch cap "
                                 f"of {batch_cap}. Narrow the filter or raise batch_cap."}
            token = writes.compute_confirm_token([t.id for t in matched], target.id)

            def _preview():
                sample = [{"transaction_id": t.id, "date": t.date.isoformat(),
                           "merchant": t.merchant, "amount": str(t.amount),
                           "current_category": t.category} for t in matched[:20]]
                return {"dry_run": True, "would_change": len(matched),
                        "resolved_category": {"id": target.id, "name": target.name,
                                              "group": target.group},
                        "sample": sample, "confirm_token": token, "mode": write_mode}

            if write_mode == "safe":
                if confirm_token is None or confirm_token != token:
                    return _preview()
            # execute (safe+valid token, or full)
            by_name = {}
            for c in cats:
                by_name.setdefault(c.name.lower(), []).append(c)
            batch_id = uuid.uuid4().hex
            changed = 0
            failures = []
            for t in matched:
                oid_matches = by_name.get(t.category.lower(), [])
                old_id = oid_matches[0].id if len(oid_matches) == 1 else None
                journal.record(batch_id, t.id, old_id, t.category, target.id, target.name)
                if svc.recategorize(t.id, target.id):
                    journal.mark_succeeded(batch_id, t.id)
                    changed += 1
                else:
                    failures.append({"transaction_id": t.id,
                                     "reason": "Monarch did not apply the category"})
            return {"changed": changed, "failed": len(failures),
                    "failures": failures, "batch_id": batch_id, "to": target.name}

        return _guard(_run)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — bulk tests green, all prior green.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: recategorize_matching (bulk) — dry-run/confirm handshake, cap refusal, partial-failure result"
```

---

## Task 6: Undo tools (`undo_last`, `undo_history`)

**Files:**
- Modify: `financial_mcp/mcp_server.py` (add both tools + list them in `refresh_cache` docstring? no — separate; just add tools)
- Modify: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `UndoJournal.last_undoable_batch/undoable_rows/mark_undone/history`; `DataService.recategorize`.
- Produces: MCP tools `undo_last() -> dict`, `undo_history() -> dict`.

**Context:** `undo_last` reverses the most recent undoable batch by issuing compensating `svc.recategorize(txn_id, old_category_id)` calls, then `mark_undone`. `undo_history` returns the journal's history. Undo is itself a write — it also respects `write_mode == "off"` (refuse), since it mutates records.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_mcp_tools.py`)

```python
def test_undo_last_reverses_most_recent_batch(tmp_path):
    # After a bulk recategorize of 2 Amazon txns to Shopping (batch b):
    recat = _get_tool(server, "recategorize_matching")
    preview = recat(to_category="Shopping", merchant="Amazon")
    recat(to_category="Shopping", merchant="Amazon", confirm_token=preview["confirm_token"])
    adapter.recategorized.clear()
    undo = _get_tool(server, "undo_last")
    out = undo()
    assert out["reversed"] == 2
    # compensating writes restored the original category id for each txn
    assert all(cat_id == "<uncategorized-id>" for _tx, cat_id in adapter.recategorized)
    assert journal.last_undoable_batch() is None


def test_undo_last_nothing_to_undo(tmp_path):
    out = _get_tool(server, "undo_last")()
    assert out["reversed"] == 0


def test_undo_history_lists_batches(tmp_path):
    # after one bulk batch
    out = _get_tool(server, "undo_history")()
    assert out["history"][0]["count"] == 2
    assert "→Shopping" in out["history"][0]["summary"]


def test_undo_off_mode_refuses(tmp_path):
    server = build_server(svc, write_mode="off", batch_cap=200, journal=journal)
    out = _get_tool(server, "undo_last")()
    assert "disabled" in out["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL — `undo_last` / `undo_history` not defined.

- [ ] **Step 3: Implement the tools** (`financial_mcp/mcp_server.py`)

```python
    @server.tool()
    def undo_last() -> dict:
        """Reverse the most recent recategorization batch (multi-level: call
        again to step back to the previous one). Undo is a compensating write —
        it re-sets each transaction to the category it had when the write ran; it
        does not detect edits you made afterward. Refuses in write_mode 'off'.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}

        def _run():
            batch_id = journal.last_undoable_batch()
            if batch_id is None:
                return {"reversed": 0, "message": "nothing to undo"}
            rows = journal.undoable_rows(batch_id)
            reversed_ = 0
            for r in rows:
                if svc.recategorize(r["transaction_id"], r["old_category_id"]):
                    reversed_ += 1
            journal.mark_undone(batch_id)
            summary = rows[0]["old_category_name"] if rows else ""
            return {"reversed": reversed_, "batch_id": batch_id,
                    "restored_to": summary}

        return _guard(_run)

    @server.tool()
    def undo_history() -> dict:
        """Show the reversible recategorization batches, newest first: each with a
        summary (e.g. 'Uncategorized→Shopping ×12'), count, when, and whether it
        has already been undone.
        """
        return _guard(lambda: {"history": journal.history()})
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — undo tests green, all prior green.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: undo_last (multi-level) + undo_history tools"
```

---

## Task 7: Live-verify + mark spec shipped

**Files:**
- Modify: `docs/specs/2026-08-11-monarch-mcp-plan4-recategorization-design.md` (Status → Shipped; add live-verify results)

**Context:** interactive — done WITH the user in Claude Desktop against the real org. The assistant cannot run the MCP; the user drives it. Recategorization is reversible, so live testing is safe with undo as the net.

- [ ] **Step 1: Single write + undo, live.** In Claude Desktop (server on `write_mode: safe`), recategorize ONE known transaction to a new category; confirm in the Monarch UI; then `undo_last`; confirm the original category is restored.

- [ ] **Step 2: Small bulk, live.** `recategorize_matching` with a tight filter (a few transactions) → verify the dry-run preview lists exactly the intended rows with their current categories and a `confirm_token` → re-call with the token → confirm the change in Monarch UI → `undo_last` → confirm restored. Confirms the `updateTransaction` mutation shape and live category-id resolution.

- [ ] **Step 3: Over-cap + bad-name, live.** Run a filter that matches more than `batch_cap` (temporarily lower the cap) → confirm it refuses. Try a nonexistent category name → confirm the fail-closed refusal with the valid list.

- [ ] **Step 4: Record results.** Set the spec Status to "✅ Shipped + live-proven (2026-08-11)" and add a live-verification subsection capturing what passed and any data-shape discoveries (mirroring how the holdings/recurring spec §4.1 recorded the `cost_basis: 0.0` finding).

- [ ] **Step 5: Commit**

```bash
git add docs/specs/2026-08-11-monarch-mcp-plan4-recategorization-design.md
git commit -m "docs: Plan 4 Slice 1 recategorization — Shipped + live-proven"
```

---

## Self-Review (completed)

**Spec coverage:** §1 scope → Tasks 1-6; §2 architecture → file structure + Tasks 1-4; §3 gate/handshake/modes → Tasks 4 (gate + single), 5 (handshake, cap, fail-closed); §3 name resolution → Task 1 + used in 4/5; §3 bulk filter → Task 1 `match_transactions` + Task 5; §4.1 adapter/mutation → Task 2; §4.2 journal → Task 3; §4.3 tools → Tasks 4/5/6; §5 error handling (auth via `_guard`, partial failure, 200-no-change, stale token, off, empty) → Tasks 2/4/5; §5 boundaries → Task 3 (old_category_id None ⇒ not undoable) + documented in tool docstrings; §6 testing → each task's tests + Task 7 live-verify. No gaps.

**Placeholder scan:** the only ellipses are in Task 4/5/6 test *fixtures* (`_service_with(...)`) that intentionally defer to `tests/test_mcp_tools.py`'s existing service/tool helpers — the implementer is told to reuse them. All implementation code blocks are complete.

**Type consistency:** `recategorize_transaction(transaction_id, category_id) -> bool` consistent across ABC/Community/CSV/DataService/tests. `UndoJournal.record(...)` arg order matches every call site (Tasks 4, 5). `resolve_category`/`compute_confirm_token`/`match_transactions` signatures match their Task-1 definitions wherever used. `build_server(svc, *, write_mode, batch_cap, journal)` matches `main.py` and all test call sites. `to_category` (bulk target) is deliberately distinct from `category` (bulk filter).
