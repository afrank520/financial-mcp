# Monarch MCP — Plan 4 Slice 2: category create + rename (writes + generalized undo) (design)

**Status:** Designed — not yet built.

**Roadmap slot:** Second write slice (Plan 4). Follows the shipped, live-proven Slice 1
(`2026-08-11-monarch-mcp-plan4-recategorization-design.md`), which established the
`write_mode` gate, dry-run/confirm handshake, `batch_cap`, and the local compensating-write
undo journal. This slice adds the two safest **category** mutations and, in doing so, makes
good on Slice 1's promise that "the journal + compensating-undo pattern is the standing
template for every future mutating slice" — by **generalizing** that journal from a
recategorization-only log into a general operation log that `undo_last` dispatches over.

## 1. What this is

Two new mutating tools, both single-entity and behind the existing safety gate:

- **`create_category(name, group)`** — create a new category in an existing group.
- **`rename_category(name, new_name)`** — rename an existing category.

Category create + rename are chosen as the next slice because they **complete the
recategorization story** (you can create a target category, then move transactions into it),
they **reuse machinery already proven** (fail-closed name→id resolution, the write gate, the
journal), and they stay on the **reversible ladder** Slice 1 started on: rename is cleanly
reversible (rename back). The one genuinely destructive category operation — **delete** — is
deliberately out of scope; it drags in the undo edge flagged in Slice 1 §Known Limitations
(undoing a delete, or deleting a now-non-empty category) and earns its own gated slice.

### Out of scope (YAGNI / later slices)
- Category **delete**, merchant edits/merge, transaction splits, tags, notes,
  hide-from-reports, account CRUD. Each is a later slice with its own gate-proven spec.

## 2. Why generalize the journal now

Slice 1's `write_journal` table is recategorization-specific: its columns are
`transaction_id`, `old/new_category_id`, `old/new_category_name`, and `undo_last` hardcodes
"for each row, call `recategorize(txn, old_category_id)`." A category rename's undo is a
*different* compensating write (rename back to the old name). Three ways to absorb that were
considered:

- **A. Generalize to one op-log (CHOSEN).** Migrate `write_journal` into a general operation
  log; `undo_last` dispatches the correct compensating write per `op_type`. Cleanest, single
  global undo order, one `undo_last` for the user. Cost: touches proven code — mitigated by
  the existing 234-test suite, which has genuinely behavioral coverage of the recat/undo path
  (mode gates, partial-failure honesty, multi-level undo, retry-after-partial-failure).
- **B. Parallel category journal + cross-table dispatcher.** Zero risk to recat undo, but two
  tables plus cross-table ordering logic — a complexity tax paid again on every future slice.
- **C. Separate `undo_last_category` tool.** Simplest, zero recat risk, but N undo commands
  accumulate and there is no single "undo my last thing." Worst UX; breaks the template
  promise.

**A is chosen** because the "journal is the standing template" promise is only real if the
journal is general, and generalizing is cheapest now, while only two operation families exist.
The migration risk is fenced by the existing behavioral test suite.

## 3. Generalized journal schema + migration

The `write_journal` table's category-specific columns become neutral. Generic spine
(`seq`, `batch_id`, `timestamp`, `undone`, `succeeded`) is unchanged.

| column | recategorize | rename_category | create_category |
|---|---|---|---|
| `op_type` | `"recategorize"` | `"rename_category"` | `"create_category"` |
| `target_id` | transaction id | category id | new category id |
| `old_value` | old category **id** | old **name** | *NULL* |
| `new_value` | new category **id** | new **name** | new **name** |
| `label_old` | old category name | old name | *NULL* |
| `label_new` | new category name | new name | created name |

**One-time migration** in `UndoJournal.__init__`: if the existing table has the old
recat-specific columns, migrate every row into the generalized shape
(`op_type='recategorize'`, `target_id←transaction_id`, `old_value←old_category_id`,
`new_value←new_category_id`, `label_old←old_category_name`, `label_new←new_category_name`),
so live undo history survives. The journal is not under `SCHEMA_VERSION`, so this migration is
hand-written (create new table → copy mapped rows → swap), not a cache rebuild.

**`undo_last` becomes a dispatcher.** It finds the most-recent undoable batch (existing logic:
`succeeded=1 AND undone=0 AND` the op is reversible), then reverses each row **by `op_type`**:
- `recategorize` → `adapter.recategorize_transaction(target_id, old_value)` — unchanged behavior.
- `rename_category` → `adapter.rename_category(target_id, old_value)` — rename back to old name.
- `create_category` → **not reversible this slice** (see §4); such rows are never selected by
  the undoable query.

The recat tool's inputs, outputs, and undo semantics stay **byte-identical** — only the
journal's internal column names and `record(...)` signature change. `record` becomes
`record(batch_id, op_type, target_id, old_value, new_value, label_old, label_new)`; the recat
call sites pass the remapped values.

## 4. Undo semantics per op (and the one honest asterisk)

- **Rename is fully undoable.** Undo renames the category back to `old_value`. No new adapter
  capability required.
- **Create is journaled for the audit trail but NOT auto-undoable this slice.** Undoing a
  create means *deleting* a category, and delete is out of scope (the risky op with the
  flagged edge: transactions may already have been moved into the new category). A create
  therefore appears in `undo_history` but is never selected by `undo_last`.
- **`undo_last` is never silent.** It returns the summary of the batch it actually reversed,
  so if the user's most recent action was a create, they see `undo_last` reversed the earlier
  *rename* and can consult `undo_history`. This mirrors how `undo_last` already skips recat
  rows with no recorded old id. To remove an unwanted new (empty) category, the user deletes
  it in Monarch, or waits for the delete slice.

## 5. Components

- **`financial_mcp/writes.py`** — pure logic, no I/O; unit-testable offline. Adds:
  - `resolve_group(name, categories) -> (group_id, group_name)` — builds the group name→id
    map from the categories list (no new query: `GetCategories` already returns
    `group { id name type }`), fail-closed with the valid-group list on miss/ambiguity,
    mirroring `resolve_category`.
  - `check_name_available(name, categories)` — raises `WriteError` if a category by that name
    already exists (no silent duplicates). Case-insensitive, matching `resolve_category`.
- **`financial_mcp/monarch/adapter.py`** — two new abstract methods:
  - `create_category(self, name: str, group_id: str, icon: str) -> str` — returns new category id.
  - `rename_category(self, category_id: str, new_name: str) -> bool` — returns whether the
    applied name matches (verify-what-came-back, like `recategorize_transaction`).
- **`financial_mcp/monarch/community_adapter.py`** — implements both via new mutations
  (`CreateCategory`, `UpdateCategory`), using the existing `_transport`/`_run` + verify pattern.
- **`financial_mcp/monarch/csv_adapter.py`** — refuses both (raises), like its
  `recategorize_transaction` refusal; the CSV source is read-only.
- **`financial_mcp/monarch/queries.py`** — `CREATE_CATEGORY`, `UPDATE_CATEGORY` mutation
  constants.
- **`financial_mcp/cache/undo_journal.py`** — generalized schema + migration + generalized
  `record`; `undoable_rows`/`history` return `op_type` so `undo_last` can dispatch.
- **`financial_mcp/mcp_server.py`** — two new tools + `undo_last` dispatch on `op_type`.
- **`financial_mcp/data_service.py`** — thin passthroughs `create_category` /
  `rename_category`, matching the existing `recategorize` passthrough (`svc.recategorize`,
  which the recat tools call rather than hitting the adapter directly).

## 6. Tool surface

- **`create_category(name: str, group: str) -> dict`** — resolves `group` (fail-closed),
  checks `name` free, creates, journals (`op_type='create_category'`), refreshes the
  categories cache, returns `{created: 1, id, name, group}`. One-shot in `safe` (single
  entity; the dry-run/confirm handshake is for bulk). Refuses in `off`.
- **`rename_category(name: str, new_name: str) -> dict`** — resolves source via
  `resolve_category`, checks `new_name` free, renames, journals
  (`op_type='rename_category'`, old/new), refreshes cache, returns
  `{renamed: 1, from, to, batch_id, undo_hint}`. One-shot in `safe`. Refuses in `off`.

## 7. Refusals (all fail-closed — nothing mutates)

- `write_mode: off` → refuse (existing gate).
- **create**: group not found/ambiguous → refuse with the valid-group list; `name` already
  exists → refuse (no duplicate).
- **rename**: source name not found/ambiguous → `resolve_category`'s existing message;
  `new_name` collides with an existing category → refuse.

## 8. Known limitations / edges

1. **Create is not auto-undoable** (§4). Recorded in history; `undo_last` skips it. Removing
   an unwanted category is a Monarch-side action or a future delete slice.
2. **Rename has a wide blast radius.** Renaming a category renames it everywhere it is
   referenced — every transaction, budget line, and Monarch rule. This is cleanly reversible
   (rename back), but the user should understand a rename is not a local edit. The tool
   response names the change plainly; `undo_hint` points at `undo_last`.
3. **Undo is a compensating write, not time-travel** (inherited from Slice 1). Rename-undo
   restores the recorded old name; it does not detect an intervening rename by the user or by
   Monarch. `undo_history` timestamps let the user judge.
4. **Live mutation-shape risk.** The exact `CreateCategory`/`UpdateCategory` field shapes and
   whether create requires an `icon`/emoji are proven **live at deploy** (as Slice 1's
   `updateTransaction` shape was). A neutral default icon is sent, overridable. This is the
   one item likely to need a tweak after first live run.

## 9. Testing

Extend existing test files, same style:
- `test_undo_journal.py` — migration (old-shape recat rows survive and remain undoable);
  rename record→succeeded→undoable; create recorded-but-not-undoable; multi-level dispatch
  across mixed op types.
- `test_writes.py` — `resolve_group` (hit / miss / ambiguous); `check_name_available`
  (free / taken / case-insensitive).
- `test_recategorize_adapter.py` (or a sibling `test_category_adapter.py`) — `create_category`
  and `rename_category` mutation shape + verify-what-came-back; csv adapter refusal for both.
- `test_mcp_tools.py` — create/rename in `off`/`safe`/`full`; all refusals; `undo_last`
  reverses a rename; **the existing recat + undo regression tests stay green** (the migration
  fence).

## 10. What this enables next

- Proves the **generalized op-log + dispatching `undo_last`** — the real form of Slice 1's
  "standing template." Every later mutating slice (merchant cleanup, splits, delete) adds an
  `op_type` and a compensating-write case, not a new journal or a new undo command.
- Completes the recategorization loop: create a target category, then recategorize into it,
  all reversible.
- Delivers cleanup any Monarch user would use (Goal #3, sharing): a shared instance runs
  `write_mode: off`; the owner runs `safe`.
