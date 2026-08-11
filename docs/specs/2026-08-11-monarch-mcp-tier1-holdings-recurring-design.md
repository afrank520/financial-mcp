# Monarch MCP — holdings + recurring/subscriptions (design)

**Date:** 2026-08-11
**Status:** Draft (design lock pending user review).
**Roadmap slot:** Second and final Tier-1 breadth spec (completes goal #2). Follows the shipped Tier-1 breadth spec (`2026-08-10-monarch-mcp-tier1-breadth-design.md`) and the budget income/expense split (`2026-08-11-monarch-mcp-budget-income-expense-split-design.md`). Next after this: the `financial_context` resource, then the HELOC glide-path checkpoint.

---

## 1. Scope

Two new read-only MCP tools — `holdings_summary` and `recurring_summary` — built holdings-first in one branch. They are genuinely independent (separate queries, models, and analytics; nothing shared), but share the delivery machinery and one live-verify session, so they ship together to finish Tier-1 breadth in a single landing.

Both follow the proven one-directional layering: Monarch GraphQL → `MonarchAdapter` → `DataService` (+ cache) → pure analytics → MCP tool.

### In scope
- **Holdings:** new `Web_GetHoldings` query (per-account fan-out), `Holding` model, `get_holdings()` adapter method, `holdings_summary` analytics + tool. Lists each investment position with Monarch's own quantity / price / value / cost basis / day-change, plus portfolio totals and a per-position `gain_loss` (value − cost basis).
- **Recurring:** new `Web_GetUpcomingRecurringTransactionItems` query (single date-bounded call), `RecurringItem` model, `get_recurring(start, end)` adapter method, `recurring_summary` analytics + tool. Lists upcoming/past recurring items in a window with a `total_expected` sum.
- A `methodology` string on each tool result (the proven `savings_summary` / budget-split "proactive asterisk" pattern).
- CSV adapter + all test fakes updated to satisfy the extended `MonarchAdapter` contract.

### Out of scope (YAGNI)
- **Holdings:** no unrealized gain/loss *percent* (`gain/basis`) and no allocation percent (position ÷ portfolio). Both are derived ratios one step past the proven "arithmetic on Monarch's own numbers" line; they earn a later spec with their own methodology string if wanted. The tool surfaces gain_loss dollars + totals, so the model can still answer "what's my return %" with all source numbers visible.
- **Recurring:** no monthly-normalized estimate (weekly×4.33, annual÷12). That is an invented normalization; `total_expected` is a raw sum only.
- No writes (Plan 4). No changes to any existing tool or model.

### Honors the standing constraint
Every value is Monarch's own or a plain sum/subtraction of Monarch's own numbers — the exact precedent set by `budget_status`'s `over_budget` flag and split totals. `gain_loss = value − cost_basis` and the portfolio/`total_expected` sums invent nothing; they aggregate fields Monarch already stores.

---

## 2. Architecture

```
Web_GetHoldings (fan-out over investment accounts) ─► Holding ─► holdings_summary ─► holdings_summary tool
   (accountIds; same fan-out shape as get_balance_history)         (positions + totals + gain_loss $)

Web_GetUpcomingRecurringTransactionItems (one call) ─► RecurringItem ─► recurring_summary ─► recurring_summary tool
   (startDate/endDate; same shape as get_cashflow)                     (windowed list + total_expected)
```

- **Holdings** fans out: the `Web_GetHoldings` query takes `accountIds`, so the adapter first reads the accounts list, selects accounts with `holdingsCount > 0` (a field already present in `GET_ACCOUNTS`), and calls `Web_GetHoldings` once per such account — the same fan-out the proven `get_balance_history` uses for per-account snapshots.
- **Recurring** is a single date-bounded query, so `get_recurring(start, end)` mirrors `get_cashflow(start, end)` and the tool takes a `start`/`end` window like the other date-bounded tools.
- **No `SCHEMA_VERSION` bump.** Both are new cache/model types, not changes to existing cached shapes.

---

## 3. Components & data contracts

### 3.1 Holdings

**Query — `Web_GetHoldings`** (from monarchmoney v0.1.15 reference lib; live-probe at build confirms field availability):
```graphql
query Web_GetHoldings($input: PortfolioInput) {
  portfolio(input: $input) {
    aggregateHoldings {
      edges {
        node {
          id
          quantity
          basis
          totalValue
          securityPriceChangeDollars
          securityPriceChangePercent
          security {
            id name type ticker typeDisplay
            currentPrice closingPrice
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
```
Variables: `{"input": {"accountIds": [<id>], "startDate": <today>, "endDate": <today>, "includeHiddenHoldings": true}}`.

**Model — `Holding`**
```python
class Holding(BaseModel):
    account: str                 # displayName of the investment account
    ticker: str | None           # None for manual/unlisted holdings
    name: str                    # security name
    quantity: Decimal
    price: Decimal               # Monarch's currentPrice, falling back to closingPrice
    value: Decimal               # Monarch's own totalValue (pass-through, NOT price*qty)
    cost_basis: Decimal | None   # Monarch's basis; None when Monarch has none
    day_change_dollars: Decimal | None   # Monarch's securityPriceChangeDollars
    day_change_percent: Decimal | None   # Monarch's securityPriceChangePercent
```
Mapping is null-safe throughout: `ticker`, `cost_basis`, and both day-change fields may be null; `price` uses `currentPrice` when present else `closingPrice` (both Monarch's own — a fallback selection, not a computation); `value` passes through Monarch's `totalValue` verbatim.

**Adapter — `get_holdings() -> list[Holding]`**
Reads accounts (via the existing accounts query), selects those with `holdingsCount > 0`, calls `Web_GetHoldings` per account, maps each `aggregateHoldings.edges[].node` to a `Holding` tagged with the account's `displayName`. An account that returns no holdings contributes nothing (mirrors `get_balance_history` skipping snapshot-less accounts).

**Analytics — `holdings_summary(holdings) -> dict`**
```json
{
  "positions": [
    { "account": "Brokerage", "ticker": "VTI", "name": "Vanguard Total Stock Market ETF",
      "quantity": "120.5", "price": "268.40", "value": "32341.20", "cost_basis": "25000.00",
      "gain_loss": "7341.20", "day_change_dollars": "180.75", "day_change_percent": "0.56" }
  ],
  "totals": { "value": "...", "cost_basis": "...", "gain_loss": "..." },
  "methodology": "Values, cost basis, and day-change are Monarch's own. gain_loss is value minus cost basis (Monarch's own numbers); positions with no cost basis are excluded from the cost-basis and gain_loss totals but still counted in total value."
}
```
- `gain_loss = value − cost_basis` per position, both Monarch's own. The `gain_loss` key is always present; it is `null` when `cost_basis is None` (never omitted, never guessed).
- `totals` are plain sums. Positions without a cost basis (e.g. manual holdings) count toward total `value` but are excluded from total `cost_basis` and total `gain_loss`, so the totals never imply a basis Monarch does not have. The methodology string states this.
- Positions sorted by `value` descending (largest first).
- Empty portfolio → `positions: []`, `totals: {}` (the budget-empty precedent — no misleading zeroed block). Methodology still present.

### 3.2 Recurring

**Query — `Web_GetUpcomingRecurringTransactionItems`** (from monarchmoney v0.1.15 reference lib):
```graphql
query Web_GetUpcomingRecurringTransactionItems($startDate: Date!, $endDate: Date!, $filters: RecurringTransactionFilter) {
  recurringTransactionItems(startDate: $startDate, endDate: $endDate, filters: $filters) {
    stream {
      id frequency amount isApproximate
      merchant { id name __typename }
      __typename
    }
    date isPast transactionId amount amountDiff
    category { id name __typename }
    account { id displayName __typename }
    __typename
  }
}
```
Variables: `{"startDate": <iso>, "endDate": <iso>, "filters": null}`. Per the build live-probe, confirm the query is accepted when `filters` is null/omitted; if the API rejects an unused declared `$filters` variable (the GET_BUDGETS 400 lesson), drop the `$filters` declaration and the `filters:` argument.

**Model — `RecurringItem`**
```python
class RecurringItem(BaseModel):
    date: datetime.date          # this instance's date
    merchant: str
    amount: Decimal              # this instance's amount (Monarch's own, signed)
    frequency: str | None        # stream frequency: "monthly" | "weekly" | ...
    account: str | None          # displayName being charged
    category: str | None
    is_past: bool                # Monarch's isPast — already occurred in this window
```
Null-safe mapping: `frequency` from `stream.frequency`; `merchant` from `stream.merchant.name`; `account`/`category` may be null.

**Adapter — `get_recurring(start, end) -> list[RecurringItem]`**
Single `Web_GetUpcomingRecurringTransactionItems` call, null-safe mapping of each item.

**Analytics — `recurring_summary(items, start, end) -> dict`**
```json
{
  "window": { "start": "2026-06-01", "end": "2026-06-30" },
  "items": [
    { "date": "2026-06-03", "merchant": "Netflix", "amount": "-15.99", "frequency": "monthly",
      "account": "Credit Card", "category": "Entertainment", "is_past": true }
  ],
  "total_expected": "...",
  "methodology": "Amounts and frequencies are Monarch's own recurring-stream data. total_expected sums the amounts of all recurring items in the window (Monarch's own per-instance amounts); it is a sum, not a normalized monthly estimate."
}
```
- `total_expected` = plain sum of item amounts in the window.
- Items sorted by `date` ascending (chronological — what is hitting next).
- Empty window → `items: []`, `total_expected: "0"`. Methodology still present.

### 3.3 Tools (`mcp_server.py`)
- `holdings_summary()` — no args; wraps analytics in `_guard` + `_envelope("holdings", ...)`.
- `recurring_summary(start: str, end: str)` — date-bounded; reuses the shared date-validation strings; wraps in `_guard` + freshness envelope. Uncached pass-through per window.
- Both docstrings describe the pass-through nature, the honesty calls (basis-less positions; `total_expected` is a raw sum), and that a `methodology` string travels with the result.
- `_jsonify` already stringifies nested Decimals in dicts and lists, so both nested shapes transport correctly.

---

## 4. Error handling, caching, testing

### Error handling
- Both tools wrap in `_guard(fn)` → `SessionExpiredError`/`CloudflareBlockedError` become the friendly re-login message. No new error paths.
- `recurring_summary` reuses the existing date-validation strings verbatim: `"start must be YYYY-MM-DD"`, `"end must be YYYY-MM-DD"`, `"start must not be after end"`.
- `holdings_summary` takes no args. Accounts with no holdings contribute nothing; empty portfolio → `positions: []`, `totals: {}`.

### Caching
- **Holdings:** own cache type `holdings`, TTL **1 hour** (prices move intraday; 1h is fresh enough for advice, cheap enough to not hammer Monarch). `refresh_cache("holdings")` forces a re-pull. Add `Holding` to the `DataService._get` TypeVar tuple and register the cache type/TTL.
- **Recurring:** date-bounded → **uncached pass-through** per window (same decision as `date_range_summary` / `budget_status`; caching one specific range has little reuse value). No cache type, no TTL.
- Freshness envelope wraps both results; holdings reports `as_of`/`age_hours` from its cache, recurring reports fresh-each-call.
- No `SCHEMA_VERSION` bump — both are new types.

### Testing (offline/deterministic, no live calls in the suite)
- **Adapter — holdings:** stub transport returns holdings across ≥2 accounts including a null-ticker + null-basis position and a null day-change → assert mapping including the `None`s and the `currentPrice`→`closingPrice` fallback; assert the fan-out selects only `holdingsCount > 0` accounts.
- **Adapter — recurring:** stub returns items with a null account/category and both `isPast` values → assert mapping; assert the operation name and start/end variables; if the null-`filters` probe forces dropping `$filters`, assert `"filters" not in query`.
- **Analytics — holdings:** `gain_loss = value − cost_basis`; basis-less position excluded from `cost_basis`/`gain_loss` totals but counted in total `value`; empty → `totals: {}`; sort by value desc; methodology byte-exact.
- **Analytics — recurring:** `total_expected` sums window amounts; sort by date asc; empty window → `total_expected: "0"`; methodology byte-exact.
- **Tool layer:** envelope shape for both; nested Decimals survive `_jsonify` → string; both docstrings describe pass-through + honesty calls + methodology.
- **Live verify (interactive, at build):** in Claude Desktop against the real org — holdings total value vs Monarch's Investments UI + a couple of positions' `gain_loss`; recurring list for a month vs Monarch's Recurring page. This is also the field/enum probe: confirms `totalValue`/`basis` come through, `frequency` strings, that `holdingsCount` reliably identifies investment accounts, and that the recurring query is accepted with a null `filters`. Recorded in this spec on ship.

---

## 5. Known boundaries
- **Cost-basis-less holdings.** Manual or unlinked positions may have no `basis`; they count toward total value but not toward total cost basis / gain_loss, and carry no per-position `gain_loss`. Honest by construction — never a guessed basis.
- **`total_expected` is a window sum, not a run-rate.** A one-month window sums that month's recurring items; it is not normalized across frequencies. A "true monthly subscription burden" figure would be a derived normalization (out of scope).
- **Holdings snapshot, not history.** `holdings_summary` is the current portfolio, not a time series. Position history would route through a future tool (analogous to `balance_history` for accounts).
- **Fan-out cost.** Holdings issues one query per investment account. For this household that is a small number; if it ever grows large, a batched query is a later optimization.

---

## 6. What this enables next
- Completes Tier-1 breadth (goal #2): the advisor can now speak to investments and recurring obligations, not just cashflow/budgets/net worth.
- Holdings totals + recurring burden are inputs the `financial_context` resource and TeamFrank-level advisory can lean on.
- The pass-through + methodology pattern, proven a third time here, is the standing template for any future Monarch entity.
