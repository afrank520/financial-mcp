# Monarch MCP — Plan 4 Slice 1: transaction recategorization (writes + undo) (design)

**Date:** 2026-08-11
**Status:** Shipped + live-proven (2026-08-11). Built via brainstorm→spec→plan→SDD (7 tasks; haiku/sonnet/opus tiers). Final whole-branch review requested one fix (undo honesty on partial compensating-write failure), fixed + re-reviewed clean. Live-verified in Claude Desktop against the real org — 10/10 spec §6 matrix passed, zero unintended mutations (see §6 "Live-verify results").
**Roadmap slot:** First phase to MUTATE real Monarch records (Plan 4). The `write_mode`/`batch_cap` scaffolding in `config.py` has existed but inert — nothing read it. This slice makes writes real, gated, and reversible. Framed as the data-cleanup capability a general Monarch user (not just Adam/TeamFrank) would use. Follows the completed Tier-1 breadth (`2026-08-11-monarch-mcp-tier1-holdings-recurring-design.md`).

---

## 1. Scope

The first write slice: **transaction recategorization**, single and bulk, behind a safety gate, with a local **multi-level undo** net. Recategorization is chosen first because it is the highest-frequency real cleanup chore, the **least destructive** (fully, cleanly reversible — undo just sets the category back), and the reference `updateTransaction` mutation is proven and clean. It exercises the entire write-gate + dry-run + batch-cap + journal machinery end-to-end so later, riskier write families (category CRUD, merchant edits) layer onto a gate already proven safe.

### In scope
- New adapter method `recategorize_transaction(transaction_id, category_id)` on the ABC + CommunityAdapter (calls Monarch's `updateTransaction` mutation) + CsvAdapter (raises "writes not available in CSV mode").
- New pure-logic `writes.py`: the write gate (mode/`confirm_token`/`batch_cap`), fail-closed category name→id resolution, filter→id-set resolution (reusing `spending.search`), dry-run preview assembly.
- New `UndoJournal` on the existing SQLite DB (separate `write_journal` table): records before-state, replays in reverse, multi-level.
- Four MCP tools: `recategorize_transaction`, `recategorize_matching`, `undo_last`, `undo_history`.

### Out of scope (YAGNI / later slices)
- Category CRUD (create/rename/delete), merchant edits/merge, transaction splits, tags, notes, hide-from-reports, account CRUD. Each is a later slice with its own gate-proven spec.
- No cross-record transactionality (Monarch has none). Partial bulk failure is reported honestly, not rolled back.
- No time-travel undo — undo is a **compensating write** (§5).

### Honors the standing constraint
Writes send only the user's explicit intent (a chosen category) to Monarch. Nothing is invented: the target category must resolve to an existing Monarch category id (fail-closed), and the before-state journaled for undo is Monarch's own prior value read at write time.

---

## 2. Architecture

```
MCP write tool ─► WriteGate (mode + confirm_token + batch_cap) ─► name→id resolver ─► UndoJournal.record() ─► MonarchAdapter.recategorize_transaction() ─► Monarch updateTransaction
                                                                                              │
                                                            undo_last / undo_history ◄────────┘  (write_journal table, local SQLite)
```

One-directional layering, same as reads. New pieces:
- **`recategorize_transaction(transaction_id, category_id)`** — ABC abstract; CommunityAdapter implements via the existing generic `_default_transport` (a mutation is just another operation/query/variables POST); CsvAdapter raises `RuntimeError("writes are not available in CSV mode")` (mirrors holdings/recurring).
- **`financial_mcp/writes.py`** — pure logic, no I/O: gate rules, name→id resolution, filter→id-set resolution (reuses `spending.search`), dry-run preview assembly, `confirm_token` computation. Fully unit-testable offline.
- **`UndoJournal`** — wraps the existing SQLite DB with a new `write_journal` table (no `SCHEMA_VERSION` coupling with the read cache — separate table). Records before-state; replays in reverse; drives multi-level undo.
- **MCP tools** — `recategorize_transaction`, `recategorize_matching`, `undo_last`, `undo_history`; all wrapped in the existing `_guard`.

No `SCHEMA_VERSION` bump to the read cache.

---

## 3. The write gate, confirm handshake & modes

**`write_mode`** (config, shipped default `safe`; `off` is the documented recommendation when sharing the server):

- **`off`** — every write tool refuses immediately, before any resolution or network call: `{"error": "writes are disabled. Set write_mode to 'safe' or 'full' to enable."}`. Read-only server behaves exactly as today.
- **`safe`** (everyday mode) — single recategorization executes one-shot (reversible + low-blast; the undo journal removes the need to confirm every single edit); **bulk requires the dry-run → `confirm_token` handshake**; bulk hard-capped at `batch_cap`.
- **`full`** — bulk one-shots too (still capped, still journaled).

**Everything is journaled regardless of mode**, so undo always works.

### The bulk confirm handshake (bulk in `safe`; nothing in `full`)
- `recategorize_matching(...)` **without** `confirm_token` → **dry-run preview**, nothing mutates:
  ```json
  {
    "dry_run": true,
    "would_change": 12,
    "resolved_category": { "id": "...", "name": "Shopping", "group": "..." },
    "sample": [ { "transaction_id": "...", "date": "2026-06-03", "merchant": "Amazon",
                  "amount": "-24.19", "current_category": "Uncategorized" } ],
    "confirm_token": "<content-hash>",
    "mode": "safe"
  }
  ```
  (`sample` = first 20 matched rows, each carrying its **current** category so the caller can catch an over-broad filter.)
- Call again **with** the `confirm_token` → executes, journals the batch, returns `{ "changed": 12, "failed": 0, "batch_id": "...", "undo_hint": "call undo_last to reverse" }`.

### confirm_token = content hash (tamper-evident)
The token is a **deterministic hash of the exact resolved change-set** (sorted transaction ids + target category id), NOT a random opaque stamp. Consequences:
- A token validates ONLY the identical operation it previewed. If the underlying data shifted between preview and confirm (a newly-matching transaction appeared), the recomputed hash ≠ the passed token → **refuse and re-preview**. The agent cannot confirm a stale or different set than what the user saw.
- The token must be echoed back exactly; the model cannot mint a valid one.

### Fail-closed everywhere
- Over-cap: filter matches > `batch_cap` → dry-run **refuses** with the count and "narrow the filter or raise batch_cap." **No truncation** (silent truncation reads as "covered everything" when it didn't).
- Bad category name → refuse with the valid category-name list. Never create-on-the-fly, never fuzzy-guess.
- Ambiguous name (Monarch allows duplicate names) → refuse, showing both with their groups.
- Unknown `transaction_id` → refuse.
- Empty match → `{ "would_change": 0 }`, no token issued, friendly "no transactions matched."

### Category name → id resolution (fail-closed)
Writes accept a **category name**, resolved against `get_categories`:
- Exact case-insensitive match → proceed.
- No match → refuse with valid names.
- Ambiguous → refuse with groups.
- The dry-run echoes the **resolved id + canonical name + group** so the user sees exactly what it locked onto.

### Bulk filter (reuses the proven search filter)
`recategorize_matching` selects transactions with the SAME filter as `search_transactions` — `merchant`, `category`, `min_amount`, `max_amount` — plus an optional date window (`start`/`end`, the proven `YYYY-MM-DD` validation). Zero new query surface. The dry-run resolves the filter to a concrete list and shows it before anything mutates.

---

## 4. Components & data contracts

### 4.1 Adapter
```python
def recategorize_transaction(self, transaction_id: str, category_id: str) -> RecategorizeResult: ...
```
CommunityAdapter calls the `Web_TransactionDrawerUpdateTransaction` mutation through `_default_transport` with input `{"id": transaction_id, "category": category_id}` (confirmed against the reference lib — the category is applied via the `category` input field, NOT `categoryId`). It **verifies the mutation response confirms the new category**; a 200 that did not apply the category is treated as a failed row (§5). Returns per-transaction success + the applied category. CsvAdapter raises `RuntimeError("writes are not available in CSV mode")`.

### 4.2 UndoJournal — `write_journal` table
```
batch_id           TEXT     -- groups one operation (single write = batch of 1)
seq                INTEGER  -- global monotonic order across batches (drives multi-level undo)
transaction_id     TEXT
old_category_id    TEXT     -- before-state: what undo restores
old_category_name  TEXT     -- captured for readable history
new_category_id    TEXT
timestamp          TEXT     -- UTC ISO
undone             INTEGER  -- 0 = live, 1 = already reversed
succeeded          INTEGER  -- 1 = mutation confirmed; 0 = attempted but failed (not offered for undo)
```
- Recorded **before** the mutation fires; each row's `succeeded` reconciled from the mutation result. The before-state (`old_category_*`) is read from the transaction's current category at write time.

### 4.3 Tools (`mcp_server.py`)
- **`recategorize_transaction(transaction_id: str, category: str)`** — single. `safe`/`full`: one-shot. Journals, returns `{changed, batch_id, from, to, undo_hint}`.
- **`recategorize_matching(merchant=None, category=None, min_amount=None, max_amount=None, start=None, end=None, to_category: str, confirm_token: str | None = None)`** — bulk. Dry-run→confirm in `safe`; one-shot in `full`; capped; journaled. `to_category` is the target name (fail-closed resolved).
- **`undo_last()`** — reverses the most recent not-yet-undone batch: for each `succeeded` row, `recategorize_transaction(txn_id, old_category_id)`, mark `undone=1`. Multi-level: calling again steps to the next-most-recent batch. Returns `{reversed, batch_id, restored_summary}`.
- **`undo_history()`** — reversible stack, newest-first: `[{batch_id, when, count, summary "Amazon: Uncategorized→Shopping ×12", undone}]`.
- All wrapped in `_guard`. Writes are not cached; results are direct.

---

## 5. Error handling

- **Auth/Cloudflare** — same `_default_transport`, so `SessionExpiredError`/`CloudflareBlockedError` surface through `_guard` → friendly `financial-mcp-login` message. **Nothing is journaled if auth fails** (journal wraps an actually-attempted mutation).
- **Partial bulk failure** — no cross-record rollback (Monarch has none). Continue, then return an honest mixed result `{changed: 6, failed: 14, failures: [{transaction_id, reason}], batch_id}`. Only successes are journaled as undoable. No silent "success" over a partial write.
- **200-but-no-change** — Monarch may accept a no-op mutation; we verify the response payload applied the target category. If not, that row is reported failed, not changed.
- **Stale confirm_token** — recomputed content-hash ≠ passed token → refuse with a fresh re-preview. Never executes a set different from what was previewed.
- **write_mode = off** — refuse before any resolution or network call.
- **Empty match** — bulk filter matches nothing → `{would_change: 0}` dry-run, no token, friendly message.

### Documented boundaries (honest, not hidden)
1. **Undo is a compensating write, not time-travel.** It re-sets the category to the recorded old id via the same mutation. If the transaction's category was changed (by the user or Monarch's rules) *after* our write, undo still restores the id we recorded — it does not detect intervening edits. `undo_history` timestamps let the user judge.
2. **Undo depends on the old category still existing.** For recategorization this is essentially always true (our tools cannot delete a category yet). When category-delete arrives in a later slice, this becomes a real edge — flagged now for that slice.

---

## 6. Testing (offline/deterministic — no live mutations in the suite)

- **Adapter — recategorize:** fake transport asserts `updateTransaction` called with `{id, category}`; a 200-with-wrong-category response → failed row. CsvAdapter raises the CSV-mode RuntimeError.
- **writes.py (gate):** `off` refuses; `safe` single one-shots; `safe` bulk without token → preview only (asserts no mutation fn invoked); `safe` bulk with valid token → executes; `full` bulk one-shots.
- **confirm_token:** identical change-set → identical hash (stable); changed set (extra id / different target) → different hash → stale token refused and re-previewed.
- **Name resolution (fail-closed):** exact case-insensitive resolves; no match → refuse w/ valid list; ambiguous → refuse w/ groups; never creates.
- **batch_cap:** matches > cap → refuse w/ count, no token; matches == cap → allowed.
- **Preview shape:** count, resolved `{id,name,group}`, sample rows carry `current_category`.
- **Partial failure:** mixed `{changed, failed, failures[]}`; only successes journaled.
- **UndoJournal:** records before-state prior to mutation; `undo_last` reverses newest batch + marks `undone=1`; **multi-level** — second `undo_last` steps to the prior batch; `undo_history` newest-first w/ undone flags; a half-failed batch offers only its `succeeded` rows.
- **Tool layer:** four tools return proper envelopes; `_guard` maps auth errors; dry-run vs execute; `write_mode=off` refusal.
- **Live-verify (interactive, at ship):** in Claude Desktop against the real org — one single recategorization + undo; one small bulk (dry-run → confirm → verify in Monarch UI → `undo_last` → verify restored). Confirms the `updateTransaction` mutation shape and live category-id resolution. Recorded in this spec on ship.

### Live-verify results (2026-08-11, real org, Claude Desktop) — 10/10 PASS, zero unintended mutations
Full §6 matrix passed; every transaction touched returned to its original category; both undo batches confirmed reversed.
- **Single recat + undo:** merchant "Austin Aster" +$2.79 moved Fast Food → Restaurants & Bars (`changed:1`), then `undo_last` restored it to Fast Food (`reversed:1`). Proves the `updateTransaction` mutation shape and live category-id resolution.
- **Bulk dry-run → confirm → undo:** dry-run returned `would_change` + resolved `{id,name,group}` + sample-with-current-category + `confirm_token`; confirm executed (`failed:0`); `undo_last` reversed the batch; `undo_history` showed it `undone`.
- **All four refusals held (nothing mutated):** stale/forged token → re-preview; over-cap → count + no token, no truncation; nonsense category → fail-closed with valid-name list, nothing created; nonexistent merchant → `would_change:0`, no token.

**Live-proven property (unplanned failure injection — the most valuable moment):** a real HTTP 502 hit mid bulk-undo. The system behaved exactly as §5 requires — the failed batch stayed `undone: false` and fully retryable, and the honest `{reversed, failed, failures}` payload plus `undo_history` let the operator see the batch was still open. This is a live, production proof of the final-review fix (undo_last marks only reverted rows; failed rows remain undoable). **Operational habit worth baking into any bulk workflow: check `undo_history` before a blind retry** — a blind second `undo_last` would otherwise step back to the *previous* (already-reversed) batch instead of finishing the open one.

### Usage guidance (from live-verify, not a defect)
- **Prefer `start`/`end` date windows to keep bulk batches small and auditable.** High-frequency repeat merchants (Spuntinos, Dunkin, Amazon) exceed 3–8 rows over their lifetime, so a merchant-only filter is how you'd accidentally approach the `batch_cap` (200) refusal. Narrowing by date window keeps each batch reviewable in the dry-run sample before you confirm. The over-cap guard (§3, fail-closed, no truncation) is the backstop, not the plan.

---

## 7. What this enables next
- Proves the write gate + dry-run + batch-cap + multi-level undo on the safest reversible write, so later slices (category CRUD, merchant cleanup) layer onto proven machinery.
- Delivers the first cleanup capability any Monarch user (not just Adam/TeamFrank) would use — advancing Goal #3 (sharing): a shared instance runs `write_mode: off`; the owner runs `safe`.
- The journal + compensating-undo pattern is the standing template for every future mutating slice.
