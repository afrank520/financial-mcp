# Monarch MCP — budget_status income/expense/transfer split (design)

**Date:** 2026-08-11
**Status:** ✅ Shipped + live-proven (2026-08-11). See §4 live-verification results.
**Roadmap slot:** Follow-up to the shipped Tier 1 breadth spec (`2026-08-10-monarch-mcp-tier1-breadth-design.md`, §8 logged this as the known boundary). Closes the "can the data be trusted" gap surfaced during that spec's live verification.

---

## 1. Scope

`budget_status` today returns a single flat `totals` block that sums income, expense, and transfer budget rows together. During live verification (2026-08-10) this made a Claude-authored "you spent $79,641" narration fold income actuals in with expenses, and forced the model to *infer* from category names which rows were income ("Paychecks came in favorable"). Every number the tool emits is Monarch's own and penny-correct — the gap is that the tool doesn't encode Monarch's income/expense semantics, so the narration can misread them.

This spec moves that trust from the narration into the tool: it surfaces Monarch's own per-group `type` (income / expense / transfer), tags each budget row with it, splits the totals by type, relabels the income "over budget" flag as `favorable`, and returns a `methodology` string on every call (the proven `savings_summary` "proactive asterisk" pattern).

### In scope
- One new GraphQL field: `group { type }` on the existing `GetCategories` query.
- One new model field: `Category.group_type: str | None`.
- Per-row `type` tagging in `budget_status`; income rows get `favorable` instead of `over_budget`.
- Split `totals` into `expense` / `income` / `transfer` (+ `unknown`) sub-blocks.
- A `methodology` string on the `budget_status` result.

### Out of scope (YAGNI)
- No new MCP tool; `budget_status`'s signature is unchanged.
- No budget writes (Plan 4).
- No rollover/flex re-derivation — planned/actual/remaining stay pass-through.
- No changes to `networth_trend` or `category_summary`.
- No group-level budget rollup from Monarch's `monthlyAmountsByCategoryGroup` (still YAGNI; the per-row `type` + split totals answer the question).

### Honors the standing constraint
Every value stays Monarch's own. `group.type` is a label Monarch already assigns to each category group — we surface it, we do not derive, infer, or invent it. `favorable` is a *relabeling* of the same `remaining < 0` comparison, not a new computed number.

---

## 2. Architecture

Same one-directional layering; this is a thin enrichment of one existing path:

```
GetCategories (+ group.type) ─► Category.group_type ─► categories map
                                                        │
BudgetItem (unchanged) ─────────► budget_status(items, categories, start, end)
                                    joins each row's category_id → type,
                                    splits totals by type, adds methodology
                                  ─► budget_status tool (shape: additive)
```

- **No cache change.** The `categories` cache already backs this tool. Adding `group_type` doesn't change the cache key or TTL (still 7×DAY).
- **No SCHEMA_VERSION bump.** `group_type` defaults to `None`, so a warm old-shape `categories` cache row deserializes fine — it simply reads as `unknown` type until the next `refresh_cache`. The plan calls this out so a reviewer doesn't mistake it for a migration gap.

---

## 3. Components & data contracts

### 3.1 Query — `GetCategories`
```graphql
query GetCategories {
  categories {
    id
    name
    group { id name type __typename }
    __typename
  }
}
```
`group.type` is Monarch's own enum. From the reference lib and 2026-08-10 live data the values are `income` / `expense` / `transfer`, but **the plan's live-probe (Task 1) must confirm the exact string values before the analytics layer trusts them** (the balance-history / GET_BUDGETS 400 lesson: never lock a query shape or enum from the reference lib without a live probe).

### 3.2 Model — `Category`
```python
class Category(BaseModel):
    id: str
    name: str
    group: str | None = None
    group_type: str | None = None   # Monarch's own: "income" | "expense" | "transfer"
```
Adapter maps `group_type = (c.get("group") or {}).get("type")` — `None` when absent. Backward-compatible.

### 3.3 Analytics — `budget_status`
Each row gains `type` (the joined `group_type`, or `"unknown"` when the category id is absent from the map or `group_type` is null). The flag is relabeled by type:
- **expense** row → `"over_budget": remaining < 0`
- **income** row → `"favorable": remaining < 0` (Monarch `remaining = planned − actual`, so *more income than planned* drives remaining negative — the favorable case; sign confirmed live at build)
- **transfer** row → `"over_budget": remaining < 0` (literal, neutral)
- **unknown** row → `"over_budget": remaining < 0` (literal; we cannot claim favorability without a type)

`totals` splits into one sub-block per type **present in the data** (`expense`, `income`, `transfer`, `unknown`), each summing only its own rows' budgeted/actual/remaining, with the type-appropriate flag (`over_budget` for expense/transfer/unknown; `favorable` for income). A type with zero rows produces **no** sub-block — the caller reads which types exist by which keys appear, and an empty budget yields `totals: {}`. (Rationale: never emit a zeroed `expense` block that could read as "budget met" when no expense budget was set; absence is the honest signal, consistent with how empty windows are handled elsewhere.)

Row sort is unchanged (most-negative `remaining` first) so the worst expense overage still surfaces at the top of the flat list.

### 3.4 Tool output — `budget_status`
```json
{
  "window": { "start": "2026-06-01", "end": "2026-06-30" },
  "categories": [
    { "category": "Groceries", "group": "Food & Dining", "type": "expense",
      "budgeted": "2700", "actual": "2978.02", "remaining": "-278.02", "over_budget": true },
    { "category": "Paychecks", "group": "Income", "type": "income",
      "budgeted": "45000", "actual": "45836", "remaining": "-836", "favorable": true }
  ],
  "totals": {
    "expense":  { "budgeted": "...", "actual": "...", "remaining": "...", "over_budget": true },
    "income":   { "budgeted": "...", "actual": "...", "remaining": "...", "favorable": true },
    "transfer": { "budgeted": "...", "actual": "...", "remaining": "...", "over_budget": false }
  },
  "methodology": "Totals are split by Monarch's own income/expense/transfer category type. For expense categories, over_budget means you spent more than planned. For income, favorable means you earned more than planned — not overspending. Transfers are internal money movement, shown for completeness."
}
```
The freshness envelope wrapping `data` is unchanged. `_jsonify` already stringifies nested Decimals in dicts, so the split totals transport correctly.

---

## 4. Error handling, caching, testing

### Error handling
- `budget_status` keeps its existing date-validation strings (`"start must be YYYY-MM-DD"`, `"end must be YYYY-MM-DD"`, `"start must not be after end"`) and `_guard` wrapping. No new error paths.

### Caching
- No cache-key or TTL change. No SCHEMA_VERSION bump (see §2). A warm old-shape `categories` cache reads `group_type` as `None` (→ `unknown`) until refreshed.

### Testing (offline/deterministic, no live calls in the suite)
- **Adapter:** stub returns categories with `group.type` of `expense`, `income`, `transfer`, and a `group: null` case → assert `group_type` maps each correctly incl. `None`.
- **Analytics (pure) — the core:**
  - expense row emits `over_budget`; income row emits `favorable`; transfer row emits literal `over_budget`.
  - income `favorable = remaining < 0` sign.
  - unknown-type row (id absent from map, or null `group_type`) → `type: "unknown"`, literal `over_budget`, rolled into the `unknown` totals bucket, never dropped.
  - totals split into per-type buckets, each summing only its own type; cross-check a bucket sum.
  - `methodology` string present and byte-exact (locked in the plan).
- **Tool layer:** envelope shape unchanged; `data.totals` exposes the per-type sub-blocks; new fields survive `_jsonify` Decimal→string transport.
- **Live accuracy check (interactive, at build):** re-run June 2026 in Claude Desktop — income rows (Paychecks, Interest) land in the income bucket flagged `favorable`; expenses in the expense bucket; "how much did I overspend" reads cleanly off `totals.expense`. Recorded in this spec on ship.

### Live-verification results (2026-08-11, real org, Claude Desktop) — PASSED
Ran `refresh_cache("categories")` then `budget_status` for June 2026 (2026-06-01 → 2026-06-30):
- **Income block, separated + `favorable`:** budgeted $44,510 / actual $45,403.37 → favorable by ~$893. Paychecks ($35,836.31) and Interest ($85.49) both landed in `totals.income` flagged favorable; Syosset Medical Buyback ($9,481.57, slightly under plan) correctly NOT flagged favorable. Income never mingled into the expense total.
- **Expense block, pure expense:** budgeted $27,281 / actual $34,048.07 → over by **$6,767.07**. This is the number the "how much did I overspend" narration now reads cleanly, with zero income contamination — the trust gap this spec set out to close.
- **Transfer block:** $189.66 actual vs $0 budgeted, shown for completeness (internal movement, not real overspend).
- **`unknown` bucket: empty (the enum probe).** All 107 categories carried a valid `income`/`expense`/`transfer` type, so no `unknown` sub-block appeared at all. This confirms Monarch's real `group.type` enum strings match the reference-lib assumption — the reason Task 1's live probe was safely collapsed into this step.
- **Methodology string** surfaced on the result as designed, so the narration could attribute the split to Monarch's own category type rather than inference.

---

## 5. Known boundaries
- **Type fidelity depends on Monarch's own `group.type`.** A category with no group, or a group Monarch hasn't typed, surfaces as `type: "unknown"` with a literal `over_budget` flag — honest, never guessed.
- **`favorable` is a relabel, not new math.** It is the same `remaining < 0` comparison the tool already computes, named for the income case. No number is invented.
- **Single-month by design** (unchanged from Tier 1): `get_budgets` maps `months[0]`; the split does not change that.

---

## 6. What this enables next
- A trustworthy budget scorecard the `financial_context` resource and TeamFrank-level advisory can lean on without the model re-mingling income into "spend."
- The pattern (surface Monarch's own type + methodology string) is reusable for the second Tier-1 spec (holdings/recurring) if those payloads carry similar semantic labels.
