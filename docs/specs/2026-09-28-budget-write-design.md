# Budget Write (`set_budget`) — Design Spec

**Date:** 2026-09-28
**Status:** Approved for planning
**Author:** Adam + Claude (brainstormed)

## Goal

Add a gated, reversible write tool that changes the **planned monthly budget
amount** for a single, already-budgeted category in a given month — behind the
same write-gate → dry-run → confirm → undo safety flow as every other write in
this server. This is the first *budget-writing* "hands" capability, the sibling
of the existing recategorize / rename / rule tools.

It is the first concrete step toward letting the steward/advisory layer propose
budget changes (e.g. "set a $300/mo ski budget") that the user approves and the
MCP applies. v1 is the smallest safe backbone: **set or change a category's
monthly planned amount.**

> **Revised 2026-09-28 (post-live-prove):** the original v1 planned to *refuse*
> unbudgeted categories, keying on an absent budget row. Live evidence proved
> Monarch's `GetJointPlanningData` returns `plannedCashFlowAmount=0.0` (a literal
> float, not null / not an absent row) for an unbudgeted category, so the query
> **cannot** distinguish "unbudgeted" from "budgeted at $0" — the refusal was
> structurally undeliverable. Per owner direction, v1 now **allows** the write on
> a $0/unbudgeted category (matching the origin intent "set a ski budget"), and
> `updateOrCreateBudgetItem` creates the budget. When the current amount reads
> $0 the dry-run carries a `warning` so the user confirms adding a new budget on
> purpose — the confirm-token handshake is the "do you want to proceed?" gate.

## Non-Goals (deferred to later, separately-captured increments)

- **Removing a budget entirely.**
- **Non-Monthly categories** (yearly "target amount" divided into a monthly
  figure; "Expense Frequency: Every 12 months").
- **Flex-budget bucket edits** (Fixed / Non-Monthly / Flexible top-line numbers)
  and switching budget *systems* (Flex vs Category).
- **Per-category toggles** (rollover fund, exclude-from-budget).
- **Goals** (native Monarch `goalsV2`) — read-accessible but out of scope here.

## Background — the captured write

Confirmed live via Chrome DevTools (Copy as cURL) on 2026-09-28, editing the
Phone category's September budget. See memory `budget-write-mutation-captured`.

**Operation:** `Common_UpdateBudgetItem`

```graphql
mutation Common_UpdateBudgetItem($input: UpdateOrCreateBudgetItemMutationInput!) {
  updateOrCreateBudgetItem(input: $input) {
    budgetItem { id plannedCashFlowAmount __typename }
    __typename
  }
}
```

**`input` fields (all confirmed):**

| field | type | meaning |
|---|---|---|
| `categoryId` | string | which category |
| `startDate` | string `YYYY-MM-01` | which month (first of month) |
| `timeframe` | string `"month"` | editing a monthly amount |
| `amount` | number | the new planned dollar figure |
| `applyToFuture` | bool | `false` = this month only; `true` = this + all future months |

**Why this is a clean write:** it is create-or-update in one mutation, and it
**returns the saved `plannedCashFlowAmount`** — so we verify by response, no
re-read needed. `plannedCashFlowAmount` is the *same field* `get_budgets` /
`budget_status` already read (`community_adapter.py` `get_budgets`), so the
current amount for undo is already reachable.

## Global Constraints (inherited by every task)

- **Never print to stdout** in server code (corrupts MCP stdio).
- **Fail-closed** everywhere: ambiguous/unknown category, unbudgeted target,
  or token mismatch → refuse with a clear message; write nothing.
- **Write-gate**: the tool is inert unless `write_mode` ∈ {`safe`, `full`}.
- **Money as `Decimal`** internally (matches the codebase); amounts are sent to
  Monarch as a JSON number and compared back with a tolerance of one cent.
- Match existing patterns exactly (see "Reused as-is" below).
- **Amounts are non-negative** for expense categories in v1; reject negatives
  with a clear message (income-category budgeting is out of v1 scope).

## User-facing behavior

### Invocation
`set_budget(category, amount, month=None, apply_to_future=False, confirm_token=None)`

- **category** — name string (e.g. `"Phone"`, `"Internet & Cable"`). Resolved
  case-insensitively to a Monarch category id via the existing
  `writes.resolve_category`. Ambiguous or unknown → refuse (lists valid names).
- **amount** — the new planned monthly dollar amount (number).
- **month** — optional. If omitted, defaults to the **current month**. Accepts a
  `YYYY-MM` or `YYYY-MM-DD` string (normalized to the first of that month).
- **apply_to_future** — default `False` (this month only). `True` applies to
  this and all future months; must be set explicitly. The dry-run always
  surfaces the scope as a recommendation (see below) so the choice is
  conscious rather than silently defaulted.
- **confirm_token** — omitted for dry-run; the token from a prior dry-run to
  commit.

### Dry-run (no token or mismatched token, in `safe` mode)
Reads the current planned amount for (category, month). Returns:

```json
{
  "dry_run": true,
  "category": "Phone",
  "group": "Bills & Utilities",
  "month": "2026-09",
  "current_amount": 210.00,
  "new_amount": 250.00,
  "apply_to_future": false,
  "scope_prompt": "This will change only 2026-09. Budget changes are often meant to be ongoing — confirm whether to apply to this month only, or to this and all future months.",
  "confirm_token": "<16-hex>",
  "mode": "safe",
  "note": "no budget change made yet"
}
```

**Scope is a conscious choice, never a silent default.** The dry-run always
returns `scope_prompt` — a plain-English line telling the caller which scope
this token commits to and inviting the this-month-vs-ongoing decision. The
default remains this-month-only, but the prompt ensures Claude asks the user
"just this month, or ongoing?" before confirming rather than quietly assuming.
Because the confirm token binds `apply_to_future` (see `compute_budget_token`),
switching scope after seeing the prompt produces a *different* token — the user
cannot accidentally confirm a scope they didn't see previewed.

### Refusals (fail-closed, no token needed to reach these)
- Writes disabled → `{"error": "writes are disabled. ..."}`.
- Unknown/ambiguous category → `WriteError` message (existing resolver text).
- **Unbudgeted target** → `{"error": "No existing budget for 'Phone' in 2026-09
  to change — adding new budgets is coming later."}`.
- Negative amount → `{"error": "amount must be non-negative"}`.

### Confirm (token matches, or `write_mode == "full"`)
Writes via the adapter, then returns:

```json
{
  "updated": true,
  "category": "Phone",
  "month": "2026-09",
  "old_amount": 210.00,
  "new_amount": 250.00,
  "apply_to_future": false,
  "undo_hint": "call undo_last to restore the previous amount"
}
```

If Monarch's returned `plannedCashFlowAmount` does not match the requested
amount (within one cent) → `{"updated": false, "reason": "Monarch did not apply
the change"}` and **no** undo op is recorded (nothing to reverse).

## Undo

- Before writing, capture `old_amount` (the dry-run already read it; the confirm
  path re-reads to avoid a stale token racing a change made elsewhere).
- Record one journal op of `op_type = "set_budget"`.
- **Undo mirrors the original scope.** We persist enough to reverse exactly:
  - `target_id` = `categoryId`
  - `old_value` = JSON `{"amount": <old>, "start_date": "YYYY-MM-01",
    "apply_to_future": <bool>}`
  - `new_value` = JSON of the applied change (same shape, new amount)
  - `label_old` / `label_new` = human strings (e.g. `"Phone 2026-09 $210.00"`).
- `undo_last` dispatches the `"set_budget"` case by calling
  `svc.update_budget_item(categoryId, old_amount, start_date, apply_to_future)`
  — i.e. re-applies the OLD amount at the SAME scope. If the original was
  all-future, undo is all-future; if this-month, undo is this-month. Symmetric.

## Architecture / components

### New

1. **`CommunityAdapter.update_budget_item`** (`monarch/community_adapter.py`)
   ```python
   def update_budget_item(
       self, category_id: str, amount, start_date: str, apply_to_future: bool
   ) -> Decimal | None:
       """Set the planned monthly budget for a category. Returns the saved
       plannedCashFlowAmount (Decimal) on success, or None if Monarch didn't
       apply it. Verify-by-response (the mutation returns the saved amount)."""
   ```
   - New query constant `UPDATE_BUDGET_ITEM` in `monarch/queries.py` holding the
     captured mutation, op name `Common_UpdateBudgetItem`.
   - `timeframe` hardcoded `"month"` in v1.

2. **`DataService.update_budget_item`** (`data_service.py`) — thin passthrough to
   the adapter, mirroring `create_category_rule` etc.

3. **A budget-read accessor for a single (category, month)** — reuse the existing
   `get_budgets(start, end)` path (via `DataService`) and pick the row for the
   category id; return `Decimal | None` (None = unbudgeted). Add a small helper
   `current_budget_amount(category_id, month)` if it keeps the tool clean.

4. **`compute_budget_token`** (`writes.py`)
   ```python
   def compute_budget_token(category_id: str, start_date: str,
                            amount, apply_to_future: bool) -> str:
       key = f"{category_id}|{start_date}|{amount}|{int(apply_to_future)}"
       return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
   ```
   Token binds category+month+amount+scope so it can't be replayed against a
   different change.

5. **`set_budget` MCP tool** (`mcp_server.py`) — orchestrates gate → resolve →
   read current → refuse-if-unbudgeted → dry-run/confirm → write → journal.

6. **`"set_budget"` undo case** in the `undo_last` dispatch (`mcp_server.py`),
   slotted after the `update_rule` branch and before `else: ok = False`.

7. Add `"set_budget"` to `REVERSIBLE_OPS` (`cache/undo_journal.py`).

### Reused as-is
- Write gate `_writes_enabled()`.
- `writes.resolve_category` (fail-closed name → Category).
- The undo journal (`record` / `mark_succeeded` / `undoable_rows` /
  `last_undoable_batch` / `mark_undone`).
- `svc.categories()` for name resolution; `get_budgets` for the current amount.

## Data flow

```
set_budget(category, amount, month?, apply_to_future?, confirm_token?)
  └─ _writes_enabled()                      → refuse if off
  └─ month = month or current-month; normalize → start_date "YYYY-MM-01"
  └─ target = resolve_category(category, svc.categories())   → fail-closed
  └─ current = current_budget_amount(target.id, month)
        └─ None → refuse "unbudgeted"
  └─ token = compute_budget_token(target.id, start_date, amount, apply_to_future)
  └─ if safe and token != confirm_token:    → return dry-run (with token)
  └─ else (token matches OR full):
        saved = svc.update_budget_item(target.id, amount, start_date, apply_to_future)
        └─ saved is None or |saved-amount|>0.01 → {"updated": false, ...} (no journal)
        └─ else: journal.record("set_budget", target.id, old=current+scope,
                                 new=amount+scope, labels...); mark_succeeded
                 → {"updated": true, "old_amount": current, "new_amount": saved, ...}
```

## Error handling

- All refusals return a dict with `error` (or raise `WriteError`, caught and
  surfaced as `{"error": str(exc)}`, matching existing tools).
- A Monarch transport error propagates as the existing tools let it (the
  self-heal-on-expiry retry in the adapter already covers session expiry).
- Mismatch between requested and saved amount → non-fatal `{"updated": false}`,
  **no** undo op (so undo_last never tries to reverse a write that didn't land).

## Testing

**Unit (fake adapter, no network):**
1. Writes disabled → `error`.
2. Unknown category → resolver refusal; ambiguous category → resolver refusal.
3. Unbudgeted (current amount None) → refusal message.
4. Negative amount → refusal.
5. Dry-run returns `dry_run: true` + correct `current_amount`, `new_amount`,
   and a stable `confirm_token`; nothing written.
6. Wrong token in safe mode → still dry-run (no write).
7. Correct token → `updated: true`, adapter called once with the exact input,
   journal op recorded with `op_type="set_budget"` and correct old/new/scope.
8. `full` mode → writes without a token.
9. Monarch returns a mismatched amount → `updated: false`, **no** journal op.
10. Undo (this-month): `undo_last` re-applies old amount at this-month scope.
11. Undo (all-future): original `apply_to_future=true` → undo re-applies old
    amount with `apply_to_future=true`.
12. Month defaulting: omitted month → current month; explicit `YYYY-MM` honored.
13. Scope prompt: the dry-run always includes a non-empty `scope_prompt`, and it
    names the correct scope for both `apply_to_future=false` (this month) and
    `apply_to_future=true` (this + all future months). Flipping the scope
    changes the `confirm_token` (can't confirm an unseen scope).

**Live prove-first (real data, before merge):** change Phone (e.g. $210 → a test
value) this-month-only → confirm → verify in app → `undo_last` → verify Phone
back to $210. Mirror the round-trip discipline used for every prior write.

## Rollout

- Standard feature branch; TDD per task; whole-branch Opus review before merge;
  `--no-ff` merge on Adam's word; live-prove on-branch before merge.
- CHANGELOG `[Unreleased]` entry: "Budget write — set an existing category's
  monthly budget behind the dry-run → confirm → undo safety flow."
- README write-layer bullet updated once shipped.
- No version bump until Adam calls a release.
```
