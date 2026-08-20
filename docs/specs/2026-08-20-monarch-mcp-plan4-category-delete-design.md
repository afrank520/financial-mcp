# Monarch MCP — Plan 4 Slice 3: category delete (gated, empty-only, undo-by-recreate) (design)

**Status:** Designed — not yet built.

**Roadmap slot:** Third write slice (Plan 4). Follows the shipped, live-proven
Slice 1 (`2026-08-11-monarch-mcp-plan4-recategorization-design.md`) and Slice 2
(`2026-08-17-monarch-mcp-plan4-category-crud-design.md`). Slice 1 established the
`write_mode` gate, the dry-run → `confirm_token` handshake, `batch_cap`, and the
compensating-write undo journal. Slice 2 generalized that journal into an op-log
that one `undo_last` dispatches over by `op_type`. This slice adds the one category
mutation both prior specs deliberately deferred — **delete** — the destructive op,
kept safe by three deliberate constraints: **empty-only**, a **confirm handshake**,
and a **fail-closed dependency check** against pending undos.

## 1. What this is

One new mutating tool, single-entity and behind the existing safety gate:

- **`delete_category(name, confirm_token=None)`** — delete an existing, **empty**
  category, with a dry-run → confirm handshake and undo-by-recreate.

Delete completes the category CRUD trilogy (create/rename shipped in Slice 2). It is
the destructive op the earlier specs carved out for its own gated slice: Slice 1
§Known-Limitations #2 explicitly flagged that "when category-delete arrives in a
later slice, [undo depending on the old category still existing] becomes a real
edge — flagged now for that slice." This spec addresses that edge head-on (§2.2).

### Out of scope (YAGNI / later slices)
- Deleting **non-empty** categories (with or without a reassignment target),
  merchant edits/merge, transaction splits, tags, notes, hide-from-reports,
  account CRUD. Each is a later slice with its own gate-proven spec.

## 2. The three safety constraints

Delete is the only tool in the suite whose undo is **imperfect** (recreate yields a
new id, §3) and whose action **destroys**. It therefore earns more ceremony than the
one-shot `create`/`rename`. Three constraints, all fail-closed — nothing mutates on
refusal:

### 2.1 Empty-only
The category must contain **zero transactions**. Emptiness is counted by category
**name** against the transactions already in cache (`svc.transactions()`) — **zero
new query surface**, consistent with prior slices. This is safe because
`resolve_category` already refuses an ambiguous name, so any name that resolves is
unique and the name-count is exact. Non-empty → refuse:
`"Category 'X' has N transactions — recategorize them first."`

### 2.2 Pending-undo dependency (the Slice 1 edge)
Recategorizing transactions *out of* category X records `old_value = X` so
`undo_last` can later move them back into X. Deleting X while such a batch is still
un-undone would strand that undo. So: if the resolved category id appears as
`old_value` in **any un-undone reversible batch**, refuse:
`"N pending undo(s) depend on this category; run undo_last first."` A cheap journal
query (`SELECT COUNT(*) ... WHERE old_value = ? AND undone = 0 AND op_type IN
REVERSIBLE_OPS`).

### 2.3 Confirm handshake (content-hash token)
`delete_category` is the **first single-entity tool to use the handshake** — chosen
because it is the destructive op. `write_mode` semantics are unchanged; delete simply
opts single-entity into the handshake that Slice 1 built for bulk.

- **`off`** → refuse immediately (existing gate).
- **`safe`/`full`**, no `confirm_token` → **dry-run preview**, nothing mutates:
  ```json
  {
    "dry_run": true,
    "id": "252...",
    "name": "Old Subscription",
    "group": "Bills & Utilities",
    "transaction_count": 0,
    "confirm_token": "<content-hash>",
    "mode": "safe"
  }
  ```
- Call again **with** the matching token → executes, journals, returns
  `{"deleted": 1, "name": ..., "group": ..., "batch_id": ..., "undo_hint": "call undo_last to reverse"}`.

**Token = content hash**, tamper-evident (same mechanism as bulk recat): the hash
binds `category_id + ":delete:" + transaction_count`. If the category gained a
transaction between preview and confirm, the recomputed hash ≠ the passed token →
**refuse and re-preview**. You can never confirm-delete a category that stopped being
empty. Both §2.1 and §2.2 checks are **re-run at confirm**, not just at dry-run.

## 3. Undo = recreate (new reversible op_type, no migration)

Delete adds `op_type = "delete_category"` to `REVERSIBLE_OPS`. It reuses the existing
generalized op-log's six neutral columns — **no schema migration**:

| column | delete_category |
|---|---|
| `op_type` | `"delete_category"` |
| `target_id` | deleted category id |
| `old_value` | **group_id** (needed to recreate) |
| `new_value` | **icon** (needed to recreate) |
| `label_old` | **category name** (recreate name) |
| `label_new` | category name |

`undo_last` dispatch for a `delete_category` row →
`adapter.create_category(name=label_old, group_id=old_value, icon=new_value)`,
faithfully restoring the category's name, group, and icon. The undoable predicate
(`op_type IN REVERSIBLE_OPS AND old_value IS NOT NULL`) is already satisfied
(`old_value` = group_id, never null).

**Honest caveat (documented, §6):** the recreated category gets a **new Monarch id**.
Undo restores an equivalent category, not the original object — any external
reference to the old id stays broken. This is the existing "undo is a compensating
write, not time-travel" asterisk, made explicit for delete.

To store the icon faithfully, add an `icon: str | None` field to the `Category`
model and populate it in `get_categories` (the category node already carries it).
`create_category` already takes an `icon` argument (Slice 2), so recreate needs no
new adapter capability beyond delete itself.

## 4. Components

- **`financial_mcp/writes.py`** — pure logic, no I/O; unit-testable offline. Adds:
  - `count_in_category(name, transactions) -> int` — name-count for the empty check.
  - `compute_delete_token(category_id, transaction_count) -> str` — the content hash
    (mirrors `compute_confirm_token`).
  - (The pending-undo-dependency check reads the journal, so it lives in the tool /
    a journal method, not in pure `writes.py`.)
- **`financial_mcp/cache/undo_journal.py`** — add `pending_dependents(category_id) ->
  int` (count of un-undone reversible rows with `old_value = category_id`); extend
  `history()` to summarize a delete row (e.g. `"✕ Old Subscription (deleted) ×1"`).
- **`financial_mcp/monarch/adapter.py`** — abstract `delete_category(category_id: str)
  -> bool` (returns whether Monarch confirms deletion — verify-what-came-back).
- **`financial_mcp/monarch/community_adapter.py`** — implements via a new
  `DELETE_CATEGORY` mutation through `_default_transport` + verify; maps the new
  `icon` field in `get_categories`.
- **`financial_mcp/monarch/csv_adapter.py`** — refuses (`RuntimeError("writes are not
  available in CSV mode")`), like its other write refusals.
- **`financial_mcp/monarch/queries.py`** — `DELETE_CATEGORY` mutation constant.
- **`financial_mcp/monarch/models.py`** — add `icon: str | None = None` to `Category`.
- **`financial_mcp/mcp_server.py`** — the `delete_category` tool (gate → resolve →
  empty check → dependency check → handshake → journal → adapter) and the
  `delete_category` branch in `undo_last`'s dispatch.
- **`financial_mcp/data_service.py`** — `delete_category` passthrough; cache
  invalidation of `"categories"` (and undo-recreate invalidates `"categories"` too).

## 5. Refusals (all fail-closed — nothing mutates)

- `write_mode: off` → refuse (existing gate).
- source name not found / ambiguous → `resolve_category`'s existing message.
- category **non-empty** → refuse with the transaction count (§2.1).
- category has **pending-undo dependents** → refuse with the count (§2.2).
- `confirm_token` present but **stale/mismatched** (category no longer empty, or wrong
  token) → refuse and re-preview (§2.3).

## 6. Known limitations / edges

1. **Empty-only.** Deleting a category with transactions is refused; recategorize
   them first. A reassign-on-delete variant is a possible later slice.
2. **Undo recreates with a new id** (§3). Name/group/icon are restored; the object
   identity is not. External references to the old id are not repaired.
3. **Emptiness is counted by name** against cached transactions (§2.1). Safe for the
   only case that reaches the check (a uniquely-resolved name). If the transactions
   cache is stale, the count reflects the last sync — the confirm-time re-check plus
   the content-hash token narrow but do not fully eliminate a race with a
   simultaneously-added transaction in the Monarch UI; Monarch's own delete would
   also then behave per its rules. `undo_history` timestamps let the user judge.
4. **Live mutation-shape risk.** The exact `DELETE_CATEGORY` field shape and success
   signal are proven **live at deploy** (as Slice 1's `updateTransaction` and Slice
   2's `createCategory`/`updateCategory` shapes were). Smoke-test before ship.

## 7. Testing

Extend existing test files, same style:
- `test_writes.py` — `count_in_category` (zero / some / name-unique); `compute_delete_token`
  (stable for same input, changes when count changes).
- `test_undo_journal.py` — `pending_dependents` (counts un-undone reversible rows by
  `old_value`, ignores undone/create rows); delete row recorded → undoable →
  recreate on undo; `history()` summarizes a delete row.
- `test_category_adapter.py` — `delete_category` mutation shape + verify-what-came-back;
  csv adapter refusal.
- `test_mcp_tools.py` — delete in `off`/`safe`/`full`; dry-run preview then confirm;
  all refusals (non-empty, pending-dependency, stale token, not-found); delete→undo
  (recreate) round-trip; mixed-op multi-level undo including a delete batch; **the
  existing recat + rename regression tests stay green** (the fence).

## 8. What this enables next

- Completes category CRUD (create/rename/delete), all gated and — except delete's
  new-id caveat — reversible through one `undo_last`.
- Proves the op-log absorbs a genuinely destructive op with **no migration** and no
  new undo command — exactly the "standing template" payoff Slice 2 set up. The next
  destructive family (merchant merge, or reassign-on-delete) adds an `op_type` and a
  compensating-write case, nothing more.
- Advances Goal #3 (sharing): a shared instance runs `write_mode: off`; the owner runs
  `safe` and gets a previewed, dependency-checked, reversible delete.
