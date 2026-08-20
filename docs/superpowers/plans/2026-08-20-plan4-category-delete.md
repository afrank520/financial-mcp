# Plan 4 Slice 3 — Category Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gated, empty-only `delete_category` MCP tool with a dry-run→confirm handshake and undo-by-recreate, built on the existing generalized undo op-log.

**Architecture:** One new mutating tool behind the existing `write_mode` gate. Delete resolves the category fail-closed, refuses if non-empty or if a pending undo depends on it, requires a content-hash confirm handshake (in BOTH safe and full — it's the destructive op), journals a new `delete_category` op, and calls a new adapter mutation. `undo_last` gains a `delete_category` branch whose compensating write is `create_category` (recreate name/group/icon → new id). No journal schema migration — the delete op reuses the six neutral op-log columns.

**Tech Stack:** Python 3, FastMCP, pydantic models, SQLite (`write_journal`), aiohttp GraphQL transport, pytest.

**Spec:** `docs/specs/2026-08-20-monarch-mcp-plan4-category-delete-design.md`

## Global Constraints

- **Test runner is `./.venv/bin/pytest`** (NOT bare `pytest`/`python`). Baseline before Task 1: 258 passed.
- **Byte-identical fence:** the recategorize AND rename_category tool inputs/outputs/undo semantics must not change. The existing recat/rename regression tests must stay green, unedited. Journal-internal API may extend additively.
- **Fail-closed:** every refusal returns `{"error": ...}` (or a dry-run) and mutates NOTHING — no network call past the point of refusal.
- **Delete requires the handshake in BOTH `safe` and `full`.** This is a deliberate deviation from bulk `recategorize_matching` (which one-shots in `full`): delete is destructive, so `full` does not bypass the preview. Only a matching `confirm_token` executes a delete.
- **`write_journal.new_value` is `NOT NULL`.** The delete op stores the icon there; it must never be null — default to `writes.DEFAULT_CATEGORY_ICON` when the category has no icon.
- **`DELETE_CATEGORY` mutation shape and the category `icon` field are proven-at-deploy** (spec §6.4), like Slice 1's `updateTransaction` and Slice 2's create/rename shapes. Unit tests use canned transport responses; a live smoke test happens before merge.
- **Commit trailer on every commit:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Match existing code style: `from __future__ import annotations`, terse module-level query constants, adapter verify-what-came-back, pure logic in `writes.py`.

---

### Task 1: Category `icon` field + GetCategories icon mapping

**Files:**
- Modify: `financial_mcp/monarch/models.py` (add `icon` to `Category`)
- Modify: `financial_mcp/monarch/queries.py` (`GET_CATEGORIES` fetches `icon`)
- Modify: `financial_mcp/monarch/community_adapter.py:254-265` (`get_categories` maps `icon`)
- Test: `tests/test_category_adapter.py`

**Interfaces:**
- Produces: `Category.icon: str | None = None`; `CommunityAdapter.get_categories()` populates `icon` from the category node. Consumed by Task 6 (delete tool records the icon for undo-recreate).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_category_adapter.py` (mirror the existing single-canned-response transport style used by the create/rename adapter tests in that file):

```python
def test_get_categories_maps_icon():
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    async def transport(operation, query, variables):
        return {"categories": [
            {"id": "c1", "name": "Groceries", "icon": "🛒",
             "group": {"id": "g1", "name": "Food & Dining", "type": "expense"}},
        ]}

    adapter = CommunityAdapter(
        session={"session_id": "s", "csrftoken": "c"},
        service_name="svc", transport=transport,
    )
    cats = adapter.get_categories()
    assert cats[0].icon == "🛒"
    assert cats[0].group_id == "g1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_category_adapter.py::test_get_categories_maps_icon -v`
Expected: FAIL (`Category` has no field `icon`, or the mapping returns None).

- [ ] **Step 3: Write minimal implementation**

In `financial_mcp/monarch/models.py`, add `icon` to `Category` (after `id`/`name`, before `group`):

```python
class Category(BaseModel):
    id: str
    name: str
    icon: str | None = None
    group: str | None = None
    group_id: str | None = None
    group_type: str | None = None   # Monarch's own: "income" | "expense" | "transfer"
```

In `financial_mcp/monarch/queries.py`, add `icon` to `GET_CATEGORIES`:

```python
GET_CATEGORIES = """
  query GetCategories {
    categories {
      id
      name
      icon
      group { id name type __typename }
      __typename
    }
  }
"""
```

In `financial_mcp/monarch/community_adapter.py`, in `get_categories`, add the `icon` mapping to the `Category(...)` construction:

```python
            Category(
                id=c["id"],
                name=c["name"],
                icon=c.get("icon"),
                group=(c.get("group") or {}).get("name"),
                group_id=(c.get("group") or {}).get("id"),
                group_type=(c.get("group") or {}).get("type"),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_category_adapter.py::test_get_categories_maps_icon -v`
Expected: PASS

- [ ] **Step 5: Run the full suite (nothing regressed)**

Run: `./.venv/bin/pytest -q`
Expected: all green (258 + 1).

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/models.py financial_mcp/monarch/queries.py financial_mcp/monarch/community_adapter.py tests/test_category_adapter.py
git commit -m "feat: Category.icon field + GetCategories icon mapping (for delete undo-recreate)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `writes.py` pure helpers — empty count + delete token

**Files:**
- Modify: `financial_mcp/writes.py`
- Test: `tests/test_writes.py`

**Interfaces:**
- Consumes: `financial_mcp.monarch.models.Transaction`.
- Produces:
  - `count_in_category(name: str, transactions: list[Transaction]) -> int` — case-insensitive count of transactions whose `.category` equals `name`.
  - `compute_delete_token(category_id: str, transaction_count: int) -> str` — deterministic 16-char content hash binding the exact delete op. Consumed by Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_writes.py` (import `count_in_category`, `compute_delete_token` from `financial_mcp.writes`; reuse the module's existing `Transaction` construction helper if present, else build inline like other tests in the file):

```python
def _txn(category):
    import datetime
    from decimal import Decimal
    from financial_mcp.monarch.models import Transaction
    return Transaction(id="t", date=datetime.date(2026, 1, 1), merchant="m",
                       category=category, account="a", original_statement="",
                       notes="", amount=Decimal("-1"), tags=[], owner="")


def test_count_in_category_counts_case_insensitively():
    from financial_mcp.writes import count_in_category
    txns = [_txn("Groceries"), _txn("groceries"), _txn("Restaurants")]
    assert count_in_category("GROCERIES", txns) == 2
    assert count_in_category("Restaurants", txns) == 1
    assert count_in_category("Travel", txns) == 0


def test_compute_delete_token_stable_and_count_sensitive():
    from financial_mcp.writes import compute_delete_token
    a = compute_delete_token("c1", 0)
    assert a == compute_delete_token("c1", 0)      # stable
    assert a != compute_delete_token("c1", 1)      # changes when count changes
    assert a != compute_delete_token("c2", 0)      # changes when id changes
    assert len(a) == 16
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_writes.py::test_count_in_category_counts_case_insensitively tests/test_writes.py::test_compute_delete_token_stable_and_count_sensitive -v`
Expected: FAIL (ImportError — functions not defined).

- [ ] **Step 3: Write minimal implementation**

In `financial_mcp/writes.py` (`compute_delete_token` mirrors the existing `compute_confirm_token` hashing style; add near it):

```python
def count_in_category(name: str, transactions: list[Transaction]) -> int:
    """Case-insensitive count of transactions currently in a category (by name)."""
    return sum(1 for t in transactions if t.category.lower() == name.lower())


def compute_delete_token(category_id: str, transaction_count: int) -> str:
    """Deterministic content hash of a delete op. Binds the category id and its
    transaction count so a category that stopped being empty re-previews."""
    key = f"{category_id}:delete:{transaction_count}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_writes.py -v`
Expected: PASS (all writes tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/writes.py tests/test_writes.py
git commit -m "feat: writes.count_in_category + compute_delete_token (delete empty-check + handshake)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Journal — reversible delete op, `pending_dependents`, `new_value` in undoable rows, delete history summary

**Files:**
- Modify: `financial_mcp/cache/undo_journal.py`
- Test: `tests/test_undo_journal.py`

**Interfaces:**
- Consumes: existing `record(batch_id, op_type, target_id, old_value, new_value, label_old, label_new)`.
- Produces:
  - `REVERSIBLE_OPS` now includes `"delete_category"`.
  - `undoable_rows(batch_id)` dicts now include a `"new_value"` key (additive — needed so the delete-recreate can pass the stored icon).
  - `pending_dependents(category_id: str) -> int` — count of un-undone, succeeded, reversible rows that reference this category id as either `target_id` OR `old_value`.
  - `history()` summarizes a `delete_category` batch as `"✕ {name} (deleted) ×{n}"`.

**Note on the dependency semantics (refines spec §2.2):** a category id is "depended on" if any pending undo would touch it — either as the value to restore *into* (recat `old_value`) or as the object to act on (rename/delete `target_id`). The `target_id OR old_value` match covers both; rename's `old_value` is a name (never an id) so it can't false-match.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_undo_journal.py` (follow the file's existing construction — `UndoJournal(str(tmp_path / "j.db"))`):

```python
def test_delete_category_is_reversible_and_carries_new_value(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal, REVERSIBLE_OPS
    assert "delete_category" in REVERSIBLE_OPS
    j = UndoJournal(str(tmp_path / "j.db"))
    # delete op: target_id=deleted id, old_value=group_id, new_value=icon, label_old=name
    j.record("b1", "delete_category", "c1", "g1", "🛒", "Groceries", "Groceries")
    j.mark_succeeded("b1", "c1")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows[0]["op_type"] == "delete_category"
    assert rows[0]["old_value"] == "g1"
    assert rows[0]["new_value"] == "🛒"       # additive key, needed for recreate
    assert rows[0]["label_old"] == "Groceries"


def test_pending_dependents_counts_target_and_old_value_refs(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "j.db"))
    # recat that would restore INTO c1 (old_value=c1)
    j.record("b1", "recategorize", "tx1", "c1", "c2", "Groceries", "Restaurants")
    j.mark_succeeded("b1", "tx1")
    # rename whose target IS c1 (target_id=c1)
    j.record("b2", "rename_category", "c1", "Groceries", "Food", "Groceries", "Food")
    j.mark_succeeded("b2", "c1")
    # unrelated recat
    j.record("b3", "recategorize", "tx2", "c9", "c8", "X", "Y")
    j.mark_succeeded("b3", "tx2")
    assert j.pending_dependents("c1") == 2
    assert j.pending_dependents("c9") == 1
    assert j.pending_dependents("c7") == 0
    # once undone, no longer a dependent
    j.mark_undone("b1")
    assert j.pending_dependents("c1") == 1


def test_history_summarizes_delete(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "j.db"))
    j.record("b1", "delete_category", "c1", "g1", "🛒", "Groceries", "Groceries")
    j.mark_succeeded("b1", "c1")
    assert j.history()[0]["summary"] == "✕ Groceries (deleted) ×1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_undo_journal.py -k "delete or pending_dependents" -v`
Expected: FAIL (`delete_category` not in REVERSIBLE_OPS; `new_value` KeyError; `pending_dependents` undefined; history summary wrong).

- [ ] **Step 3: Write minimal implementation**

In `financial_mcp/cache/undo_journal.py`:

Extend the module constant:
```python
REVERSIBLE_OPS = ("recategorize", "rename_category", "delete_category")
```

Add `"new_value"` to `undoable_rows` — change its SELECT and the returned dicts (additive):
```python
    def undoable_rows(self, batch_id) -> list[dict]:
        placeholders = ",".join("?" for _ in REVERSIBLE_OPS)
        rows = self._conn.execute(
            f"""SELECT target_id, op_type, old_value, new_value, label_old, label_new
                FROM write_journal
                WHERE batch_id=? AND succeeded=1 AND undone=0 AND old_value IS NOT NULL
                  AND op_type IN ({placeholders})
                ORDER BY seq""",
            (batch_id, *REVERSIBLE_OPS),
        ).fetchall()
        return [
            {"target_id": r[0], "op_type": r[1], "old_value": r[2],
             "new_value": r[3], "label_old": r[4], "label_new": r[5]}
            for r in rows
        ]
```

Add `pending_dependents`:
```python
    def pending_dependents(self, category_id: str) -> int:
        """Count of un-undone, succeeded, reversible rows that reference this
        category id as either the object acted on (target_id) or the value to
        restore into (old_value). Deleting such a category would strand those
        undos, so the delete tool refuses when this is > 0."""
        placeholders = ",".join("?" for _ in REVERSIBLE_OPS)
        row = self._conn.execute(
            f"""SELECT COUNT(*) FROM write_journal
                WHERE succeeded=1 AND undone=0
                  AND op_type IN ({placeholders})
                  AND (target_id=? OR old_value=?)""",
            (*REVERSIBLE_OPS, category_id, category_id),
        ).fetchone()
        return row[0] if row else 0
```

Extend `history()`'s summary branch (add the delete case alongside the create case):
```python
            if op_type == "create_category":
                summary = f"＋{new_n} (created) ×{n}"
            elif op_type == "delete_category":
                summary = f"✕ {old_n} (deleted) ×{n}"
            else:
                summary = f"{old_n}→{new_n} ×{n}"
```
(For a delete row `label_old` = the category name, so `old_n` is the name.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_undo_journal.py -v`
Expected: PASS.

- [ ] **Step 5: Fence check — run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: all green. The `new_value` key is additive; if any existing test asserts an *exact* dict equality on `undoable_rows` output, update only that assertion to include `new_value` — but recat/rename TOOL behavior (the fence) must not change.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/cache/undo_journal.py tests/test_undo_journal.py
git commit -m "feat: journal supports reversible delete_category (pending_dependents, new_value in undoable rows, delete history)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Adapter `delete_category` — abstract + community mutation + CSV refusal + test-double stubs

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (abstract method)
- Modify: `financial_mcp/monarch/queries.py` (`DELETE_CATEGORY`)
- Modify: `financial_mcp/monarch/community_adapter.py` (implement + import)
- Modify: `financial_mcp/monarch/csv_adapter.py` (refuse)
- Modify (inert stubs): every `MonarchAdapter` subclass instantiated in tests that lacks `delete_category` — at minimum the doubles in `tests/test_mcp_tools.py`, `tests/test_data_service.py`, and any in `tests/test_category_adapter.py`. Find them: `grep -rn "MonarchAdapter)" tests/`.
- Test: `tests/test_category_adapter.py`

**Interfaces:**
- Produces: `MonarchAdapter.delete_category(self, category_id: str) -> bool` (returns whether Monarch confirms deletion). `CommunityAdapter` implements it; `CsvAdapter` refuses. Consumed by Task 5 (data_service passthrough).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_category_adapter.py`:

```python
def test_delete_category_returns_true_when_monarch_confirms():
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    async def transport(operation, query, variables):
        assert operation == "Web_DeleteCategory"
        assert variables == {"id": "c1"}
        return {"deleteCategory": {"deleted": True, "errors": None}}

    adapter = CommunityAdapter(session={"session_id": "s", "csrftoken": "c"},
                               service_name="svc", transport=transport)
    assert adapter.delete_category("c1") is True


def test_delete_category_returns_false_when_not_deleted():
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    async def transport(operation, query, variables):
        return {"deleteCategory": {"deleted": False,
                                   "errors": [{"message": "not empty"}]}}

    adapter = CommunityAdapter(session={"session_id": "s", "csrftoken": "c"},
                               service_name="svc", transport=transport)
    assert adapter.delete_category("c1") is False


def test_csv_adapter_delete_category_refuses():
    import pytest
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    adapter = CsvAdapter("unused_txns.csv", "unused_balances.csv")
    with pytest.raises(RuntimeError, match="writes are not available in CSV mode"):
        adapter.delete_category("c1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_category_adapter.py -k delete -v`
Expected: FAIL (no `delete_category`); also expect collection errors from abstract-method instantiation once Step 3 adds the abstract method — Step 3 adds the stubs to fix those.

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/monarch/adapter.py` — add the abstract method (next to `rename_category`):
```python
    @abc.abstractmethod
    def delete_category(self, category_id: str) -> bool: ...
```

`financial_mcp/monarch/queries.py` — add (shape proven-at-deploy; `Web_DeleteCategory`, the `$id` var, and the `deleted` field are the live-verify items):
```python
DELETE_CATEGORY = """
mutation Web_DeleteCategory($id: UUID!) {
  deleteCategory(id: $id) {
    deleted
    errors { message code __typename }
    __typename
  }
}
"""
```

`financial_mcp/monarch/community_adapter.py` — import `DELETE_CATEGORY` in the queries import block, and implement (after `rename_category`):
```python
    def delete_category(self, category_id: str) -> bool:
        data = self._run(
            self._transport("Web_DeleteCategory", DELETE_CATEGORY, {"id": category_id})
        )
        res = (data.get("deleteCategory") or {})
        return bool(res.get("deleted"))
```

`financial_mcp/monarch/csv_adapter.py` — add (next to the other write refusals):
```python
    def delete_category(self, category_id: str) -> bool:
        raise RuntimeError("writes are not available in CSV mode")
```

Test-double stubs — for EACH `MonarchAdapter` subclass in tests that is instantiated (grep `tests/` for `MonarchAdapter)`), add an inert method so instantiation still works. In `tests/test_mcp_tools.py` this includes `CategoryCrudAdapter` (give it a REAL recording stub, since Task 6 uses it):
```python
    def delete_category(self, category_id):
        self.deleted = getattr(self, "deleted", [])
        self.deleted.append(category_id)
        return True
```
For doubles that never exercise delete (e.g. `FakeAdapter` in `test_mcp_tools.py`, the doubles in `test_data_service.py`), a `raise NotImplementedError` stub is fine (inert — no call sites):
```python
    def delete_category(self, category_id):
        raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_category_adapter.py -k delete -v`
Expected: PASS.

- [ ] **Step 5: Full suite (stubs fixed all instantiation ripples)**

Run: `./.venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/queries.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/csv_adapter.py tests/
git commit -m "feat: adapter.delete_category + DELETE_CATEGORY mutation + CSV refusal + inert test stubs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: DataService `delete_category` passthrough

**Files:**
- Modify: `financial_mcp/data_service.py`
- Test: `tests/test_data_service.py`

**Interfaces:**
- Consumes: `adapter.delete_category(category_id) -> bool`.
- Produces: `DataService.delete_category(category_id: str) -> bool` — calls the adapter, invalidates the `"categories"` cache, returns the applied flag. Consumed by Task 6.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_service.py` (mirror the existing `create_category`/`rename_category` passthrough tests — use the file's existing stub adapter + cache pattern; the stub needs a `delete_category` that returns True and records the id, and a way to assert the categories cache was invalidated):

```python
def test_delete_category_passthrough_invalidates_categories(tmp_path):
    # Build svc exactly like the sibling create/rename passthrough tests in this file.
    svc, adapter, cache = _make_service(tmp_path)   # reuse the file's existing helper
    assert svc.delete_category("c1") is True
    assert adapter.deleted == ["c1"]
    assert cache.invalidated("categories")          # reuse the file's invalidation assertion
```
(If `test_data_service.py` asserts invalidation differently — e.g. by checking `is_fresh` — follow that file's existing convention rather than the pseudo-helpers above.)

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_data_service.py -k delete_category -v`
Expected: FAIL (`DataService` has no `delete_category`).

- [ ] **Step 3: Write minimal implementation**

In `financial_mcp/data_service.py`, add after `rename_category`:
```python
    def delete_category(self, category_id: str) -> bool:
        applied = self._adapter.delete_category(category_id)
        self._cache.invalidate("categories")
        return applied
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_data_service.py -k delete_category -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/data_service.py tests/test_data_service.py
git commit -m "feat: DataService.delete_category passthrough (invalidates categories cache)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `delete_category` tool + `undo_last` recreate dispatch

**Files:**
- Modify: `financial_mcp/mcp_server.py` (new tool + `undo_last` branch)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `writes.resolve_category`, `writes.count_in_category`, `writes.compute_delete_token`, `writes.DEFAULT_CATEGORY_ICON`, `writes.WriteError`; `journal.pending_dependents`, `journal.record`, `journal.mark_succeeded`, `journal.last_undoable_batch`, `journal.undoable_rows`, `journal.mark_undone`; `svc.categories`, `svc.transactions`, `svc.delete_category`, `svc.create_category`.
- Produces: the `delete_category` MCP tool and a `delete_category` case in `undo_last`'s dispatch loop.

**Behavior contract (from spec §2–§3):**
- `off` → `{"error": "writes are disabled. ..."}`.
- resolve `name` fail-closed (reuse `resolve_category`; on `WriteError` return `{"error": str(exc)}`).
- **empty check:** `count = writes.count_in_category(cat.name, svc.transactions())`; if `count > 0` → `{"error": f"Category '{cat.name}' has {count} transactions — recategorize them first."}`.
- **dependency check:** `deps = journal.pending_dependents(cat.id)`; if `deps > 0` → `{"error": f"{deps} pending undo(s) depend on this category; run undo_last first."}`.
- **handshake (both safe AND full):** `token = writes.compute_delete_token(cat.id, count)`; if `confirm_token is None or confirm_token != token` → return the dry-run preview `{"dry_run": True, "id": cat.id, "name": cat.name, "group": cat.group, "transaction_count": count, "confirm_token": token, "mode": write_mode}`. (Note: unlike bulk recat, `full` does NOT skip this.)
- **execute:** `icon = cat.icon or writes.DEFAULT_CATEGORY_ICON`; `batch_id = uuid.uuid4().hex`; `journal.record(batch_id, "delete_category", cat.id, cat.group_id, icon, cat.name, cat.name)`; if `svc.delete_category(cat.id)` → `journal.mark_succeeded(batch_id, cat.id)` and return `{"deleted": 1, "name": cat.name, "group": cat.group, "batch_id": batch_id, "undo_hint": "call undo_last to reverse"}`; else return `{"deleted": 0, "failed": 1, "failures": [{"category_id": cat.id, "reason": "Monarch did not apply the delete"}]}`.
- Wrap the body in `_guard(_run)` like the other write tools.
- **`undo_last` branch:** add to the dispatch loop, alongside `recategorize`/`rename_category`:
  ```python
                elif r["op_type"] == "delete_category":
                    ok = bool(svc.create_category(r["label_old"], r["old_value"], r["new_value"]))
  ```
  and in the failure-append, an `else`/delete branch keyed `{"category_id": r["target_id"], "reason": "Monarch did not apply the recreate"}`. Keep the recat and rename failure keys byte-identical.

- [ ] **Step 1: Extend the test adapter, then write the failing tests**

In `tests/test_mcp_tools.py`, ensure `CategoryCrudAdapter` has a recording `delete_category` (added in Task 4). Add a non-empty variant and delete tests. `CategoryCrudAdapter.get_categories` returns `c1 Groceries` / `c2 Restaurants` (group `Food & Dining`, group_id `g1`); its `get_transactions` returns `[]` (so both are empty by default). `create_category` returns `"newcat"`.

```python
class DeleteNonEmptyAdapter(CategoryCrudAdapter):
    """Groceries (c1) has one transaction, so delete('Groceries') must refuse."""
    def get_transactions(self):
        return [Transaction(id="tx1", date=datetime.date(2026, 1, 2), merchant="m",
                            category="Groceries", account="a", original_statement="",
                            notes="", amount=Decimal("-1"), tags=[], owner="")]


def _delete_server(tmp_path, adapter=None, write_mode="safe"):
    from financial_mcp.cache.undo_journal import UndoJournal
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter or CategoryCrudAdapter(), cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode=write_mode, batch_cap=200, journal=journal)
    return svc, journal, server


def test_delete_category_off_mode_refuses(tmp_path):
    _, _, server = _delete_server(tmp_path, write_mode="off")
    out = _tool(server, "delete_category")(name="Groceries")
    assert "disabled" in out["error"]


def test_delete_category_dry_run_then_confirm(tmp_path):
    svc, journal, server = _delete_server(tmp_path, write_mode="safe")
    delete = _tool(server, "delete_category")
    preview = delete(name="Groceries")
    assert preview["dry_run"] is True
    assert preview["id"] == "c1"
    assert preview["transaction_count"] == 0
    token = preview["confirm_token"]
    done = delete(name="Groceries", confirm_token=token)
    assert done["deleted"] == 1
    assert svc._adapter.deleted == ["c1"]
    assert journal.last_undoable_batch() == done["batch_id"]


def test_delete_category_full_mode_still_requires_handshake(tmp_path):
    _, _, server = _delete_server(tmp_path, write_mode="full")
    preview = _tool(server, "delete_category")(name="Groceries")
    assert preview["dry_run"] is True   # full does NOT one-shot a delete


def test_delete_category_non_empty_refuses(tmp_path):
    _, _, server = _delete_server(tmp_path, DeleteNonEmptyAdapter(), write_mode="full")
    out = _tool(server, "delete_category")(name="Groceries")
    assert "transactions" in out["error"] and "1" in out["error"]


def test_delete_category_pending_dependency_refuses(tmp_path):
    svc, journal, server = _delete_server(tmp_path, write_mode="full")
    # A pending recat whose undo would restore INTO c1.
    journal.record("bx", "recategorize", "tx1", "c1", "c2", "Groceries", "Restaurants")
    journal.mark_succeeded("bx", "tx1")
    out = _tool(server, "delete_category")(name="Groceries")
    assert "pending undo" in out["error"]


def test_delete_category_stale_token_re_previews(tmp_path):
    _, _, server = _delete_server(tmp_path, write_mode="safe")
    out = _tool(server, "delete_category")(name="Groceries", confirm_token="wrong")
    assert out["dry_run"] is True


def test_delete_category_not_found_refuses(tmp_path):
    _, _, server = _delete_server(tmp_path, write_mode="full")
    out = _tool(server, "delete_category")(name="Nope", confirm_token="x")
    assert "No category named" in out["error"]


def test_delete_then_undo_recreates(tmp_path):
    svc, journal, server = _delete_server(tmp_path, write_mode="safe")
    delete = _tool(server, "delete_category")
    token = delete(name="Groceries")["confirm_token"]
    delete(name="Groceries", confirm_token=token)
    undo = _tool(server, "undo_last")()
    assert undo["reversed"] == 1
    # recreate called with (name, group_id, icon) — icon defaulted since stub has none
    assert svc._adapter.created[-1][0] == "Groceries"
    assert svc._adapter.created[-1][1] == "g1"
    assert journal.last_undoable_batch() is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_mcp_tools.py -k delete_category -v`
Expected: FAIL (`delete_category` tool not registered).

- [ ] **Step 3: Write the tool + undo branch**

Add the `delete_category` tool in `financial_mcp/mcp_server.py` (after `rename_category`, before `undo_last`), following the behavior contract above and the `_guard`/`_writes_enabled` pattern of the sibling tools. Add the `delete_category` dispatch + failure branch to `undo_last`'s loop (see Interfaces). Do NOT alter the recategorize/rename branches or their failure keys.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_mcp_tools.py -k delete_category -v`
Expected: PASS.

- [ ] **Step 5: Extend the mixed-op multi-level undo test to include a delete**

Add a test (or extend the pattern of `test_undo_last_multi_level_dispatch_across_mixed_op_types`) using a `MixedOpsAdapter`-style adapter that drives THREE batches through one journal — a rename, a recat, and a delete — and asserts `undo_last` reverses them newest-first, dispatching each to its correct compensating write (`create_category` for the delete recreate), ending with `{"reversed": 0, "message": "nothing to undo"}`. Assert the delete's undo called `create_category` with the recorded name/group_id.

- [ ] **Step 6: Fence + full suite**

Run: `./.venv/bin/pytest -q`
Expected: all green. Confirm the recat/rename regression tests are unedited and passing (the byte-identical fence). In particular `test_undo_last_multi_level_dispatch_across_mixed_op_types` and the partial-failure fence tests must be unchanged.

- [ ] **Step 7: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: delete_category tool (empty-only, gated handshake, dependency-checked) + undo-by-recreate dispatch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review (author checklist — completed)

**Spec coverage:** §1 tool → Task 6. §2.1 empty-only → Task 2 (`count_in_category`) + Task 6. §2.2 dependency → Task 3 (`pending_dependents`) + Task 6. §2.3 handshake → Task 2 (`compute_delete_token`) + Task 6. §3 undo-recreate → Task 1 (icon) + Task 3 (reversible op + `new_value` in rows) + Task 6 (dispatch). §4 components → Tasks 1,3,4,5,6. §5 refusals → Task 6 tests. §6 caveats: new-id (documented), live-shape (Global Constraints + smoke test before merge). §7 testing → each task's tests.

**Placeholder scan:** none — every step has concrete code or a concrete command. Task 5's test references the file's existing helpers with a fallback instruction because the exact stub/assertion style must match that file.

**Type consistency:** `delete_category(category_id: str) -> bool` consistent across adapter/community/csv/data_service. `undoable_rows` new `new_value` key is threaded from Task 3 into Task 6's recreate. `compute_delete_token(category_id, transaction_count)` signature matches its call site. `Category.icon: str | None` matches the recreate default-icon handling.
