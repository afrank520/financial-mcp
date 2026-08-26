# Monarch MCP — Plan 4 rule-update: gated in-place edit of simple merchant-name rules (design)

**Status:** DESIGN — not yet built. Architectural slice brainstormed 2026-08-26 on the back of
a completed read/write-shape spike (§2). This is the **last piece of "bucket A"** (write-CRUD
polish for transaction rules), following the shipped-and-live-proven `create_merchant_rule`
(2026-08-23) and `create_category_rule` (2026-08-25). Follows the proven write-gate /
dry-run→confirm / op-log / `undo_last` template established by every prior Plan 4 slice. Adds
ONE gated mutating tool. No journal schema migration.

**Roadmap slot:** Completes rule CRUD (create ✅, delete ✅ via `delete_transaction_rule`, now
update). Precedes the read-only `rule_review` advisory (Slice 2) and rule reorder (Slice 3),
then the `financial_context` resource and the HELOC glide-path.

---

## 1. What this is

The user has 61 transaction rules and wants Claude to help **clean them up**: spot inefficient
or counterproductive rules and *correct* them. The four bad-rule types map across three slices:

| Bad-rule type | Handled by |
|---|---|
| **Wrong / messy target** (rule sets the wrong category or renames to the wrong merchant) | **this tool** |
| **Bad match text** (the `contains`/`eq` phrase is wrong, too broad, or too narrow) | **this tool** |
| **Redundant / duplicate** | existing `delete_transaction_rule` (via `undo`-style delete) + Slice 2 detection |
| **Order / conflict** | Slice 3 (reorder) — deferred, needs its own DevTools capture |

This slice builds exactly the correction primitive, and only that:

- **`update_transaction_rule` (gated write)** — edits an existing rule *in place* (preserving
  its id and its top-to-bottom order), changing any of: the match text, the category target,
  the merchant-rename target. Behind the write-mode gate, with a dry-run→confirm handshake,
  a no-op guard, fail-closed refusal of any rule too complex to safely round-trip, and full
  undo (undo = a compensating edit back to the old values).

### Design decision that shapes everything: FAIL-CLOSED to SIMPLE rules (§2, §5)

Monarch's update mutation is a **full replacement**, not a partial patch: the client re-sends
the rule's *entire* state and only the changed field differs (confirmed via DevTools capture,
2026-08-25). So a naive editor that only knows about merchant-criteria + set-actions would
**silently wipe** any other component a rule carries (an amount criterion, tags, a linked goal,
account scoping). The spike measured the real risk on the live account (§2): **27 of 61 rules
carry something we don't model** — 26 don't match on merchant text at all.

Therefore this tool refuses to touch any rule it cannot fully and faithfully reconstruct. A
**simple rule** — the only kind it edits — is one whose *only* criterion is a merchant-name
criterion and whose *only* actions are set-category and/or set-merchant, with **every other
criterion and action empty**. On the live account that is **34 of 61 rules** (14 category-only,
9 merchant-only, 11 both) — and it is exactly the population the cleanup goals apply to, since
those are the rules `create_merchant_rule` / `create_category_rule` already mint. Any
non-simple rule is refused with a message naming the offending component and pointing to the
Monarch UI. Fail-closed; never a silent wipe.

### Design decision: edits apply to FUTURE transactions only

`applyToExistingTransactions` is always sent `false` (matching the DevTools capture and every
prior write slice's future-only philosophy). Editing a rule's category does **not**
retroactively recategorize past transactions; the dry-run message says so plainly. This keeps
undo honest (a full-replacement edit has no per-transaction side effects to unwind) and
preserves the clean division of labor: past fixes are the job of `recategorize` /
`rename_merchant_matching`, both already reversible per-row.

### Out of scope (deferred)
- **Adding or removing an action** (turning a category-only rule into a category+merchant rule,
  or dropping an action entirely) — changes the rule's nature and is rarer than fixing a wrong
  value. YAGNI; a later slice if wanted.
- **Changing the match operator** (`contains` ↔ `eq`, or to `starts_with`/`exactly`) — the tool
  **preserves** the rule's existing operator and only changes the *value*. Operator changes are
  deferred.
- **Editing non-simple rules** (amount/statement/category-id/account-scoped, tagged, goal-linked,
  or notify/review-status rules) — fail-closed refused (§5). Editing them is a UI job.
- **Cross-rule duplicate detection** ("this edit makes rule A identical to rule B") — that is
  Slice 2 `rule_review`'s job, not this tool's. This tool only guards against a **no-op** edit.

## 2. Spike findings (the confirmed API facts this design rests on)

Schema introspection is disabled for non-admin users. The update write-shape was captured
**live** on 2026-08-25 (Adam edited a throwaway `ZZUPDATEPROBE` rule in the Monarch web app
with DevTools recording, reading only the request **Payload** — never "Copy as cURL", which
would leak session cookies; the probe rule was then deleted). The read-shape and the
simple/complex distribution were confirmed by a **read-only** GraphQL spike on 2026-08-26
(throwaway probe scripts, since deleted; touched only merchant text + category names, never
balances/amounts).

| Operation | Mutation (operationName) | Shape | Status |
|---|---|---|---|
| **Read** | `Web_GetTransactionRules` → `GET_TRANSACTION_RULES` | `transactionRules { id order merchantCriteria{…} … set*Action{…} }` — **extended** this slice (§4.1) | ✅ live |
| **Update** | `Common_UpdateTransactionRuleMutationV2` | `updateTransactionRuleV2(input: UpdateTransactionRuleInput!) { errors }` | ✅ captured live |

**Update input = the create input, field-for-field, PLUS `id`.** That is the only structural
difference from `CreateTransactionRuleInput`. Returns **only `{ errors }`** — no rule id, no
trustable success bool. **Verify by re-read** (does the rule now reflect the intended values?).
GOTCHA still holds: never trust a Monarch rule/V2 mutation's returned boolean.

**Five facts that drive the design:**

1. **Full replacement, not a patch.** The observed payload re-sent the entire rule state with
   only the changed field differing. So the update builder must construct the *complete* desired
   end-state → the tool must read the rule's current fields first, apply the delta, and send the
   whole input. Sending just the changed field would blank everything else.

2. **Write field ≠ read field.** The write input uses `merchantNameCriteria`; the read query
   returns the criterion under `merchantCriteria`. The tool reads operator/value from
   `merchantCriteria` and writes them into `merchantNameCriteria`.

3. **`setMerchantAction` is a bare NAME string on write, an object on read.** The create builder
   (`_rename_rule_input`) sends `setMerchantAction: "<name>"` (proven live for create). The read
   returns `setMerchantAction: { id, name }`. So to re-send an unchanged rename target the update
   builder sends the **name** string (from the read's `setMerchantAction.name`); to change it, the
   caller's `set_merchant` name. **Residual live unknown** (flagged, tested first in live-prove,
   §9): that re-sending an unchanged rename target *by name* preserves it without repointing to a
   different merchant id. The verify-by-re-read (§4.2) turns any surprise into an honest failure,
   never a silent wrong write.

4. **`setCategoryAction` is a bare category-id string on write** (proven live for
   `create_category_rule`). To re-send an unchanged category the update builder sends the
   existing category **id** (now surfaced by the extended read, §4.1); to change it, the id from
   `resolve_category(set_category)`.

5. **Simple/complex is fully detectable.** Every field a rule could carry is readable
   (confirmed): criteria `merchantCriteria`, `merchantCriteriaUseOriginalStatement`,
   `categoryIds`, `accountIds`, `amountCriteria`, `originalStatementCriteria`; actions
   `setMerchantAction`, `setCategoryAction`, `reviewStatusAction`, `sendNotificationAction`,
   `unassignNeedsReviewByUserAction`, `addTagsAction`, `linkGoalAction`, `linkSavingsGoalAction`
   (`splitTransactionsAction` does not exist on read). So the simple-rule guard can be exact —
   it reads all of them and refuses if anything beyond a merchant criterion + set-category/merchant
   is populated. Measured on the live account: 34 simple / 27 complex.

## 3. Architecture

Additive, follows the established seams. No journal schema migration.

### 3.1 GraphQL constants (`financial_mcp/monarch/queries.py`)

Add the update mutation beside the existing create/delete constants:

```python
UPDATE_TRANSACTION_RULE = """
  mutation Common_UpdateTransactionRuleMutationV2($input: UpdateTransactionRuleInput!) {
    updateTransactionRuleV2(input: $input) {
      errors { message code __typename }
      __typename
    }
  }
"""
```

**Extend `GET_TRANSACTION_RULES`** to select the fields needed for (a) reconstructing the full
input for unchanged components and (b) the simple-rule guard. Add to the existing selection:
`order`, `merchantCriteriaUseOriginalStatement`, `categoryIds`, `accountIds`,
`amountCriteria { operator }`, `originalStatementCriteria { operator }`,
`reviewStatusAction`, `sendNotificationAction`, `unassignNeedsReviewByUserAction`,
`addTagsAction { id }`, `linkGoalAction { id }`, `linkSavingsGoalAction { id }`, and the
**action ids** on `setMerchantAction { id name }` / `setCategoryAction { id name }` (the shipped
query already selects these two objects; ensure `id` is present). All fields were proven
selectable by the spike, so the extension carries no 400 risk. The extension is additive — the
create-rule id-set diff and both duplicate guards read only `id`/`value`/`set_merchant`/
`set_category` and are unaffected.

### 3.2 `writes.py` helpers (pure, offline-testable)

- **`_update_rule_input(rule_id, operator, value, set_category_id, set_merchant) -> dict`** —
  the create input + `id`. Mirrors `_rename_rule_input` / `_category_rule_input` but:
  - takes `operator` as a parameter (does **not** hardcode `"contains"`), preserving the rule's
    existing operator;
  - populates *both* `setCategoryAction` (id string or `None`) and `setMerchantAction` (name
    string or `None`) from the caller's resolved end-state;
  - sends `applyToExistingTransactions: False`, `merchantCriteriaUseOriginalStatement: False`,
    and every other criterion/action explicit-`null` — correct because the rule is verified
    simple (those fields were already null).
- **`compute_rule_update_token(rule_id, operator, value, set_category_id, set_merchant) -> str`**
  — sha256 hexdigest[:16] over a key in a **fresh namespace** that can't collide with the create
  tokens, e.g. `f"{rule_id}#upd#{operator}:{value}=>cat:{set_category_id}|mer:{set_merchant}"`.
  Binds the exact end-state so a stale preview can't be replayed against a different edit.
- **`is_simple_rule(rule: dict) -> bool`** (and a companion that names the offending component
  for the refusal message) — `True` iff `merchantCriteria` is present/non-empty AND at least one
  of `set_category`/`set_merchant` is set AND all of `categoryIds`, `accountIds`, `amountCriteria`,
  `originalStatementCriteria`, `addTagsAction`, `linkGoalAction`, `linkSavingsGoalAction` are
  empty/absent AND `merchantCriteriaUseOriginalStatement` is false AND `reviewStatusAction` is
  null AND `sendNotificationAction`/`unassignNeedsReviewByUserAction` are false.

### 3.3 Adapter (mirror the create/delete pattern across all three adapters)

- `adapter.py` (ABC) — add one abstract method:
  `update_transaction_rule(self, rule_id: str, operator: str, value: str, set_category_id: str | None, set_merchant: str | None) -> bool`
  — `True` only when a post-write re-read confirms the rule reflects the intended end-state.
- `community_adapter.py`:
  ```python
  def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant) -> bool:
      payload = self._run(self._transport(
          "Common_UpdateTransactionRuleMutationV2", UPDATE_TRANSACTION_RULE,
          {"input": _update_rule_input(rule_id, operator, value, set_category_id, set_merchant)}))
      errors = ((payload.get("updateTransactionRuleV2") or {}).get("errors")) or []
      if errors:
          return False
      rule = next((r for r in self.get_transaction_rules() if r["id"] == rule_id), None)
      if rule is None:
          return False
      return (rule.get("operator") == operator
              and rule.get("value") == value
              and (rule.get("set_category_id") or None) == (set_category_id or None)
              and (rule.get("set_merchant") or None) == (set_merchant or None))
  ```
  The extended parse (§3.1) must surface `set_category_id`, `set_merchant` (name), `order`, and
  the complexity fields alongside the existing keys, without dropping any current key.
- `csv_adapter.py` — `update_transaction_rule` **raises** `RuntimeError("writes are not available
  in CSV mode")`, exactly like the sibling write stubs.

### 3.4 DataService (`data_service.py`)
Thin pass-through mirroring the existing rule methods:
- `update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant) -> bool`
  → adapter passthrough. `transaction_rules()` already exists (uncached passthrough).

### 3.5 Op-log / undo (`cache/undo_journal.py` — NO schema change)
- Add `"update_rule"` to `REVERSIBLE_OPS`
  (currently `("recategorize", "rename_category", "delete_category", "rename_merchant",
  "create_rule", "create_category_rule")`). That single change makes `last_undoable_batch`,
  `undoable_rows`, and dependency logic recognize it.
- **Journaled row:** `target_id = rule_id`; `old_value` / `new_value` each hold the *complete
  simple-rule state* as a small JSON string — `{operator, value, set_category_id, set_merchant}`
  — captured pre-edit and post-edit respectively; `label_old` / `label_new` hold human-readable
  before/after summaries for `history`. TEXT columns hold these happily.
- `history()`: add a branch so `op_type == "update_rule"` renders a readable line, e.g.
  `f"Rule edit: {label_old} → {label_new}"`, parallel to the existing branches.

### 3.6 MCP tool (`mcp_server.py`)
`update_transaction_rule(rule_id, match_text=None, set_category=None, set_merchant=None, confirm_token=None) -> dict`
— placed beside `create_merchant_rule` / `create_category_rule`, wrapped in the standard
`_guard(_run)`:

1. **Gate:** `write_mode == "off"` → refuse with the standard message.
2. **Load:** find `rule_id` in `svc.transaction_rules()`; not found → refuse
   (`"no rule with id {rule_id}"`).
3. **Simple guard (fail-closed):** `is_simple_rule(rule)` false → refuse, naming the offending
   component (e.g. `"this rule also matches on amount / sets tags — edit it in Monarch → Rules"`).
4. **Compute end-state** (delta over the current values):
   - `operator` = the rule's existing operator (preserved);
   - `value` = `match_text` if provided (non-blank) else the rule's current value;
   - `set_category_id` = `resolve_category(set_category).id` (fail-closed) if `set_category`
     provided else the rule's current `set_category_id`;
   - `set_merchant` = `set_merchant` if provided else the rule's current `set_merchant` name.
5. **No-op guard:** if the end-state equals the current state → refuse
   (`"that edit changes nothing"`).
6. **Token:** `token = compute_rule_update_token(rule_id, operator, value, set_category_id, set_merchant)`.
7. **Dry-run vs execute** (same control flow as the create tools):
   - `safe` mode with no/invalid token → **preview** (change nothing):
     ```json
     {
       "dry_run": true,
       "rule_id": "1694…",
       "before": { "match": "aplpay ubs arena", "category": "Entertainment & Recreation", "rename_to": "UBS Arena" },
       "after":  { "match": "ubs arena",         "category": "Entertainment & Recreation", "rename_to": "UBS Arena" },
       "message": "This edits an existing rule in place (its id and order are preserved) and applies to FUTURE transactions only — it will NOT recategorize or rename past transactions. Confirm to apply the edit.",
       "confirm_token": "…",
       "mode": "safe"
     }
     ```
   - `full` mode, or matching `confirm_token` → **execute**: capture the pre-edit state, call
     `ok = svc.update_transaction_rule(rule_id, operator, value, set_category_id, set_merchant)`:
     - `ok` true → `journal.record(batch_id, "update_rule", rule_id, json(old_state),
       json(new_state), label_old, label_new)`; return
       `{ "updated": true, "rule_id": "…", "before": {…}, "after": {…}, "applied_to_existing": false,
         "undo_hint": "call undo_last to revert this edit" }`.
     - `ok` false → `{ "updated": false, "reason": "Monarch did not apply the edit (re-read did not
       reflect it)" }`; **no journal row** (nothing verified to undo).
- **`undo_last`** — extend the `op_type` dispatch: for an `"update_rule"` row, parse `old_value`
  JSON and reverse by
  `ok = svc.update_transaction_rule(r["target_id"], old["operator"], old["value"], old["set_category_id"], old["set_merchant"])`;
  truthy → mark undone; falsy → leave open (retryable), identical to the sibling reverse branches.

## 4. Undo semantics
- **Fully reversible in place:** the reverse of an edit is a compensating edit back to the
  captured old state. The rule id and its top-to-bottom order are preserved throughout — the
  reason real in-place update was chosen over delete+recreate.
- **No past-data side effects to reverse** (future-only, §1): the edit never touched a
  transaction, so undo is a single clean compensating write.
- **Verify-by-re-read on both directions:** success (of edit *and* of undo) is confirmed by
  reading the rule back; a failed re-read leaves the batch open/retryable, never corrupts state.
- **Idempotency caveat (documented):** if the rule is deleted in the Monarch UI between edit and
  `undo_last`, the compensating update's re-read finds no rule → reports undo failed-but-safe
  (retryable), consistent with the honest partial-failure contract from Slice 1.

## 5. Refusals / failure behavior (fail-closed on safety)
- `write_mode == "off"` → standard gate refusal.
- Rule id not found → refuse.
- **Non-simple rule → refuse, naming the offending component** (the core safety guard; prevents
  a full-replacement wipe of criteria/actions the tool doesn't model).
- `set_category` that doesn't resolve to exactly one category → refuse (reuse `resolve_category`).
- No-op edit → refuse.
- Token mismatch in `safe` mode → return the preview again, never execute.
- Post-write re-read doesn't reflect the intended end-state → `{ "updated": false, … }`, no
  journal row. Honest failure, not a silent wrong write. (This also catches the residual
  `setMerchantAction`-by-name round-trip unknown, §2 fact 3.)
- CSV mode → `RuntimeError`.

## 6. Testing (all offline: fake adapter / `tmp_path`)
- **`writes.py`:** `_update_rule_input` builds `id` + create-shape, **preserves the passed
  operator** (not hardcoded `contains`), populates both set-actions from the end-state, all other
  fields null/false; `compute_rule_update_token` is stable, sensitive to every input, and
  distinct from create/category tokens; `is_simple_rule` accepts the three simple shapes
  (cat-only, merchant-only, both) and rejects each complex shape (amount, statement, tags, goal,
  `categoryIds`, `accountIds`, extra action, no-merchant-criteria), and the companion names the
  offending component.
- **FakeAdapter:** grows an in-memory update that finds the rule and replaces its
  operator/value/set-category/set-merchant; its `get_transaction_rules` can return both simple
  and complex rule shapes so the guard tests are real; update round-trips through re-read.
- **community_adapter (parse):** the extended `get_transaction_rules` surfaces `set_category_id`,
  `set_merchant` name, `order`, and every complexity field without dropping existing keys;
  `update_transaction_rule` returns `True` only when the re-read matches, `False` on an error
  payload or a mismatching/absent re-read.
- **MCP tool:** gate off/safe/full; not-found refusal; non-simple refusal (each complex kind);
  unresolvable-category refusal; no-op refusal; dry-run returns before/after + token and changes
  nothing; wrong token re-previews; confirm-with-token (and `full` mode) executes and journals an
  `update_rule` row; failed re-read returns `updated:false` with no journal row.
- **Journal/undo:** `update_rule` records old+new JSON state; `undo_last` replays the old state
  and marks undone (leaves a failed compensating edit retryable); `history` renders the
  before→after line beside older op types.
- **Regression fence:** all existing tests (349 on main at slice start) stay green; the new
  abstract adapter method requires the fake-sweep — every directly-instantiated fake/stub adapter
  in the suite gets a minimal `update_transaction_rule` so the baseline never goes red on
  instantiation.

## 7. What this slice delivers
- The **correction** half of rule cleanup: Claude can now not just *spot* a bad simple rule but
  *fix* it — wrong category target, wrong rename target, or bad match text — reversibly and in
  place, without disturbing the rule's id or order.
- Completes rule CRUD (create ✅ / delete ✅ / **update**), closing "bucket A."
- Extends the op-log to a **seventh** op type with no schema change — further proof the
  generalized journal + single `undo_last` dispatcher scales to new write kinds.

## 8. Boundary / honesty notes
- **Covers 34 of 61 rules** (the simple ones). The other 27 are refused, by design, with a clear
  message. This is stated to the user, not hidden — silent partial coverage would read as "edits
  any rule."
- **Operator and action-set are preserved, not editable** this slice (§1 out-of-scope). A cleanup
  that needs to change contains↔eq, or add/remove an action, is a Monarch-UI job or a later slice.

## 9. Live-proving plan (on-branch, prove-FIRST before merge)
Per the create_category_rule precedent (prove on-branch so any fix stays in one slice), in Claude
Desktop with `write_mode` `safe`, and likely with owner-decision framing in a **fresh chat** (the
Desktop persona has refused standing-config edits without it — see the create_category_rule
finding):
1. **Category-target edit** on a real *category-only* simple rule: dry-run (verify before/after +
   future-only message), confirm, verify the new category in Monarch → Rules, `undo_last`, verify
   reverted to baseline.
2. **Match-text edit** on a real *merchant-rename* simple rule — this is the run that resolves the
   residual `setMerchantAction`-by-name round-trip unknown (§2 fact 3): after the edit, confirm the
   rename target is **unchanged** (not repointed) and the match text changed; `undo_last`; verify
   baseline. If the rename target does not survive, the verify-by-re-read reports `updated:false`
   and we fix the merchant representation before merge.
3. **Non-simple refusal:** point the tool at one of the 27 complex rules; confirm it refuses with
   the component-naming message and changes nothing.

## 10. What this enables next (explicitly deferred)
1. **Slice 2 — `rule_review`** (read-only advisory): scan all 61 rules, flag the four bad-rule
   types (wrong/messy target, bad match text, redundant/duplicate, order/conflict), recommend a
   fix per rule — then the user drives `update_transaction_rule` / `delete_transaction_rule`.
2. **Slice 3 — rule reorder** (needs a DevTools capture of the reorder mutation).
3. Operator changes / add-remove-action edits, if a real cleanup need surfaces.
4. Feeds the eventual `financial_context` resource (clean, self-maintaining rule set → better
   advisory), then the HELOC glide-path tool.
