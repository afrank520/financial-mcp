# Monarch MCP — Plan 4 merchant cleanup: advisory + safe transaction-rename (design)

**Status:** DESIGN — not yet built. Architectural slice brainstormed + feasibility-spiked
2026-08-22. Follows the proven write-gate / dry-run→confirm / op-log / `undo_last`
template established by Plan 4 Slices 1–3 (recategorization, category CRUD). Adds ONE
read-only advisory tool and ONE gated mutating tool; defers the higher-risk / unverified
capabilities (transaction rules, merchant merge) to their own later spikes.

**Roadmap slot:** Continues Plan 4 (the "cleanup" superpower of the original Monarch MCP)
and advances Goal #3 (a trustworthy tool over clean data). Sits after the credential-store
foundation (shipped) and before the `financial_context` resource / HELOC glide-path.

---

## 1. What this is

Merchant "cleanup" in Monarch is really a **merchant-identity** problem: bank statement
strings carry volatile tokens (dates, transaction ids), so Monarch keeps minting *new*
merchant entities for what is one real merchant (`AMZN Mktp US*2X4…`, `AMZN Mktp US*9K1…`,
`Amazon.com*RT5…`). The user's manual ritual is: (1) rename merchants on transactions
using **"contains"** matching, then (2) consolidate any merchant **without a logo** (an
uncurated, auto-minted junk entity) into the curated one.

This slice builds the safe, confirmed-feasible half of that ritual:

- **`merchant_cleanup_suggestions` (read-only advisory)** — builds a merchant inventory
  from transactions, flags the **uncurated (no-logo)** merchants, groups likely variants
  under a probable canonical target, cross-references existing transaction rules, and marks
  Retail-Sync-managed merchants as off-limits. Pure read; zero risk. This is the
  north-star "the tool finds the cleanup for you" capability.
- **`rename_merchant_matching` (gated bulk write)** — bulk-renames the merchant on every
  transaction matching a filter (the **"contains"** logic), behind the write-mode gate,
  with a dry-run→confirm handshake, a batch cap, and full per-row undo. Automates step (1)
  safely.

### Out of scope (deferred, each to its own spike / slice)
- **Transaction rule creation** — the *permanent* cure ("merchant contains X → set merchant
  Y", applied to past AND future). Rules are **readable** (proven — the user has 61), but
  the **create/update/delete** operations are **not verifiable** from the installed
  `monarchmoney` library or via introspection (disabled for non-admins). Building the write
  side needs a dedicated mutation-probe session first. §9.
- **Merchant merge** — no merge operation is discoverable, and a merge is likely
  irreversible on Monarch's side (breaks the clean undo story). Lowest priority. §9.
- **Single-transaction merchant rename** — YAGNI. The bulk "contains" form is the real
  workflow; a one-row rename adds a tool surface without a use case.

## 2. Spike findings (the confirmed API facts this design rests on)

Probed 2026-08-22 against the live account (throwaway scripts, deleted). Schema
introspection is disabled for non-admin users, so findings come from the installed
`monarchmoney` 0.1.15 library (authoritative encoding of Monarch's real operations) plus
read-only live queries.

| Fact | Status | Basis |
|---|---|---|
| Transaction-level merchant rename via `Web_TransactionDrawerUpdateTransaction` with `input.name` | ✅ writable | `monarchmoney.update_transaction(merchant_name=…)` sends `input.update({"name": …})` |
| `Merchant` exposes `id`, `name`, `logoUrl` | ✅ readable live | live query returned `logo=YES` on curated merchants |
| "No logo = uncurated/junk" signal | ✅ machine-readable | `logoUrl` present ⇔ user-curated |
| `Retail Sync` system tag on extension-managed transactions | ✅ already parsed | `Transaction.tags` already carries tag names today |
| Transaction rules readable — shape `merchantCriteria[{operator: contains\|eq, value}]` + `setMerchantAction{id,name}` + `setCategoryAction{id,name}`, type `TransactionRuleV2` | ✅ readable (61 live) | live `transactionRules` query |
| Merchant **merge** operation | ❌ not discoverable | absent from library; introspection blocked |
| Rule **create/update/delete** | ⚠️ unverified | read works; no write op in library |

**Residual unknown (answered at first live write, not guessed):** when a transaction's
merchant is renamed to `"Amazon"`, does Monarch **re-point** it to the existing `Amazon`
entity (consolidation) or merely **relabel** that row (a new/override merchant)? The rename
mutation will select `merchant { id name }` back, so the tool observes the resulting
merchant id and reports it — the same "prove the mutation shape live" approach used in
Slices 1–3. The tool is correct either way (it renames the name string the user matches on);
this only informs how much *consolidation* a rename achieves vs. a rule.

## 3. Retail Sync awareness (first-class constraint)

Monarch's **Retail Sync Extension** (Amazon/Target, more later) splits orders into line
items, recategorizes them, adds notes, and stamps a **`Retail Sync` system tag** on every
matched transaction. It is idempotent per transaction (*"once touched… won't be updated
again"*), runs every 24h on new transactions, and — critically — **does not change merchant
names**. So the extension and this slice operate on different layers (it owns line-item
splitting/categorization; we own merchant identity) and are **complementary, not
colliding**. Two rules fall out of that and bind this design:

1. **`rename_merchant_matching` excludes `Retail Sync`-tagged transactions by default.**
   An explicit `include_retail_sync: bool = False` opt-in is required to touch them, and
   the result reports how many were skipped. Rationale: renaming an Amazon/Target merchant
   requires the user to also update the extension's Retailers setting or the extension may
   stop matching; the safe default keeps us out of that lane.
2. **`merchant_cleanup_suggestions` flags any merchant with Retail-Sync-tagged transactions
   as "managed by Retail Sync — leave to the extension"** and does not propose renaming it.

(For the deferred rules work: the extension overrides *category* rules after import on
Amazon/Target, so category rules for those retailers are pointless; merchant-rename rules
survive. Recorded for §9, not built here.)

## 4. Architecture

Follows the established seams exactly. New code is additive; no schema migration.

### 4.1 Data model + query (small, backward-compatible additions)
- `financial_mcp/monarch/models.py` — add two optional fields to `Transaction`:
  `merchant_id: str = ""` and `merchant_logo: str = ""` (both default empty, so every
  existing constructor call and test stays valid).
- `financial_mcp/monarch/queries.py` — add `logoUrl` to the `merchant { … }` selection in
  `GET_TRANSACTIONS` (it already fetches `id`, `name`, `transactionsCount`). Add a new
  read query `GET_TRANSACTION_RULES` selecting `transactionRules { id merchantCriteria
  { operator value } setMerchantAction { id name } setCategoryAction { id name } }`.
- `community_adapter.py::_to_transaction` — populate `merchant_id` from `merchant.id` and
  `merchant_logo` from `merchant.logoUrl` (both `or ""`).

### 4.2 Adapter (mirror the recategorize pattern across all three adapters)
- `adapter.py` (ABC) — add abstract `rename_transaction_merchant(self, transaction_id: str,
  merchant_name: str) -> str | None` (returns the **resulting merchant id** on success,
  `None` on failure — a truthy superset of the recat pattern's bool that also answers the
  §2 residual unknown) and `get_transaction_rules(self) -> list[dict]`.
- `community_adapter.py`:
  ```python
  def rename_transaction_merchant(self, transaction_id, merchant_name) -> str | None:
      data = self._run(self._transport(
          "Web_TransactionDrawerUpdateTransaction", UPDATE_TRANSACTION_MERCHANT,
          {"input": {"id": transaction_id, "name": merchant_name}}))
      txn = (data.get("updateTransaction") or {}).get("transaction") or {}
      merch = txn.get("merchant") or {}
      return merch.get("id") if merch.get("name") == merchant_name else None
  ```
  where `UPDATE_TRANSACTION_MERCHANT` is the same mutation as the category one but selects
  `merchant { id name }` back (so the resulting id is observable — §2 residual unknown).
  `get_transaction_rules` runs `GET_TRANSACTION_RULES` and flattens each rule to
  `{"id", "operator", "value", "set_merchant", "set_category"}` (first criterion; `None`s
  where absent).
- `csv_adapter.py` — `rename_transaction_merchant` **raises** `RuntimeError("writes are not
  available in CSV mode")` exactly like the sibling write stubs; `get_transaction_rules`
  returns `[]` (a truthful empty — CSV mode simply has no rules, not an error).

### 4.3 `writes.py` helpers (pure, offline-testable)
- Reuse `match_transactions(...)` unchanged for the "contains" filter (its `merchant=`
  substring match IS the contains logic).
- `compute_merchant_token(transaction_ids: list[str], target_merchant: str) -> str` —
  mirror `compute_confirm_token`: `key = "|".join(sorted(ids)) + "->" + target_merchant`;
  sha256 hexdigest[:16]. Binds the exact matched set + target name so a stale/edited preview
  can't be replayed against a different result.
- `build_merchant_inventory(transactions) -> list[MerchantRow]` — group by `merchant_id`
  (fall back to name when id empty); each row: `id`, `name`, `logo` (bool from
  `merchant_logo`), `txn_count`, `total_amount`, `retail_sync` (any member txn carries the
  `"Retail Sync"` tag), `sample_ids`.
- `suggest_consolidations(inventory, rules) -> list[Suggestion]` — deterministic, no fuzzy
  ML: for each **uncurated** row (no logo, not retail_sync), normalize its name (lowercase,
  strip trailing volatile tokens — digits, `*`-suffixed ids, date-like fragments) to a stem;
  if a **curated** row's normalized name contains / is contained by that stem, propose it as
  the canonical target. Annotate each suggestion with whether an existing rule's `value`
  already matches the uncurated name (`already_ruled: bool`). Conservative: no target ⇒
  emit the uncurated merchant as an unmatched "needs a decision" entry rather than guessing.

### 4.4 Undo journal (op-log — NO schema change)
- Add `"rename_merchant"` to `REVERSIBLE_OPS` in `undo_journal.py`. That single change makes
  `last_undoable_batch`, `undoable_rows`, and `pending_dependents` recognize it.
- `history()` summary: add a branch so `op_type == "rename_merchant"` renders
  `f"{old_n}→{new_n} (merchant) ×{n}"` (parallel to the recat and category branches).
- The generic row already fits: `old_value` = old merchant **name**, `new_value` = new
  merchant name, `label_old`/`label_new` = the same names. TEXT columns hold names as
  happily as they held category ids. `undo_last` already dispatches by `op_type`; add the
  one reverse branch (below).

### 4.5 MCP tools (`mcp_server.py`)
- **`rename_merchant_matching(to_merchant, merchant=None, category=None, min_amount=None,
  max_amount=None, start=None, end=None, include_retail_sync=False, confirm_token=None)`** —
  structurally a clone of `recategorize_matching` (§ lines 479–567 today):
  - Same `_writes_enabled()` gate, same date parsing/validation, same `_guard`/`_run`.
  - Resolve nothing against categories; the target is a free-text merchant name (the rename
    can create/repoint — fail-closed name resolution does not apply to a *new* name).
  - `matched = match_transactions(...)`; then, unless `include_retail_sync`, partition out
    transactions whose `tags` contain `"Retail Sync"` into `skipped_retail_sync` and drop
    them from the write set.
  - Empty → `{"would_change": 0, ...}`. Over `batch_cap` → refuse (no truncation).
  - `token = compute_merchant_token([t.id for t in write_set], to_merchant)`.
  - `write_mode == "safe"` and no/invalid token → preview (`dry_run: True`, `would_change`,
    `sample` of up to 20 rows showing `merchant`→`to_merchant`, `skipped_retail_sync`,
    `confirm_token`, `mode`). `full` or valid token → execute.
  - Execute: per row `journal.record(batch_id, "rename_merchant", t.id, t.merchant,
    to_merchant, t.merchant, to_merchant)`; `rid = svc.rename_merchant(t.id, to_merchant)`;
    on truthy `rid` → `mark_succeeded` + capture `rid` (report the first as
    `resulting_merchant_id`); on `None` collect `{transaction_id, reason}`.
  - Return `{"changed", "failed", "failures", "skipped_retail_sync", "batch_id",
    "to": to_merchant, "resulting_merchant_id", "undo_hint": "call undo_last to reverse"}`.
- **`merchant_cleanup_suggestions(limit=50, include_curated=False)`** — read-only:
  `inv = build_merchant_inventory(svc.transactions())`; `rules = svc.transaction_rules()`;
  `suggestions = suggest_consolidations(inv, rules)`. Return `{"uncurated_count",
  "suggestions": [...], "retail_sync_managed": [...names...], "note": "..."}`. No gate (no
  writes). `include_curated=True` also lists curated merchants for context.
- **`undo_last`** — extend its dispatch: for a `"rename_merchant"` row, reverse by
  `svc.rename_merchant(target_id, old_value)` (old_value = the prior name); success =
  truthy return → mark that row undone, `None` → leave it open (retryable) — identical
  control flow to the recat reverse branch it sits beside.
- **Service layer (`svc`)** — add thin `rename_merchant(txn_id, name)` and
  `transaction_rules()` pass-throughs mirroring `recategorize`/`categories`.

## 5. Undo semantics
- **Fully reversible** (unlike `create_category`/`delete_category`): the reverse of a name
  rename is a name rename. Per-row `old_value` restores each transaction to *its own* prior
  name, so a "contains" batch that swept several distinct junk names into one canonical name
  un-sweeps each correctly.
- **Caveat (documented, mirrors the delete→recreate id caveat):** if renaming away from a
  junk auto-merchant leaves it with zero transactions, Monarch may garbage-collect that
  entity; undo restores the *name*, and Monarch may mint a new entity id for it. The name
  the user matched on is restored faithfully; only the underlying merchant id may differ.

## 6. Refusals / failure behavior (fail-closed, never a bare trace)
- `write_mode == "off"` → both writes refuse with the standard gate message; the advisory
  tool still works (it's read-only).
- Over batch cap → refuse with the count and remedy; never silently truncate.
- Bad date → `"start must be YYYY-MM-DD"` / `"end must be YYYY-MM-DD"`; `start > end` →
  refuse. (Copied verbatim from `recategorize_matching`.)
- Empty `to_merchant` (blank/whitespace) → refuse (`"to_merchant must be a non-empty name"`).
- A row Monarch declines to apply → counted in `failures` with a reason; the batch reports
  partial success honestly (the Slice-1 partial-failure contract).

## 7. Testing
New `tests/test_merchant_cleanup.py` + additions to the write-tool and journal suites,
all offline (fake adapter / `tmp_path`):
- `compute_merchant_token`: stable, order-independent over ids, target-sensitive.
- `build_merchant_inventory`: groups by id; logo→curated flag; Retail-Sync detection from
  tags; counts/totals.
- `suggest_consolidations`: uncurated→curated match by stripped stem; `already_ruled`
  annotation from a fake rules list; no-guess when no curated target; curated + retail-sync
  excluded from suggestions.
- `rename_merchant_matching`: off-mode refusal; dry-run token then confirm; batch-cap
  refusal; Retail-Sync exclusion by default and inclusion under the opt-in; partial failure;
  journal rows written with `op_type="rename_merchant"` and name old/new values.
- `undo_last` reverses a `rename_merchant` batch (restores per-row prior names) and leaves
  failed rows retryable; `undo_history` renders the merchant summary line.
- Adapter: `community_adapter.rename_transaction_merchant` returns the resulting merchant id
  when the mutation echoes the new name, `None` otherwise (mocked transport);
  `get_transaction_rules` flattens the proven live shape; `csv_adapter` rename→None,
  rules→[].
- **Regression fence:** the existing 292 tests stay green; the new optional `Transaction`
  fields default empty so no existing constructor/assertion changes.

## 8. What this slice delivers
- The advisory tool turns the user's manual "look for merchants without a logo" scan into a
  one-call, ranked, rule-aware report — at scale, across the whole merchant list.
- The rename tool automates the "contains" sweep safely and reversibly, staying clear of the
  Retail Sync extension by default.
- Both establish the merchant-identity seam (merchant id + logo now flow through the model)
  that the deferred rules work will build on.

## 9. What this enables next (explicitly deferred)
1. **Rule-creation spike** — reverse-engineer `createTransactionRule` (and update/delete)
   from the web app's network traffic, then a `create_merchant_rule` tool: *"merchant
   contains X → set merchant Y"* — the permanent cure that fixes past AND future imports.
   Guard: never create category rules for Retail-Sync retailers (§3).
2. **Merchant merge** — only if a reversible (or acceptably one-way, `create_category`-style
   non-undoable) merge operation is confirmed. Lowest priority.
3. Feeds the eventual `financial_context` resource (clean merchant identity → better
   advisory).
