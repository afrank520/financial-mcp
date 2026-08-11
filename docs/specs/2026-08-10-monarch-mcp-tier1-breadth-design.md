# Monarch MCP — Tier 1 breadth: net-worth trend + budgets + real categories (design)

**Date:** 2026-08-10
**Status:** ✅ Shipped + live-proven (2026-08-10). All three tools verified in Claude Desktop against the real org — see §8.
**Roadmap slot:** Goal #2 (bring in the full Monarch capability menu), Tier 1. First of two Tier-1 breadth specs; holdings + recurring/subscriptions are the deferred second spec. Builds on the shipped adapter → analytics → tool layering and the id-keyed balance foundation.

---

## 1. Scope

Three read-only capabilities, each following the proven `model → query → adapter → cached DataService → pure analytics → MCP tool` layering:

1. **Net-worth trend** — Monarch's own net-worth time series for a window: net worth at start, at end, the change, and the raw monthly points. The headline line from the Monarch net-worth graph.
2. **Budgets** — per-category budgeted vs. actual for a month window (the red/green scorecard), plus rolled-up totals.
3. **Real categories (group rollup)** — enrich the shipped `category_summary` with each category's Monarch **group**, and add group-level subtotals. Backward-compatible enrichment, not a rewrite.

### Why these three, now
Goal #2 is "bring in the full menu." Of the five Tier-1 entities, these three form one coherent "financial scorecard + trend" theme and are the two that feed the north star (net-worth trend → the HELOC glide-path tool that comes next; budgets → the monthly red/green scorecard the TeamFrank plan runs on), plus the categories upgrade that makes every spending lens more trustworthy. Holdings and recurring/subscriptions are real but serve completeness over the north star, so they are a separate follow-up spec.

### In scope
- Three new Monarch GraphQL queries: `GetAggregateSnapshots`, `GetJointPlanningData` (budgets), `GetCategories`.
- Three new models: `NetWorthSnapshot`, `BudgetItem`, `Category`.
- Three new adapter methods (all single-call, cached): `get_networth_history(start, end)`, `get_budgets(start, end)`, `get_categories()`. `CsvAdapter` raises "not available in CSV mode" for each (honest; keeps offline tests green).
- Two new pure analytics modules: `analytics/networth.py`, `analytics/budgets.py`. Category group rollup extends the existing `analytics/spending.py`.
- Two new date-window MCP tools: `networth_trend(start, end)`, `budget_status(start, end)`. One shipped tool upgraded: `category_summary` (return shape changes — see §3.3).

### Out of scope (YAGNI)
- **Holdings** (`get_account_holdings`) and **recurring/subscriptions** (`get_recurring_transactions`) — deferred to the second Tier-1 spec.
- **Assets/liabilities split on net-worth trend.** The base `aggregateSnapshots` query returns net worth per date only; splitting would require 3 accountType-filtered calls. Rejected as YAGNI: the "why did net worth change (assets vs. debt)" question is already answerable via the shipped `balance_delta` / `savings_summary` "where it went" lens. Net-worth trend's unique job is the headline line over time.
- **Flex/rollover re-derivation.** Monarch's budget payload already computes planned/actual/remaining per category (including rollover); we pass those through as-is. Re-deriving rollover math is the "invented precision" trap avoided in the savings spec.
- **All writes** (Plan 4).
- **The HELOC glide-path tool** — the *next* build; it stands on the net-worth source this spec delivers.

---

## 2. Architecture

Same one-directional layering as every prior plan. All three sources are single live calls (unlike balance_history's per-account fan-out), so caching is simple — each flows through the existing cached `DataService._get` path.

```
GetAggregateSnapshots ─► get_networth_history(start,end) ─► networth_trend tool (NEW)
GetCategories ──────────► get_categories() ──────┬────────► category_summary (UPGRADED: group rollup)
                                                 └────────► (id→name/group map consumed by budgets)
GetJointPlanningData ───► get_budgets(start,end) ─────────► budget_status tool (NEW)
```

- **Internal dependency:** the budgets payload returns categories **by id only** (`monthlyAmountsByCategory.category.id`), so `budget_status` joins budget rows against the categories map (id → name, group) to produce human-readable rows. Build the categories capability first; budgets consumes it.
- **Caching:** `networth` and `budgets` cached at TTL = DAY; `categories` at TTL = 7×DAY (rarely changes). All slots already exist in `CacheTTLs`. These are new cached types — no re-keying of existing types — so the SCHEMA_VERSION cache-migration safety added for the savings work is exercised but no migration risk arises here.
- **Freshness envelope:** each tool reports the freshness of its backing cache, as every data tool does.

---

## 3. Components & data contracts

### 3.1 Net-worth trend

```
NetWorthSnapshot (models.py):
    date: datetime.date
    net_worth: Decimal        # aggregateSnapshots.balance — Monarch's net worth at that date
```

- **Query `GetAggregateSnapshots`:** `query GetAggregateSnapshots($filters: AggregateSnapshotFilters) { aggregateSnapshots(filters: $filters) { date balance __typename } }`, variables `{"filters": {"startDate": "YYYY-MM-DD", "endDate": "YYYY-MM-DD", "accountType": null}}`. JSON path: `data.aggregateSnapshots` → list of `{date, balance}`. `balance` maps to `net_worth`.
- **Adapter `get_networth_history(start, end)`** (CommunityAdapter): one live call, filtered to the window; maps each point to `NetWorthSnapshot`. Cached under `networth`. `CsvAdapter` raises "not available in CSV mode".
- **Analytics `networth_trend(snapshots, start, end)`** (`analytics/networth.py`, pure): picks the first and last points in the window, computes `change = end.net_worth − start.net_worth` (server-computed Decimal, no Claude arithmetic), returns endpoints + change + all points chronological. Empty window → null endpoints + a `reason` ("no net-worth snapshots in window").
- **Tool `networth_trend(start, end)`** `data`:
```json
{
  "window": { "start": "2026-01-01", "end": "2026-06-30" },
  "start_point": { "date": "2026-01-01", "net_worth": "..." },
  "end_point":   { "date": "2026-06-30", "net_worth": "..." },
  "change": { "net_worth": "..." },
  "points": [ { "date": "...", "net_worth": "..." } ]
}
```

### 3.2 Budgets

```
BudgetItem (models.py):
    category: str             # joined from the categories map via category id
    group: str | None         # category's group name, if known
    budgeted: Decimal         # Monarch's planned amount, as-is
    actual: Decimal           # Monarch's actual amount, as-is (sign per Monarch)
    remaining: Decimal        # Monarch's remainingAmount, as-is
    over_budget: bool         # remaining < 0
```

- **Query `GetJointPlanningData`** (operation name must match the document): pulls `budgetData(startMonth, endMonth) { monthlyAmountsByCategory { category { id } monthlyAmounts { month plannedCashFlowAmount actualAmount remainingAmount } } }`. Variables include `startDate`, `endDate`, `useLegacyGoals: false`, `useV2Goals: true`. JSON path: `data.budgetData.monthlyAmountsByCategory`. (The payload also offers `monthlyAmountsByCategoryGroup`; not requested — group-level budget rollups are YAGNI for the approved output shape and can be added later.)
- **Adapter `get_budgets(start, end)`** (CommunityAdapter): one live call for the month window; maps `monthlyAmountsByCategory` rows → `BudgetItem` (planned→budgeted, actual→actual, remaining→remaining), joining `category.id` against the categories map for name+group. Cached under `budgets`. `CsvAdapter` raises "not available in CSV mode".
- **Analytics `budget_status(items, start, end)`** (`analytics/budgets.py`, pure): sorts categories worst-over-budget first (`remaining` ascending); rolls up `totals` = Σ budgeted, Σ actual, Σ remaining, `over_budget` = total remaining < 0. The `group` field on each row (from the categories join) lets Claude group by theme without a separate budget-group fetch. Empty (no budget set) → empty `categories` + zeroed totals, never a crash.
- **Tool `budget_status(start, end)`** `data`:
```json
{
  "window": { "start": "2026-06-01", "end": "2026-06-30" },
  "categories": [ { "category": "Groceries", "group": "Food & Dining", "budgeted": "...", "actual": "...", "remaining": "...", "over_budget": true } ],
  "totals": { "budgeted": "...", "actual": "...", "remaining": "...", "over_budget": false }
}
```

### 3.3 Real categories (group rollup)

```
Category (models.py):
    id: str
    name: str
    group: str | None         # group.name from the payload
```

- **Query `GetCategories`:** `query GetCategories { categories { id name group { id name } } }`. JSON path: `data.categories`.
- **Adapter `get_categories()`** (CommunityAdapter): one live call; maps each to `Category`. Cached under `categories` (TTL 7×DAY). `CsvAdapter` raises "not available in CSV mode".
- **Analytics — extend `spending.category_summary`:** the existing per-category totals join to a `name → group` map built from `get_categories()`. Each category row gains a `group` field; a new `groups` section subtotals by group. A transaction whose category name has **no match** in the map (renamed/deleted category) gets `group: null` on its row and rolls into an `"Uncategorized"` group bucket — never silently dropped (same honesty rule as savings' `excluded_accounts`).
- **Return-shape change (intentional, must be tested):** `category_summary`'s tool `data` changes from a bare list to an object. The freshness envelope is unchanged; Claude reads `data.categories` instead of `data`:
```json
{
  "categories": [ { "category": "Groceries", "group": "Food & Dining", "total": "-1234.56", "count": 42 } ],
  "groups":     [ { "group": "Food & Dining", "total": "-1800.00", "count": 63 } ]
}
```
  The tool now needs the categories map, so `category_summary` gains a dependency on `svc.categories()` alongside `svc.transactions()`.

---

## 4. Error handling, caching, testing

### Error handling (reuses existing typed-error plumbing)
- `SessionExpiredError` / `CloudflareBlockedError` → `_guard` → standard "re-run `financial-mcp-login`" message, on all three tools.
- Date-window tools (`networth_trend`, `budget_status`) use the exact same validation strings as `savings_summary`: `"start must be YYYY-MM-DD"`, `"end must be YYYY-MM-DD"`, `"start must not be after end"`.
- CSV mode → each new adapter method raises "... not available in CSV mode"; tool surfaces cleanly.
- Empty windows → honest empty result (null endpoints + reason for net-worth; empty categories + zeroed totals for budgets), never a crash.

### Caching
- `networth`, `budgets`: TTL = DAY. `categories`: TTL = 7×DAY. All via `DataService._get` (cached path), unlike cashflow's uncached passthrough — these are bounded (windowed or single snapshot), not unbounded-per-window.
- Freshness envelope reports each tool's backing cache. `refresh_cache` extended to cover the three new types.

### Testing (offline/deterministic, mirrors every prior plan — no live calls in the suite)
- **Adapter:** stub transport returns canned JSON for each query → correct model mapping; edge cases (missing `group`, null amounts, category id absent from the map); each CSV method raises.
- **Analytics (pure):** net-worth endpoints + change (Decimal-exact); empty-window null+reason. Budget remaining/over-flag + totals rollup; empty-budget zeroed totals; worst-first sort. Category group rollup incl. the `Uncategorized` bucket for unmatched names; group subtotals sum correctly.
- **Tools:** envelope shape; date validation strings; `_guard` on expired session; the `category_summary` object-shape change (assert `data.categories` and `data.groups`, and that existing per-category rows retain `category`/`total`/`count` plus the new `group`).
- **Live accuracy check (goal #4, at implementation time):** each tool cross-checked against the Monarch UI — net-worth graph, budget screen, category totals — penny/point-for-point. Recorded in this spec on ship.

---

## 5. Live verification (to run before design lock is fully closed / at build)

Query shapes above are drawn from the retained `monarchmoney` reference lib (the GraphQL catalog kept in `.venv`), not yet re-probed live against the current API. Per the balance-history 400 lesson, operation names must match the query document exactly. At build time (Task 1, like prior plans), probe each query live against the real org and record the confirmed JSON paths + one sample payload here before the adapter work proceeds.

---

## 6. Known boundaries
- **Net-worth trend is net worth only** — no assets/liabilities split (see §1 out-of-scope). The "why it changed" question routes to `balance_delta` / `savings_summary`.
- **Budget numbers are Monarch's** — planned/actual/remaining passed through as-is, including whatever rollover Monarch baked in. The tool does not re-derive flex/rollover.
- **Category group rollup depends on name-matching** transactions to the categories map. Renamed/deleted categories surface as `Uncategorized`, honestly, rather than being dropped or guessed.

---

## 7. What this enables next
- **HELOC glide-path checkpoint tracking** — stands on `get_networth_history` (this spec) + the id-keyed `balance_as_of` already shipped.
- **The `financial_context` resource** — house-rules/targets/lineage that let Claude sustain TeamFrank-level guidance on top of these honest numbers (the north star's second half).
- **Second Tier-1 spec** — holdings + recurring/subscriptions, completing the read-only menu.

---

## 8. Live verification (2026-08-10, Claude Desktop, real org) — Shipped

All three tools verified against the real Monarch org. 182 offline tests green.

- **`networth_trend`** — penny-perfect vs the Monarch net-worth graph: end $2,122,088.91, YTD change +$499,163.10. Net worth only, as designed; the tool proactively offered `balance_delta`/`savings_summary` for the "why."
- **`budget_status`** (June 2026) — real per-category table, `over_by = actual − budgeted` penny-exact on every spot-checked row (Capital Improvement 2,604.56; Lawn & Garden 1,386.00; Ice Hockey 1,320.00; Shopping 965.18; Mortgage 0.13).
- **`category_summary`** — all-time net-by-group returned correctly; net signed totals behave as designed (an inflow can net a group down, e.g. Misc +81,833 nets against Camp Tuition inside "Other" — honest, not a bug).

**Bug caught + fixed during verification (the deferred-Task-1 risk materialized):** `budget_status` first returned HTTP 400 ("line 2, column 66"). Root cause: `GET_BUDGETS` declared `$useLegacyGoals`/`$useV2Goals` but the slimmed query body (the `goals @include(if:…)` sections were dropped as YAGNI per §3.2) never referenced them — GraphQL rejects any operation with an unused declared variable, pointing at the first offender's column. Fixed by dropping both declarations and no longer passing them (commit `1b69705`); a regression test asserts the variable set is exactly `{startDate, endDate}` and the flags are absent from the query document. Same family as the balance-history 400 lesson: a parse-location 400 means Monarch is rejecting our query document, never connectivity.

**Known boundary logged for follow-up (data is trustworthy; narration is the gap):** `budget_status`'s `totals` block sums income budget rows and expense budget rows together, so a "spent $X" narration can fold income actuals in with expenses. Every tool number is Monarch's own and correct; the mingling is the boundary. Planned fix (own spec): surface Monarch's own `group.type` (income/expense/transfer), split totals into `expense_totals` + `income_totals`, and add a `methodology` string (the `savings_summary` pattern).
