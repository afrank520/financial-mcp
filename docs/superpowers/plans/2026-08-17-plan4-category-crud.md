# Plan 4 Slice 2: Category Create + Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `create_category` and `rename_category` MCP tools behind the existing write gate, and generalize the recategorization undo journal into an operation log that one `undo_last` dispatches over.

**Architecture:** The recat-specific `write_journal` table becomes a general op-log (`op_type`, `target_id`, `old_value`, `new_value`, `label_old`, `label_new`) with a one-time in-place migration; `undo_last` dispatches the compensating write per `op_type`. Two new single-entity tools reuse the fail-closed name→id resolution and write gate proven in Slice 1. Rename is fully undoable; create is journaled but not auto-undoable (delete is out of scope).

**Tech Stack:** Python 3, `pytest`, SQLite (stdlib `sqlite3`), FastMCP, Pydantic models, GraphQL over the community adapter's `_transport`/`_run` bridge.

**Spec:** `docs/specs/2026-08-17-monarch-mcp-plan4-category-crud-design.md`

## Global Constraints

- **Fail-closed always.** A write that cannot be fully resolved refuses and mutates nothing. Never fuzzy-match, never auto-create.
- **Write gate is authoritative.** `write_mode: off` refuses every mutating tool. Single-entity tools are one-shot in `safe` and `full` (no confirm-token; that handshake is bulk-only).
- **Recat tool behavior stays byte-identical.** The *tool outputs* of `recategorize_transaction`, `recategorize_matching`, `undo_last`, `undo_history` must not change. The journal's internal Python API may change; its direct-access tests are updated to match.
- **Verify-what-came-back.** Mutating adapter methods return whether Monarch actually applied the change (compare the echoed value), mirroring `recategorize_transaction`.
- **Delete is out of scope.** No category deletion tool or adapter method in this slice.
- **Run the full suite (`pytest -q`) green before every commit.** The existing 234 tests are the migration fence.

---

### Task 1: Generalize the UndoJournal into an op-log

**Files:**
- Modify: `financial_mcp/cache/undo_journal.py` (whole file — schema, migration, `record`, `last_undoable_batch`, `undoable_rows`, `history`)
- Modify: `financial_mcp/mcp_server.py` (recat `journal.record(...)` call sites + `undo_last`)
- Test: `tests/test_undo_journal.py` (update direct-API calls; add migration + create-not-undoable tests)
- Test: `tests/test_mcp_tools.py` (update ONE direct `undoable_rows` key read; leave all tool-output assertions unchanged)

**Interfaces:**
- Produces: `UndoJournal.record(batch_id, op_type, target_id, old_value, new_value, label_old, label_new)`; `undoable_rows(batch_id) -> list[{target_id, op_type, old_value, label_old, label_new}]`; `last_undoable_batch()` selects only reversible op types with a non-null `old_value`; `history()` rows unchanged shape (`batch_id, when, count, summary, undone`).
- Consumes: nothing new.

- [ ] **Step 1: Write the failing migration + generalized-API tests**

Replace the body of `tests/test_undo_journal.py` calls to the new `record` signature and add migration coverage. Add these tests (keep the existing `_journal` helper):

```python
def test_migration_preserves_old_recat_rows(tmp_path):
    import sqlite3
    from financial_mcp.cache.undo_journal import UndoJournal
    db = str(tmp_path / "old.db")
    # Simulate a Slice-1 journal DB with the OLD schema + one succeeded row.
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE write_journal (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
        transaction_id TEXT NOT NULL, old_category_id TEXT,
        old_category_name TEXT NOT NULL, new_category_id TEXT NOT NULL,
        new_category_name TEXT NOT NULL, timestamp TEXT NOT NULL,
        undone INTEGER NOT NULL DEFAULT 0, succeeded INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""INSERT INTO write_journal
        (batch_id, transaction_id, old_category_id, old_category_name,
         new_category_id, new_category_name, timestamp, undone, succeeded)
        VALUES ('b1','tx1','oid','Dining','nid','Groceries','2026-08-01T00:00:00+00:00',0,1)""")
    conn.commit(); conn.close()

    j = UndoJournal(db)  # opening it runs the migration
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows == [{"target_id": "tx1", "op_type": "recategorize",
                     "old_value": "oid", "label_old": "Dining", "label_new": "Groceries"}]


def test_rename_row_is_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "rename_category", "cat1", "Old Name", "New Name", "Old Name", "New Name")
    j.mark_succeeded("b1", "cat1")
    assert j.last_undoable_batch() == "b1"
    assert j.undoable_rows("b1")[0]["op_type"] == "rename_category"


def test_create_row_is_recorded_but_not_undoable(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "create_category", "newcat", None, "Vacations", None, "Vacations")
    j.mark_succeeded("b1", "newcat")
    assert j.last_undoable_batch() is None          # create is never selected
    assert j.undoable_rows("b1") == []
    hist = j.history()
    assert hist[0]["summary"] == "＋Vacations (created) ×1"
```

Update the EXISTING tests in this file to the new `record` signature. For example the history test becomes:

```python
def test_history_is_newest_first_with_summary_and_undone_flag(tmp_path):
    j = _journal(tmp_path)
    j.record("b1", "recategorize", "tx1", "o", "n", "Uncategorized", "Shopping"); j.mark_succeeded("b1", "tx1")
    j.record("b1", "recategorize", "tx2", "o", "n", "Uncategorized", "Shopping"); j.mark_succeeded("b1", "tx2")
    j.record("b2", "recategorize", "tx3", "o", "n", "Dining", "Groceries"); j.mark_succeeded("b2", "tx3")
    hist = j.history()
    assert [h["batch_id"] for h in hist] == ["b2", "b1"]
    assert hist[1]["count"] == 2
    assert hist[1]["summary"] == "Uncategorized→Shopping ×2"
    assert hist[0]["undone"] is False
```

Apply the same signature change (`record(batch_id, "recategorize", <target_id>, <old_value>, <new_value>, <label_old>, <label_new>)`) to every other `j.record(...)` call in this file, and change any `r["transaction_id"]` read on `undoable_rows` output to `r["target_id"]`.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_undo_journal.py -q`
Expected: FAIL (old `record` signature / no migration / summary mismatch).

- [ ] **Step 3: Rewrite `undo_journal.py` with the general schema + migration**

```python
# financial_mcp/cache/undo_journal.py
from __future__ import annotations

import datetime
import sqlite3
import time
from collections.abc import Callable

REVERSIBLE_OPS = ("recategorize", "rename_category")


class UndoJournal:
    """Local, compensating-write undo log for mutating tools. A general op-log:
    each row is one operation with an op_type the dispatcher reverses. Lives in
    its own `write_journal` table in the cache DB, not under SCHEMA_VERSION."""

    def __init__(self, db_path: str, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(db_path)
        self._migrate()
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS write_journal (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                op_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT NOT NULL,
                label_old TEXT,
                label_new TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0
            )
            """)
        self._conn.commit()

    def _migrate(self) -> None:
        """One-time in-place migration from the Slice-1 recat-only schema."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(write_journal)")}
        if not cols or "op_type" in cols:
            return  # fresh install or already migrated
        # Old schema present: copy rows into the generalized shape, preserving seq.
        self._conn.execute("ALTER TABLE write_journal RENAME TO write_journal_old")
        self._conn.execute("""
            CREATE TABLE write_journal (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL, op_type TEXT NOT NULL, target_id TEXT NOT NULL,
                old_value TEXT, new_value TEXT NOT NULL, label_old TEXT, label_new TEXT NOT NULL,
                timestamp TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0)""")
        self._conn.execute("""
            INSERT INTO write_journal
                (seq, batch_id, op_type, target_id, old_value, new_value,
                 label_old, label_new, timestamp, undone, succeeded)
            SELECT seq, batch_id, 'recategorize', transaction_id, old_category_id,
                   new_category_id, old_category_name, new_category_name,
                   timestamp, undone, succeeded
            FROM write_journal_old""")
        self._conn.execute("DROP TABLE write_journal_old")
        self._conn.commit()

    def record(self, batch_id, op_type, target_id, old_value,
               new_value, label_old, label_new) -> None:
        ts = datetime.datetime.fromtimestamp(
            self._clock(), tz=datetime.timezone.utc
        ).isoformat()
        self._conn.execute(
            """INSERT INTO write_journal
               (batch_id, op_type, target_id, old_value, new_value,
                label_old, label_new, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, op_type, target_id, old_value, new_value, label_old, label_new, ts),
        )
        self._conn.commit()

    def mark_succeeded(self, batch_id, target_id) -> None:
        self._conn.execute(
            "UPDATE write_journal SET succeeded=1 WHERE batch_id=? AND target_id=?",
            (batch_id, target_id),
        )
        self._conn.commit()

    def last_undoable_batch(self) -> str | None:
        placeholders = ",".join("?" for _ in REVERSIBLE_OPS)
        row = self._conn.execute(
            f"""SELECT batch_id FROM write_journal
                WHERE succeeded=1 AND undone=0 AND old_value IS NOT NULL
                  AND op_type IN ({placeholders})
                ORDER BY seq DESC LIMIT 1""",
            REVERSIBLE_OPS,
        ).fetchone()
        return row[0] if row else None

    def undoable_rows(self, batch_id) -> list[dict]:
        placeholders = ",".join("?" for _ in REVERSIBLE_OPS)
        rows = self._conn.execute(
            f"""SELECT target_id, op_type, old_value, label_old, label_new
                FROM write_journal
                WHERE batch_id=? AND succeeded=1 AND undone=0 AND old_value IS NOT NULL
                  AND op_type IN ({placeholders})
                ORDER BY seq""",
            (batch_id, *REVERSIBLE_OPS),
        ).fetchall()
        return [
            {"target_id": r[0], "op_type": r[1], "old_value": r[2],
             "label_old": r[3], "label_new": r[4]}
            for r in rows
        ]

    def mark_undone(self, batch_id, target_id=None) -> None:
        if target_id is None:
            self._conn.execute(
                "UPDATE write_journal SET undone=1 WHERE batch_id=?", (batch_id,)
            )
        else:
            self._conn.execute(
                "UPDATE write_journal SET undone=1 WHERE batch_id=? AND target_id=?",
                (batch_id, target_id),
            )
        self._conn.commit()

    def history(self, limit: int = 20) -> list[dict]:
        from collections import Counter
        rows = self._conn.execute(
            """SELECT batch_id, MAX(seq) AS mseq FROM write_journal
               GROUP BY batch_id ORDER BY mseq DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for batch_id, _mseq in rows:
            detail = self._conn.execute(
                """SELECT op_type, label_old, label_new, timestamp, undone
                   FROM write_journal WHERE batch_id=? ORDER BY seq""",
                (batch_id,),
            ).fetchall()
            pairs = Counter((d[0], d[1], d[2]) for d in detail)
            (op_type, old_n, new_n), n = pairs.most_common(1)[0]
            if op_type == "create_category":
                summary = f"＋{new_n} (created) ×{n}"
            else:
                summary = f"{old_n}→{new_n} ×{n}"
            out.append({
                "batch_id": batch_id,
                "when": detail[0][3],
                "count": len(detail),
                "summary": summary,
                "undone": all(d[4] == 1 for d in detail),
            })
        return out
```

- [ ] **Step 4: Run the journal tests to verify they pass**

Run: `pytest tests/test_undo_journal.py -q`
Expected: PASS.

- [ ] **Step 5: Rewire the recat call sites + `undo_last` in `mcp_server.py`**

In `recategorize_transaction` change the record call to the general signature:

```python
journal.record(batch_id, "recategorize", txn.id, old_id, target.id, txn.category, target.name)
```

In `recategorize_matching` change the record call:

```python
journal.record(batch_id, "recategorize", t.id, old_id, target.id, t.category, target.name)
```

Rewrite `undo_last`'s `_run` to read the generalized row keys and dispatch by `op_type` (only `recategorize` for now; the rename branch is added in Task 5). Keep the tool output byte-identical — failures still use the `transaction_id` key, `restored_to` still comes from the old label:

```python
        def _run():
            batch_id = journal.last_undoable_batch()
            if batch_id is None:
                return {"reversed": 0, "message": "nothing to undo"}
            rows = journal.undoable_rows(batch_id)
            reverted = []
            failures = []
            for r in rows:
                if r["op_type"] == "recategorize":
                    ok = svc.recategorize(r["target_id"], r["old_value"])
                else:
                    ok = False
                if ok:
                    journal.mark_undone(batch_id, r["target_id"])
                    reverted.append(r)
                else:
                    failures.append({"transaction_id": r["target_id"],
                                     "reason": "Monarch did not apply the category"})
            restored_names = {r["label_old"] for r in reverted}
            if len(restored_names) == 1:
                restored_to = restored_names.pop()
            elif reverted:
                restored_to = f"{len(reverted)} items"
            else:
                restored_to = ""
            return {"reversed": len(reverted), "failed": len(failures),
                    "failures": failures, "batch_id": batch_id,
                    "restored_to": restored_to}
```

- [ ] **Step 6: Update the one direct-journal read in `test_mcp_tools.py`**

In `test_undo_last_partial_failure_reports_honestly_and_retains_open_row`, change the direct journal read only (leave the `out[...]` tool-output assertions exactly as they are — they must stay green):

```python
    open_rows = journal.undoable_rows(batch_id)
    assert [r["target_id"] for r in open_rows] == ["tx2"]
```

- [ ] **Step 7: Run the full suite to verify no regression**

Run: `pytest -q`
Expected: PASS (all existing recat + undo tool tests green; new journal tests green).

- [ ] **Step 8: Commit**

```bash
git add financial_mcp/cache/undo_journal.py financial_mcp/mcp_server.py tests/test_undo_journal.py tests/test_mcp_tools.py
git commit -m "refactor: generalize undo journal into an op-log (recat behavior byte-identical)"
```

---

### Task 2: `Category.group_id` + pure write helpers

**Files:**
- Modify: `financial_mcp/monarch/models.py:65-69` (add `group_id`)
- Modify: `financial_mcp/writes.py` (add `DEFAULT_CATEGORY_ICON`, `resolve_group`, `check_name_available`)
- Test: `tests/test_writes.py`

**Interfaces:**
- Consumes: `Category` (now with `group_id: str | None`), `WriteError` (existing).
- Produces: `writes.resolve_group(name, categories) -> tuple[str, str]` (`(group_id, group_name)`, fail-closed); `writes.check_name_available(name, categories) -> None` (raises `WriteError` if taken); `writes.DEFAULT_CATEGORY_ICON: str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_writes.py` (reuse or create a small `Category` factory in-test):

```python
from financial_mcp.monarch.models import Category
from financial_mcp import writes
import pytest


def _cats():
    return [
        Category(id="c1", name="Groceries", group="Food & Dining", group_id="g1"),
        Category(id="c2", name="Restaurants", group="Food & Dining", group_id="g1"),
        Category(id="c3", name="Gas", group="Auto & Transport", group_id="g2"),
    ]


def test_resolve_group_hit_is_case_insensitive():
    assert writes.resolve_group("food & dining", _cats()) == ("g1", "Food & Dining")


def test_resolve_group_miss_lists_valid_groups():
    with pytest.raises(writes.WriteError) as exc:
        writes.resolve_group("Nope", _cats())
    assert "Auto & Transport" in str(exc.value)
    assert "Food & Dining" in str(exc.value)


def test_check_name_available_raises_when_taken():
    with pytest.raises(writes.WriteError):
        writes.check_name_available("groceries", _cats())


def test_check_name_available_ok_when_free():
    writes.check_name_available("Vacations", _cats())  # no raise
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_writes.py -q`
Expected: FAIL (`group_id` unknown field / `resolve_group` missing).

- [ ] **Step 3: Add `group_id` to the model**

In `financial_mcp/monarch/models.py`, extend `Category`:

```python
class Category(BaseModel):
    id: str
    name: str
    group: str | None = None
    group_id: str | None = None
    group_type: str | None = None   # Monarch's own: "income" | "expense" | "transfer"
```

- [ ] **Step 4: Add the helpers to `writes.py`**

Append to `financial_mcp/writes.py`:

```python
DEFAULT_CATEGORY_ICON = "🏷️"


def resolve_group(name: str, categories: list[Category]) -> tuple[str, str]:
    """Exact case-insensitive group name -> (group_id, group_name). Fail-closed."""
    by_name = {}
    for c in categories:
        if c.group and c.group_id:
            by_name.setdefault(c.group.lower(), (c.group_id, c.group))
    hit = by_name.get(name.lower())
    if hit:
        return hit
    valid = ", ".join(sorted({c.group for c in categories if c.group}))
    raise WriteError(
        f"No category group named {name!r}. Pick an existing group: {valid}"
    )


def check_name_available(name: str, categories: list[Category]) -> None:
    """Fail-closed: raise if a category by this name already exists (case-insensitive)."""
    if any(c.name.lower() == name.lower() for c in categories):
        raise WriteError(
            f"A category named {name!r} already exists. Pick a different name."
        )
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_writes.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/models.py financial_mcp/writes.py tests/test_writes.py
git commit -m "feat: Category.group_id + resolve_group/check_name_available write helpers"
```

---

### Task 3: Adapter contract + community mutations + csv refusal

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (two abstract methods)
- Modify: `financial_mcp/monarch/queries.py` (add `CREATE_CATEGORY`, `UPDATE_CATEGORY`)
- Modify: `financial_mcp/monarch/community_adapter.py` (imports, `get_categories` maps `group_id`, `create_category`, `rename_category`)
- Modify: `financial_mcp/monarch/csv_adapter.py` (refuse both)
- Test: `tests/test_category_adapter.py` (new)

**Interfaces:**
- Consumes: `_transport`/`_run` bridge, `Category` (with `group_id`).
- Produces: `MonarchAdapter.create_category(name, group_id, icon) -> str`; `MonarchAdapter.rename_category(category_id, new_name) -> bool`; community adapter populates `Category.group_id`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_category_adapter.py` (mirror the pattern in `tests/test_recategorize_adapter.py`, using a stub `transport` callable passed to the community adapter):

```python
import pytest
from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter


def _adapter(responses):
    async def transport(operation, query, variables):
        return responses[operation]
    return CommunityAdapter(transport=transport)


def test_create_category_returns_new_id():
    a = _adapter({"CreateCategory": {"createCategory": {"category": {"id": "new1", "name": "Vacations"}}}})
    assert a.create_category("Vacations", "g1", "🏷️") == "new1"


def test_create_category_raises_when_monarch_returns_no_id():
    a = _adapter({"CreateCategory": {"createCategory": {"category": {}}}})
    with pytest.raises(RuntimeError):
        a.create_category("Vacations", "g1", "🏷️")


def test_rename_category_confirms_applied_name():
    a = _adapter({"UpdateCategory": {"updateCategory": {"category": {"id": "c1", "name": "New Name"}}}})
    assert a.rename_category("c1", "New Name") is True


def test_rename_category_false_when_name_not_applied():
    a = _adapter({"UpdateCategory": {"updateCategory": {"category": {"id": "c1", "name": "Old Name"}}}})
    assert a.rename_category("c1", "New Name") is False


def test_get_categories_populates_group_id():
    a = _adapter({"GetCategories": {"categories": [
        {"id": "c1", "name": "Groceries",
         "group": {"id": "g1", "name": "Food & Dining", "type": "expense"}}]}})
    cats = a.get_categories()
    assert cats[0].group_id == "g1"


def test_csv_adapter_category_writes_refuse():
    c = CsvAdapter.__new__(CsvAdapter)  # no CSV file needed to test the refusals
    with pytest.raises(RuntimeError):
        c.create_category("X", "g1", "🏷️")
    with pytest.raises(RuntimeError):
        c.rename_category("c1", "X")
```

> If `CommunityAdapter.__init__` requires args beyond `transport`, construct it the same way `tests/test_recategorize_adapter.py` already does — copy that setup verbatim.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_category_adapter.py -q`
Expected: FAIL (methods/queries missing; `group_id` not mapped).

- [ ] **Step 3: Add the mutation queries**

Append to `financial_mcp/monarch/queries.py`:

```python
CREATE_CATEGORY = """
mutation CreateCategory($input: CreateCategoryInput!) {
  createCategory(input: $input) {
    category { id name __typename }
    __typename
  }
}
"""

UPDATE_CATEGORY = """
mutation UpdateCategory($input: UpdateCategoryInput!) {
  updateCategory(input: $input) {
    category { id name __typename }
    __typename
  }
}
"""
```

- [ ] **Step 4: Add the abstract methods**

In `financial_mcp/monarch/adapter.py`, after `recategorize_transaction`:

```python
    @abc.abstractmethod
    def create_category(self, name: str, group_id: str, icon: str) -> str: ...

    @abc.abstractmethod
    def rename_category(self, category_id: str, new_name: str) -> bool: ...
```

- [ ] **Step 5: Implement in the community adapter + map `group_id`**

Add `CREATE_CATEGORY, UPDATE_CATEGORY` to the `from financial_mcp.monarch.queries import (...)` block. In `get_categories`, add `group_id` to the `Category(...)` construction:

```python
                group_id=(c.get("group") or {}).get("id"),
```

Add the two methods (next to `recategorize_transaction`):

```python
    def create_category(self, name: str, group_id: str, icon: str) -> str:
        data = self._run(
            self._transport(
                "CreateCategory", CREATE_CATEGORY,
                {"input": {"group": group_id, "name": name, "icon": icon}},
            )
        )
        cat = (data.get("createCategory") or {}).get("category") or {}
        new_id = cat.get("id")
        if not new_id:
            raise RuntimeError(f"Monarch did not create category {name!r}")
        return new_id

    def rename_category(self, category_id: str, new_name: str) -> bool:
        data = self._run(
            self._transport(
                "UpdateCategory", UPDATE_CATEGORY,
                {"input": {"id": category_id, "name": new_name}},
            )
        )
        cat = (data.get("updateCategory") or {}).get("category") or {}
        return cat.get("name") == new_name
```

- [ ] **Step 6: Add the csv refusals**

In `financial_mcp/monarch/csv_adapter.py`, after `recategorize_transaction`:

```python
    def create_category(self, name: str, group_id: str, icon: str) -> str:
        raise RuntimeError("writes are not available in CSV mode")

    def rename_category(self, category_id: str, new_name: str) -> bool:
        raise RuntimeError("writes are not available in CSV mode")
```

- [ ] **Step 7: Run to verify pass + full suite**

Run: `pytest tests/test_category_adapter.py -q && pytest -q`
Expected: PASS (new adapter tests green; full suite green — abstract-method additions covered by csv + community impls).

- [ ] **Step 8: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/queries.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/csv_adapter.py tests/test_category_adapter.py
git commit -m "feat: category create/rename adapter methods + mutations + group_id mapping"
```

---

### Task 4: `create_category` MCP tool

**Files:**
- Modify: `financial_mcp/data_service.py` (add `create_category` passthrough)
- Modify: `financial_mcp/mcp_server.py` (add `create_category` tool)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `svc.categories()`, `writes.resolve_group`, `writes.check_name_available`, `writes.DEFAULT_CATEGORY_ICON`, `journal.record`, `_writes_enabled`, `_guard`.
- Produces: `svc.create_category(name, group_id, icon) -> str`; MCP tool `create_category(name, group)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mcp_tools.py` (follow the existing recat-tool test setup — a stub adapter, `SqliteCache`, `DataService`, `UndoJournal`, `build_server`, `_tool`). Extend the test adapter used for category tests with `create_category`/`rename_category` returning canned values, or add a small dedicated stub:

```python
def test_create_category_off_mode_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="off")
    out = _tool(server, "create_category")(name="Vacations", group="Food & Dining")
    assert "disabled" in out["error"]


def test_create_category_safe_mode_creates_and_journals(tmp_path):
    server, journal = _category_server(tmp_path, write_mode="safe")
    out = _tool(server, "create_category")(name="Vacations", group="Food & Dining")
    assert out["created"] == 1
    assert out["name"] == "Vacations"
    assert out["group"] == "Food & Dining"
    assert journal.history()[0]["summary"] == "＋Vacations (created) ×1"
    assert journal.last_undoable_batch() is None  # create is not auto-undoable


def test_create_category_unknown_group_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="full")
    out = _tool(server, "create_category")(name="Vacations", group="Nope")
    assert "group" in out["error"].lower()


def test_create_category_duplicate_name_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="full")
    out = _tool(server, "create_category")(name="Groceries", group="Food & Dining")
    assert "already exists" in out["error"]
```

Add a `_category_server(tmp_path, write_mode)` helper near the other helpers in the file: a stub adapter whose `get_categories()` returns a fixed list including `Category(id="c1", name="Groceries", group="Food & Dining", group_id="g1")`, whose `create_category(...)` returns `"newcat"`, and whose `rename_category(...)` echoes success; build the server with a real `UndoJournal(str(tmp_path/"j.db"))` and return `(server, journal)`.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_mcp_tools.py -k create_category -q`
Expected: FAIL (tool + `svc.create_category` missing).

- [ ] **Step 3: Add the data_service passthrough**

In `financial_mcp/data_service.py`, after `recategorize`:

```python
    def create_category(self, name: str, group_id: str, icon: str) -> str:
        new_id = self._adapter.create_category(name, group_id, icon)
        self._cache.invalidate("categories")
        return new_id
```

- [ ] **Step 4: Add the `create_category` tool**

In `financial_mcp/mcp_server.py`, after `recategorize_matching` (before `undo_last`):

```python
    @server.tool()
    def create_category(name: str, group: str) -> dict:
        """Create a NEW category inside an existing category group (by name).

        Fail-closed: the group must exist (else refuses with the valid list) and
        the new name must not already exist. Refuses in write_mode 'off'; one-shot
        in 'safe'/'full'. Note: creating a category is recorded in undo_history but
        is NOT auto-undoable this slice (undo would require deleting it) — remove an
        unwanted empty category in Monarch.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}

        def _run():
            cats = svc.categories()
            try:
                group_id, group_name = writes.resolve_group(group, cats)
                writes.check_name_available(name, cats)
            except writes.WriteError as exc:
                return {"error": str(exc)}
            new_id = svc.create_category(name, group_id, writes.DEFAULT_CATEGORY_ICON)
            batch_id = uuid.uuid4().hex
            journal.record(batch_id, "create_category", new_id, None, name, None, name)
            journal.mark_succeeded(batch_id, new_id)
            return {"created": 1, "id": new_id, "name": name, "group": group_name,
                    "note": "creates aren't auto-undoable; delete in Monarch if unwanted"}

        return _guard(_run)
```

- [ ] **Step 5: Run to verify pass + full suite**

Run: `pytest tests/test_mcp_tools.py -k create_category -q && pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/data_service.py financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: create_category tool (journaled, not auto-undoable)"
```

---

### Task 5: `rename_category` tool + `undo_last` rename dispatch

**Files:**
- Modify: `financial_mcp/data_service.py` (add `rename_category` passthrough)
- Modify: `financial_mcp/mcp_server.py` (add `rename_category` tool; add rename branch to `undo_last`; widen `undo_last`/`undo_history` docstrings)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `svc.rename_category`, `writes.resolve_category`, `writes.check_name_available`, `journal`, `_writes_enabled`, `_guard`; the generalized `undoable_rows` from Task 1.
- Produces: `svc.rename_category(category_id, new_name) -> bool`; MCP tool `rename_category(name, new_name)`; `undo_last` reverses `rename_category` ops.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mcp_tools.py`:

```python
def test_rename_category_off_mode_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="off")
    out = _tool(server, "rename_category")(name="Groceries", new_name="Food")
    assert "disabled" in out["error"]


def test_rename_category_safe_mode_renames_and_is_undoable(tmp_path):
    server, journal = _category_server(tmp_path, write_mode="safe")
    out = _tool(server, "rename_category")(name="Groceries", new_name="Food")
    assert out["renamed"] == 1
    assert out["from"] == "Groceries"
    assert out["to"] == "Food"
    assert journal.last_undoable_batch() == out["batch_id"]

    undo = _tool(server, "undo_last")()
    assert undo["reversed"] == 1
    assert undo["restored_to"] == "Groceries"


def test_rename_category_unknown_source_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="full")
    out = _tool(server, "rename_category")(name="Nope", new_name="Food")
    assert "No category named" in out["error"]


def test_rename_category_new_name_taken_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="full")
    # 'Groceries' and 'Restaurants' both exist in the stub; renaming to an existing name refuses.
    out = _tool(server, "rename_category")(name="Groceries", new_name="Restaurants")
    assert "already exists" in out["error"]
```

> The `_category_server` stub's `rename_category(category_id, new_name)` must echo success (return the applied name matches) AND its `svc`/adapter must let `undo_last` call `rename_category` again to restore the old name — a simple stub that records the last applied name and always returns `True` is sufficient for the undo assertion.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_mcp_tools.py -k rename_category -q`
Expected: FAIL (tool + `svc.rename_category` missing; `undo_last` has no rename branch).

- [ ] **Step 3: Add the data_service passthrough**

In `financial_mcp/data_service.py`, after `create_category`:

```python
    def rename_category(self, category_id: str, new_name: str) -> bool:
        applied = self._adapter.rename_category(category_id, new_name)
        # A rename changes the category name that rides on cached transactions.
        self._cache.invalidate("categories")
        self._cache.invalidate("transactions")
        return applied
```

- [ ] **Step 4: Add the `rename_category` tool**

In `financial_mcp/mcp_server.py`, after `create_category`:

```python
    @server.tool()
    def rename_category(name: str, new_name: str) -> dict:
        """Rename an existing category (by name) to a new name.

        Fail-closed: the source category must match exactly (case-insensitive) and
        the new name must not already exist. A rename applies everywhere the category
        is referenced (transactions, budgets, rules) — it is not a local edit — but is
        cleanly reversible via undo_last. Refuses in write_mode 'off'; one-shot otherwise.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}

        def _run():
            cats = svc.categories()
            try:
                src = writes.resolve_category(name, cats)
                writes.check_name_available(new_name, cats)
            except writes.WriteError as exc:
                return {"error": str(exc)}
            batch_id = uuid.uuid4().hex
            journal.record(batch_id, "rename_category", src.id, src.name,
                           new_name, src.name, new_name)
            if svc.rename_category(src.id, new_name):
                journal.mark_succeeded(batch_id, src.id)
                return {"renamed": 1, "from": src.name, "to": new_name,
                        "batch_id": batch_id, "undo_hint": "call undo_last to reverse"}
            return {"renamed": 0, "failed": 1,
                    "failures": [{"category_id": src.id,
                                  "reason": "Monarch did not apply the rename"}]}

        return _guard(_run)
```

- [ ] **Step 5: Add the rename branch to `undo_last`**

In `undo_last`'s `_run`, extend the dispatch loop added in Task 1:

```python
            for r in rows:
                if r["op_type"] == "recategorize":
                    ok = svc.recategorize(r["target_id"], r["old_value"])
                elif r["op_type"] == "rename_category":
                    ok = svc.rename_category(r["target_id"], r["old_value"])
                else:
                    ok = False
                if ok:
                    journal.mark_undone(batch_id, r["target_id"])
                    reverted.append(r)
                else:
                    failures.append({"target_id": r["target_id"],
                                     "reason": "Monarch did not apply the change"})
```

Note: the recategorize partial-failure test asserts `failures == [{"transaction_id": "tx2", ...}]`. To keep that green, branch the failure key by op_type: use `"transaction_id"` for `recategorize`, `"category_id"` for `rename_category`. Implement the failure append as:

```python
                else:
                    key = "transaction_id" if r["op_type"] == "recategorize" else "category_id"
                    failures.append({key: r["target_id"],
                                     "reason": "Monarch did not apply the change"})
```

Also update the `recategorize` branch's existing failure reason string test expectation: the recat partial-failure test asserts the reason `"Monarch did not apply the category"`. Keep the recategorize failure message exactly that; only the rename failure uses the generic wording. Implement:

```python
                else:
                    if r["op_type"] == "recategorize":
                        failures.append({"transaction_id": r["target_id"],
                                         "reason": "Monarch did not apply the category"})
                    else:
                        failures.append({"category_id": r["target_id"],
                                         "reason": "Monarch did not apply the rename"})
```

Widen the `undo_last` and `undo_history` docstrings to say "recategorization or category-rename batch" instead of "recategorization batch".

- [ ] **Step 6: Run to verify pass + full suite**

Run: `pytest tests/test_mcp_tools.py -k rename_category -q && pytest -q`
Expected: PASS — including the untouched recat partial-failure test (`transaction_id` key + "Monarch did not apply the category" reason preserved).

- [ ] **Step 7: Commit**

```bash
git add financial_mcp/data_service.py financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: rename_category tool + undo_last rename dispatch (fully reversible)"
```

---

## Self-Review

**Spec coverage:**
- §1 create + rename tools → Tasks 4, 5. ✓
- §2 generalize journal (chosen approach A) → Task 1. ✓
- §3 schema + one-time migration + `undo_last` dispatch → Task 1. ✓
- §4 rename undoable / create journaled-not-undoable / `undo_last` never silent → Task 1 (`last_undoable_batch` excludes create; `undo_last` returns the batch it reversed), Tasks 4–5. ✓
- §5 components: `writes.py` helpers (Task 2), adapter + community + csv + queries (Task 3), `undo_journal` (Task 1), `mcp_server` (Tasks 1,4,5), `data_service` (Tasks 4,5). ✓
- §6 tool surface (one-shot in safe, return shapes) → Tasks 4, 5. ✓
- §7 refusals (off; group-not-found; name-taken; source-not-found; new-name-taken) → Tasks 4, 5. ✓
- §8 edges: create not auto-undoable (Task 1/4), rename blast radius (data_service invalidates transactions, Task 5; docstring), compensating-write caveat (inherited), live mutation-shape risk (queries flagged). ✓
- §9 testing across `test_undo_journal`, `test_writes`, `test_category_adapter`, `test_mcp_tools` → all tasks. ✓
- §10 enables-next → outcome of Task 1's generalization. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N" — every step carries real code. ✓

**Type consistency:** `record(batch_id, op_type, target_id, old_value, new_value, label_old, label_new)` used identically in Tasks 1, 4, 5. `undoable_rows` returns `{target_id, op_type, old_value, label_old, label_new}` — consumed with those exact keys in `undo_last` (Tasks 1, 5). `resolve_group -> (group_id, group_name)` produced in Task 2, consumed in Task 4. `create_category(name, group_id, icon) -> str` and `rename_category(category_id, new_name) -> bool` consistent across adapter (Task 3), data_service (Tasks 4, 5), and tools. ✓

## Known open item for live deploy

The `CREATE_CATEGORY` / `UPDATE_CATEGORY` mutation shapes (field names, whether create requires `icon`, whether update requires `group`) are proven against live Monarch at deploy, exactly as Slice 1's `updateTransaction` was. If the first live create/rename returns no category or an unapplied name, adjust the `input` maps in `community_adapter.create_category` / `rename_category` — no other layer changes.
