# Monarch MCP — Plan 4 create-merchant-rule: gated future-only rename rule (design)

**Status:** DESIGN — not yet built. Architectural slice brainstormed 2026-08-23 on the
back of a completed feasibility spike (below). Follows the proven write-gate /
dry-run→confirm / op-log / `undo_last` template established by Plan 4 Slices 1–3
(recategorization, category CRUD) and the merchant-cleanup slice (rename + advisory, shipped
+ live-proven 2026-08-23). Adds ONE gated mutating tool. No schema migration.

**Roadmap slot:** Continues Plan 4 (the "cleanup" superpower of the original Monarch MCP)
and advances Goal #3 (a trustworthy tool over clean data). Sits after the merchant-cleanup
slice (shipped) and before the `financial_context` resource / HELOC glide-path. This is the
capability the merchant-cleanup spec deferred to §9.1 ("rule-creation spike → `create_merchant_rule`").

---

## 1. What this is

The shipped `rename_merchant_matching` tool consolidates **past** transactions (proven live:
`Capital One Des`→`Capital One`, 16 rows repointed onto one curated merchant). It does
nothing for the **future** — next month's import mints the junk merchant again and the user
re-cleans it. A **transaction rule** is the permanent cure: *"whenever a merchant name
contains X, rename it to Y."*

This slice builds exactly that, and only that:

- **`create_merchant_rule` (gated write)** — creates a Monarch transaction rule that renames
  the merchant on **future** transactions whose merchant name **contains** a phrase, behind
  the write-mode gate, with a dry-run→confirm handshake, a duplicate guard, and full undo
  (undo = delete the rule).

### Design decision that shapes everything: FUTURE-ONLY (§4.1)

The Monarch create-rule mutation carries an `applyToExistingTransactions` flag. **This tool
always sends it `false`.** Rationale (decided in brainstorming, 2026-08-23):

- The create mutation returns **only `errors`** — never the list of transactions it touched.
  So if the rule silently rewrote past rows, we would have **no record of what changed**, and
  deleting the rule would **not** un-rename them. That breaks the one invariant every shipped
  slice holds: *every write is cleanly reversible.*
- We already have a fully-reversible past-fixer (`rename_merchant_matching`, per-row
  before-state journaled). Making the rule future-only keeps a clean division of labor and
  keeps undo honest.
- "Fix past **and** prevent future" — the seasoned-user dream — is still delivered, by
  **composition**: Claude calls `rename_merchant_matching` first (reversible past fix), then
  `create_merchant_rule` (reversible future catch). Two independently undoable operations, one
  smooth user experience, zero loss of reversibility. A brand-new Monarch user (little past
  data) is served directly — the future-catch is the whole value for them.

### Out of scope (deferred)
- **`applyToExistingTransactions=true`** — deliberately not exposed (see above). Composition
  covers the use case reversibly.
- **Non-rename rules** (set category, add tags, split, link goal) — the create input supports
  them (§2), but this slice is merchant-rename only. YAGNI; each is its own later slice.
- **Rule *update*** — not captured or built. Editing a rule = delete + recreate for now, or a
  later spike. Only create + delete are needed for a reversible create-only tool.
- **Other match operators** (`exactly`, `starts_with`, …) — only `contains` was captured and
  only `contains` matches the existing rename tool's semantics. Others deferred.

## 2. Spike findings (the confirmed API facts this design rests on)

Schema introspection is disabled for non-admin users and `monarchmoney` 0.1.15 has **no**
rule-write operations, so the create/delete shapes were captured **live** on 2026-08-23:
Adam created and then deleted a throwaway rule (`ZZTESTPROBE` contains → rename `ZZ Probe`)
in the Monarch web app with the DevTools Network panel recording, reading only the request
**Payload** (never "Copy as cURL", which would leak session cookies). The test rule was
deleted; the account is back to baseline.

| Operation | Mutation (operationName) | Shape | Status |
|---|---|---|---|
| **Read** | `Web_GetTransactionRules` | `transactionRules { id order ...TransactionRuleFields }` — already shipped as `GET_TRANSACTION_RULES` | ✅ live |
| **Create** | `Common_CreateTransactionRuleMutationV2` | `createTransactionRuleV2(input: CreateTransactionRuleInput!) { errors { ...PayloadErrorFields } }` | ✅ captured live |
| **Delete** | `Common_DeleteTransactionRule` | `deleteTransactionRule(id: ID!) { deleted errors { ...PayloadErrorFields } }` | ✅ captured live |

**Create input (`CreateTransactionRuleInput`), decoded from the captured ZZTESTPROBE rule.**
A rename rule needs only three populated fields; every other field is sent explicit-`null`
exactly as the web app sent them (defensive — avoids a "non-null field missing" 400 like the
GET_BUDGETS incident):

```jsonc
{
  "merchantNameCriteria": [{ "operator": "contains", "value": "<contains>" }],  // the match
  "setMerchantAction": "<rename_to>",                                            // the rename (plain string)
  "applyToExistingTransactions": false,                                          // FUTURE-ONLY (§1)
  "merchantCriteriaUseOriginalStatement": false,
  // --- all null, mirrored from the captured payload ---
  "accountIds": null, "categoryIds": null, "amountCriteria": null,
  "merchantCriteria": null, "originalStatementCriteria": null,
  "addTagsAction": null, "linkGoalAction": null, "linkSavingsGoalAction": null,
  "reviewStatusAction": null, "setCategoryAction": null, "splitTransactionsAction": null
}
```

**Two facts that drive the design:**
1. **Create returns no id.** Response is `{ errors }` only. To learn the new rule's id (for
   the undo journal), the tool snapshots the rule-id set *before* create and re-reads *after*;
   the newly-appeared id is the created rule (§4.2). Robust regardless of criteria field names.
2. **Write field ≠ read field.** The create input uses `merchantNameCriteria`; the shipped
   read query (`GET_TRANSACTION_RULES`) selects `merchantCriteria`. Whether a rule created via
   `merchantNameCriteria` reads back under `merchantCriteria` is **unverified**. This only
   affects the *duplicate guard* (§6), which is a convenience, not a safety invariant — the
   guard fails **open** (see §6). The undo id-lookup does not depend on it (id-set diff).

## 3. Retail Sync awareness (carried forward)

The merchant-cleanup spec (§3) established that the Retail Sync extension owns line-item
splitting/categorization on Amazon/Target and **does not change merchant names**, so
merchant-*rename* rules are complementary to it (only *category* rules for those retailers
are pointless). This slice creates merchant-rename rules only, so it is safe with respect to
Retail Sync. The dry-run's informational match count (§4.3) reuses the shipped matching, which
already carries Retail-Sync tag data; no special handling is required here beyond noting that
`rename_merchant_matching` (the composition partner) applies its own Retail-Sync default-exclude.

## 4. Architecture

Additive, follows the established seams. No schema migration.

### 4.1 GraphQL constants (`financial_mcp/monarch/queries.py`)
Add two mutation constants beside the existing `GET_TRANSACTION_RULES`:

```python
CREATE_TRANSACTION_RULE = """
  mutation Common_CreateTransactionRuleMutationV2($input: CreateTransactionRuleInput!) {
    createTransactionRuleV2(input: $input) {
      errors { message code __typename }
      __typename
    }
  }
"""

DELETE_TRANSACTION_RULE = """
  mutation Common_DeleteTransactionRule($id: ID!) {
    deleteTransactionRule(id: $id) {
      deleted
      errors { message code __typename }
      __typename
    }
  }
"""
```

(The web app requests errors via a `PayloadErrorFields` fragment; we inline `message code`
— the minimal fields needed to surface a failure reason. This is the same slimming approach
used throughout the server, and errors are read-only fields so slimming carries no 400 risk.)

### 4.2 Adapter (mirror the rename pattern across all three adapters)
- `adapter.py` (ABC) — add two abstract methods:
  - `create_transaction_rule(self, contains: str, rename_to: str) -> tuple[bool, str | None]`
    — returns `(created, rule_id)`. The two facts are **distinct** so the tool can report
    honestly (§4.6): `(True, "id")` = created and identified; `(True, None)` = created but the
    id could not be captured for undo (rare — read-lag or a concurrent rule made the id-set
    diff ambiguous); `(False, None)` = Monarch declined (the rule was **not** created).
  - `delete_transaction_rule(self, rule_id: str) -> bool` — `True` when Monarch reports the
    rule deleted, else `False`.
- `community_adapter.py`:
  ```python
  def create_transaction_rule(self, contains, rename_to) -> tuple[bool, str | None]:
      before = {r["id"] for r in self.get_transaction_rules()}
      payload = self._run(self._transport(
          "Common_CreateTransactionRuleMutationV2", CREATE_TRANSACTION_RULE,
          {"input": _rename_rule_input(contains, rename_to)}))
      errors = ((payload.get("createTransactionRuleV2") or {}).get("errors")) or []
      if errors:
          return (False, None)                       # not created
      after = self.get_transaction_rules()
      new = [r["id"] for r in after if r["id"] not in before]
      # created either way (errors empty); id only trustworthy when exactly one appeared
      return (True, new[0]) if len(new) == 1 else (True, None)

  def delete_transaction_rule(self, rule_id) -> bool:
      data = self._run(self._transport(
          "Common_DeleteTransactionRule", DELETE_TRANSACTION_RULE, {"id": rule_id}))
      res = data.get("deleteTransactionRule") or {}
      return bool(res.get("deleted")) and not (res.get("errors") or [])
  ```
  `_rename_rule_input(contains, rename_to)` is a small pure helper (in `writes.py`, §4.4)
  returning the full input dict from §2.
- `csv_adapter.py` — `create_transaction_rule` **raises** `RuntimeError("writes are not
  available in CSV mode")` exactly like the sibling write stubs; `delete_transaction_rule`
  raises the same (it is a write). `get_transaction_rules` already returns `[]` in CSV mode.

### 4.3 DataService (`data_service.py`)
Thin pass-throughs mirroring `rename_merchant`/`transaction_rules` (both already exist):
- `create_merchant_rule(self, contains, rename_to) -> tuple[bool, str | None]` → adapter
  passthrough (see §4.2 for the tuple contract), then invalidate any cached rules read (the
  existing `transaction_rules()` is an uncached passthrough today; if that holds, nothing to
  invalidate — match the current behavior).
- `delete_transaction_rule(self, rule_id) -> bool` → adapter passthrough.

### 4.4 `writes.py` helpers (pure, offline-testable)
- `_rename_rule_input(contains: str, rename_to: str) -> dict` — returns the §2 input dict
  (only the three populated fields vary; the rest are constant nulls). One place owns the
  captured shape.
- `compute_rule_token(contains: str, rename_to: str) -> str` — mirror `compute_merchant_token`:
  `key = contains + "->" + rename_to`; sha256 hexdigest[:16]. Binds the exact rule so a stale
  preview can't be replayed against a different rule.
- **Match count for the dry-run:** reuse the shipped "contains" matching
  (`match_transactions(..., merchant=contains)`) against `svc.transactions()` and count the
  result. Identical semantics to what the rule will do going forward.

### 4.5 Op-log / undo (`cache/undo_journal.py` — NO schema change)
- Add `"create_rule"` to `REVERSIBLE_OPS`
  (currently `("recategorize", "rename_category", "delete_category", "rename_merchant")`).
  That single change makes `last_undoable_batch`, `undoable_rows`, and `pending_dependents`
  recognize it.
- `history()` summary: add a branch so `op_type == "create_rule"` renders a readable line,
  e.g. `f"Rule: contains '{old_n}' → '{new_n}' ×{n}"` (parallel to the existing
  create/delete/rename branches around lines 159–164). For a single-rule batch `n == 1`.
- The generic row already fits: store `old_value = contains`, `new_value = rename_to`,
  `target_id = rule_id`, `label_old = contains`, `label_new = rename_to`. TEXT columns hold
  these happily.

### 4.6 MCP tool (`mcp_server.py`)
`create_merchant_rule(contains, rename_to, confirm_token=None) -> dict` — placed beside
`rename_merchant_matching` (line ~599) and `merchant_cleanup_suggestions`, wrapped in the
standard `_guard(_run)`:

1. **Gate:** `_writes_enabled()` — `write_mode == "off"` → refuse with the standard message.
2. **Validate:** blank/whitespace `contains` → refuse (`"contains must be a non-empty phrase"`);
   blank `rename_to` → refuse (`"rename_to must be a non-empty name"`).
3. **Duplicate guard (best-effort, §6):** read `svc.transaction_rules()`; if a rule already
   has the same `value` (== `contains`) and same `set_merchant` (== `rename_to`), refuse with
   `{"refused": "a rule for this already exists", "existing_rule_id": ...}`.
4. **Count existing matches:** `existing = len(match_transactions(svc.transactions(),
   merchant=contains))`.
5. **Token:** `token = compute_rule_token(contains, rename_to)`.
6. **Dry-run vs execute** (identical control flow to `rename_merchant_matching`):
   - `write_mode == "safe"` and no/invalid `confirm_token` → **preview** (create nothing):
     ```json
     {
       "dry_run": true,
       "rule": { "contains": "AMZN", "rename_to": "Amazon", "apply_to_existing": false },
       "existing_matches": 16,
       "message": "This rule will rename FUTURE transactions whose merchant contains 'AMZN' to 'Amazon'. I'm showing you the 16 existing transactions it matches so you know they will NOT be changed — a new rule only applies to transactions imported from now on. If you also want those 16 cleaned up now, run rename_merchant_matching first. Confirm to create the rule.",
       "confirm_token": "…",
       "mode": "safe",
       "note": "no rule created yet"
     }
     ```
     The `message` states the count **and the reason it is being shown** (Adam's explicit
     requirement, 2026-08-23): the count exists to make the past/future boundary visible and
     point to the companion tool.
   - `write_mode == "full"`, or `confirm_token` matches → **execute**:
     `created, rule_id = svc.create_merchant_rule(contains, rename_to)`, then three cases
     (from the §4.2 tuple contract):
     - **`(False, None)` — not created:** `{"created": false, "reason": "Monarch declined to
       create the rule"}`. No journal row (nothing to undo).
     - **`(True, rule_id)` — created + identified (the normal path):** journal it —
       `journal.record(batch_id, "create_rule", rule_id, contains, rename_to, contains,
       rename_to)` — and return:
       ```json
       { "created": true, "rule_id": "2529…", "contains": "AMZN", "rename_to": "Amazon",
         "applied_to_existing": false, "existing_matches": 16,
         "undo_hint": "call undo_last to delete this rule" }
       ```
     - **`(True, None)` — created but id un-capturable (rare):** no journal row (we have no id
       to delete). Return, **and only in this case**, a briefing so the user knows to act if
       they need to:
       ```json
       { "created": true, "rule_id": null, "undo_available": false,
         "contains": "AMZN", "rename_to": "Amazon", "applied_to_existing": false,
         "note": "Rule created, but I couldn't capture its id to enable one-click undo. If you change your mind, delete it in Monarch → Settings → Rules." }
       ```
       The `undo_available: false` + `note` fields appear **only** on this path — the normal
       success response (above) never carries them, so it stays uncluttered (Adam's
       requirement, 2026-08-23: brief the user only when there is something they need to
       know or decide).

- **`undo_last`** — extend its dispatch (the `op_type` chain at lines ~827–847): for a
  `"create_rule"` row, reverse by `ok = svc.delete_transaction_rule(r["target_id"])`; truthy
  → mark undone; falsy → leave open (retryable), identical to the sibling reverse branches.

## 5. Undo semantics
- **Fully reversible:** the reverse of "create rule" is "delete rule", and delete takes only
  the id we journaled. `undo_last` deletes the created rule; `undo_history` shows the
  `create_rule` line beside older batches.
- **No past-data side effects to reverse** (the whole point of future-only, §1): the rule
  never touched a transaction, so undo is a clean single delete with nothing else to unwind.
- **Un-capturable-id path is not undoable by design (§4.6 `(True, None)`):** no journal row is
  written because there is no id to delete, and the response tells the user so with
  `undo_available: false` + a delete-it-manually note. Honest over silent — never reports a
  non-existent rule, never risks a retry-duplicate.
- **Idempotency caveat (documented):** if the user deletes the rule manually in the Monarch UI
  before calling `undo_last`, the delete mutation returns `deleted: false` (nothing to delete);
  the tool reports the undo as failed-but-safe (retryable) rather than corrupting state —
  consistent with the honest partial-failure contract from Slice 1.

## 6. Refusals / failure behavior (fail-closed on safety, fail-open on the dup guard)
- `write_mode == "off"` → refuse with the standard gate message.
- Blank `contains` / blank `rename_to` → refuse (messages in §4.6).
- Token mismatch in `safe` mode → return the preview again (re-run dry-run), never execute.
- Monarch declines the create (`(False, None)`) → `{"created": false, "reason": ...}`, no
  journal row.
- Create succeeds but id un-capturable (`(True, None)`) → `{"created": true,
  "undo_available": false, "note": ...}`, no journal row (§4.6). Honest, not a false failure.
- **Duplicate guard is best-effort and fails OPEN.** Because the read field may not surface
  `merchantNameCriteria`-created rules (§2 fact 2), the guard may miss an existing duplicate.
  This is acceptable: **a duplicate rule is not destructive** (Monarch permits identical rules;
  two rules renaming `AMZN`→`Amazon` produce the same result) and **is reversible** (delete).
  The guard is a courtesy to avoid clutter, not a safety invariant — so a miss is harmless.
  The safety invariants (gate, token handshake, reversibility) do **not** depend on it.

## 7. Testing
Additions to the write-tool, journal, and adapter suites — all offline (fake adapter /
`tmp_path`); the fake adapter grows an in-memory rules list so create/delete/read round-trip:
- `_rename_rule_input`: exactly the three populated fields; all others null; `apply_to_existing`
  always false.
- `compute_rule_token`: stable, sensitive to both `contains` and `rename_to`.
- Adapter round-trip: `create_transaction_rule` returns `(True, new_id)` on an id-set diff of
  exactly one, `(False, None)` on a Monarch error payload, and `(True, None)` on an ambiguous
  diff (0 or >1 new ids); `delete_transaction_rule` returns `True` on `{deleted: true,
  errors: []}`, `False` on `{deleted: false}` or non-empty errors; `csv_adapter` create/delete
  raise `RuntimeError`.
- `create_merchant_rule` tool: off-mode refusal; blank-`contains` and blank-`rename_to`
  refusals; dry-run returns the rule + `existing_matches` + the reason-bearing `message` +
  a token, and creates nothing; confirm-with-token executes and journals a `create_rule` row;
  `full` mode executes without a token; duplicate guard refuses when the fake rules list
  already contains the pair (and the message names the existing id); `(False, None)` path
  returns `created: false` with no journal row; `(True, None)` path returns `created: true`
  **with** `undo_available: false` + `note` and writes **no** journal row, while the normal
  `(True, id)` path returns neither `undo_available` nor `note` (uncluttered success).
- `undo_last` reverses a `create_rule` batch (calls delete, marks undone) and leaves a
  failed delete retryable; `undo_history` renders the `create_rule` summary line beside older
  op types.
- **Regression fence:** all existing tests (314 on main at slice start) stay green; the two
  new abstract adapter methods require the R1-style fake sweep — every directly-instantiated
  fake adapter in the suite gets minimal `create_transaction_rule`/`delete_transaction_rule`
  implementations so the baseline never goes red on instantiation.

## 8. What this slice delivers
- The **permanent** merchant-cleanup cure: set it once, future imports stay clean — the
  capability the merchant-cleanup spec explicitly deferred.
- Completes the merchant-identity story: past fix (`rename_merchant_matching`, shipped) +
  future prevention (`create_merchant_rule`), composable by Claude into one "clean it up and
  keep it clean" experience while keeping every step reversible.
- Extends the op-log to a **fifth** op type with no schema change — further proof the
  generalized journal + single `undo_last` dispatcher scales to new write kinds.

## 9. Live-proving plan (post-merge, mirrors prior slices)
After merge, prove in Claude Desktop (write_mode `safe`): dry-run a rule (verify the
match-count + reason message), confirm-create it, verify it appears in Monarch's Rules UI,
`undo_last` to delete it, verify it's gone and the account is back to baseline. This confirms
the captured create/delete mutation shapes work first-try against the live API (the §2 facts)
and answers the one residual live unknown — whether the id-set diff cleanly captures the new
rule id in practice.

## 10. What this enables next (explicitly deferred)
1. **Rule *update*** — capture `updateTransactionRule` if in-place edits are wanted (vs
   delete+recreate).
2. **Non-rename rule actions** (category / tags / split) — the input already supports them.
3. **Merchant merge** — still not discoverable; lowest priority (and largely moot since
   rename consolidates past rows).
4. Feeds the eventual `financial_context` resource (clean, self-maintaining merchant identity
   → better advisory), then the HELOC glide-path tool.
