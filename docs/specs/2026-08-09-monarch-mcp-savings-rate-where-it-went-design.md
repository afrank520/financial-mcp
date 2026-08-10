# Monarch MCP — Savings rate + "where it went" reconciliation (design)

**Date:** 2026-08-09
**Status:** Shipped + live-proven (2026-08-09).
**Roadmap slot:** P1 differentiator (the derived-ratio advisory tool). Builds directly on the shipped balance-history source (`balance_delta`) and a new `get_cashflow` source.

---

## 1. Scope

Build **one new MCP tool, `savings_summary(start, end)`**, that answers two linked questions for a date window:

1. **Savings rate** (the headline) — what share of take-home income was kept, from Monarch's own authoritative cashflow aggregates.
2. **"Where it went"** (the reconciliation) — which accounts absorbed the savings, using the already-shipped `balance_delta`, plus a server-computed net-worth change and an *unattributed* reconciliation gap.

The professional framing: this pairs the two core personal financial statements a CFP reconciles — the **cashflow statement** (income vs. spending) against the **balance sheet** (Δ what you own/owe). The metric a CFP recognizes as "savings rate" is `(income − spending) ÷ income`; the analysis a CFP performs is reconciling that against Δ net worth and naming the gap.

### Why now
The parked NEXT step was "cashflow → savings rate + where-it-went." Balance history shipped 2026-08-08, so the balance half already exists. This spec adds the cashflow half and the tool that unites them.

### In scope
- One new Monarch GraphQL query (`GET_CASHFLOW_SUMMARY`, live-verified 2026-08-09 — see §5).
- A `Cashflow` model + `get_cashflow(start, end)` adapter method (per-window, live, **uncached**).
- A new pure module `analytics/savings.py` (mirroring `analytics/balances.py`) that computes the true signed rate, the scoped net-worth delta, and the gap. Kept separate from `spending.py`/`balances.py` because it composes both.
- **Switch the balance side to `id`-based account keying** (retires the displayName known-boundary — see §3.4 and §6). This is folded into this plan because this is the first feature that *sums* deltas and is therefore the first where the collision is a correctness bug.
- One new MCP tool, `savings_summary`.

### Out of scope (YAGNI)
- Account succession / lineage mapping (old→new "same account" — the NYSaves 529 case). Deferred to the `financial_context` resource + net-worth-trend tools. This spec does not need it (see §3.4).
- Gross (pre-tax) savings rate. Monarch cannot see pre-tax withholding; not inventable. Handled by caveat (§3.3).
- An "adjusted" savings rate that adds retirement contributions back in — rejected: Δ retirement balance blends contributions with market gains, which Monarch can't separate → invented precision.
- Rolling/trend framing, goal-adequacy judgment ("is 26% enough?"). That is the Claude-plus-context judgment layer, not this measurement tool.
- Caching cashflow (per-window; low hit-rate, unbounded growth — fetched live instead).
- Anything that writes (Plan 4).

---

## 2. Architecture

Same one-directional layering as every prior plan; two thin additions, one reuse:

```
Monarch aggregates ─┐
   (NEW query)       ├─► get_cashflow(start,end) ─┐
                     │   (NEW adapter, uncached)   ├─► savings_summary tool (NEW, thin)
balance_history ─────┘                             │
   (SHIPPED, cached) ──► balance_delta() ──────────┘   ← reused; re-keyed on account id
```

- **`queries.py`** — add `GET_CASHFLOW_SUMMARY` (the live-verified `aggregates` query). Also extend `GET_ACCOUNTS_MIN` to return `type { name }` and `includeInNetWorth` so the balance side can scope by account class (§3.2).
- **`models.py`** — add `Cashflow`. Carry an account `id` and classification onto `BalanceSnapshot` (§3.4).
- **Adapter** — add `get_cashflow(start, end)` to the interface + `CommunityAdapter` (one live `aggregates` call, uncached). `CsvAdapter.get_cashflow` raises "not available in CSV mode" (honest; keeps offline tests green).
- **Analytics** — new pure module `analytics/savings.py`: true signed rate; scoped `net_worth_delta`; `reconciliation_gap`; zero-income guard.
- **Tool** — `savings_summary` in `mcp_server.py`: thin, `_guard`- and `_envelope`-wrapped, mirroring `spending_summary`.

**Risk concentration:** the only genuinely new external surface is the `aggregates` query — already probed live (§5). The `id`-re-keying touches shipped code and is the highest-care internal change (§6).

---

## 3. Components & data contracts

### 3.1 Model — `Cashflow` (`models.py`)
```
Cashflow:
    start: datetime.date
    end: datetime.date
    income: Decimal          # sumIncome, as Monarch reports (positive)
    expense: Decimal         # sumExpense, negative as Monarch reports
    savings: Decimal         # Monarch's savings (== income + expense)
    savings_rate_monarch: Decimal   # Monarch's savingsRate, floored at 0
```
`Decimal` throughout for penny-exactness.

### 3.2 Adapter — `get_cashflow(start, end)`
- `CommunityAdapter`: one live `GET_CASHFLOW_SUMMARY` call for the window; maps `summary[0].summary` → `Cashflow`. **Uncached** — the call is lightweight and window-parameterized.
- `CsvAdapter`: raises a clear "cashflow is not available in CSV mode" error (surfaced cleanly by the tool).
- Balance-side scoping data (`type.name`, `includeInNetWorth`) comes from the extended `GET_ACCOUNTS_MIN`, consumed inside `get_balance_history`.

### 3.3 The `savings_summary` tool contract (`mcp_server.py`)
`savings_summary(start: str, end: str)` — YYYY-MM-DD, inclusive; same date-validation error strings as `spending_summary` (`"start must be YYYY-MM-DD"`, `"end must be YYYY-MM-DD"`, `"start must not be after end"`). Returns an `_envelope` whose `data` is:

```json
{
  "window": { "start": "2026-06-01", "end": "2026-06-30" },

  "cashflow": {
     "income": "45976.73",
     "expense": "-34059.93",
     "savings": "11916.80",
     "savings_rate": "0.2592",
     "savings_rate_monarch": "0.2592",
     "rate_is_floored": false
  },

  "where_it_went": {
     "cash_and_debt": [ /* balance_delta rows, accounts summed into the gap */ ],
     "investments_shown_separately": [ /* balance_delta rows, NOT summed */ ]
  },

  "net_worth_delta_cash_debt": "13115.00",
  "reconciliation_gap": "1198.20",
  "excluded_accounts": [ /* names with null delta or excluded from net worth */ ],

  "methodology": "Savings rate is on take-home (net) income; pre-tax 401k/403b/withholding are not in Monarch's cashflow and are excluded from the rate — that saving appears in where_it_went as retirement/brokerage balances rising. The reconciliation compares cash savings against the change in cash and debt account balances only; investment and real-estate balances are shown separately because their changes are mostly market movement, not saving. The remaining gap reflects internal transfers (excluded from cashflow), snapshot timing, and any manual/unlinked cash accounts; it is not attributed to a single cause."
}
```

**Design points:**
- **True signed rate computed by us** (`savings_rate = savings ÷ income`, rounded to 4 dp) *and* Monarch's floored one passed through (`savings_rate_monarch`), with `rate_is_floored = true` when they diverge (Monarch floored a negative rate to 0). Live YTD case: savings −$31,086 → Monarch rate `0.0`, our rate negative, flag true.
- **`net_worth_delta_cash_debt` and `reconciliation_gap` are server-computed** (Σ of Decimal deltas over cash+debt accounts, then one subtraction: `gap = net_worth_delta_cash_debt − savings`). No Claude arithmetic.
- **`methodology` returned every call** — the proactive asterisk.

### 3.4 Balance-side scoping + `id`-keying (the reconciliation-correctness core)

**Account classification (from extended `GET_ACCOUNTS_MIN`):**
- **Summed into the gap** — accounts where balance change ≈ cash movement: `type.name ∈ {depository, credit, loan}` **and** `includeInNetWorth == true`.
- **Shown separately, not summed** — `type.name ∈ {brokerage, real_estate}` (market-driven) — so retirement/529/ESPP/brokerage rises stay *visible* (the advisor's "retirement saving is real" point) without polluting the gap with market noise.
- **Excluded entirely** — `includeInNetWorth == false` (old/duplicate 529s, corporate cards, one student loan) — matches Monarch's own net-worth figure, and drops the duplicate-displayName accounts.

**`id`-keying:** `BalanceSnapshot` and `balance_delta` currently key on `displayName`. The live probe (2026-08-09) confirmed **duplicate display names exist** (Braden/Colton/Dylan/Elaina 529 each appear twice — old NYSaves + current). Summing deltas by name would silently merge two different accounts. Fix: carry account `id` (already fetched in `GetAccountsMin`) onto `BalanceSnapshot` and key the balance analytics on `id`, using `displayName` only as a human label in output. This retires the displayName known-boundary from balance-history. `balance_as_of` / `balance_delta` output keeps a `display_name` field for readability but joins on `id`.

**Null-delta accounts** (missing snapshot at an endpoint) are **never silently dropped from the sum** — they are excluded from `net_worth_delta_cash_debt` **and listed by name in `excluded_accounts`** with the reason, so the sum is honest.

---

## 4. Error handling, caching, testing

### Error handling (reuses existing typed-error plumbing)
- `SessionExpiredError` / `CloudflareBlockedError` → `_guard` → standard "re-run `financial-mcp-login`" message.
- Bad/reversed dates → same exact strings as `spending_summary`.
- **Zero income in window** → `savings_rate` = `null` + a `rate_reason` ("no income in window; rate undefined"), never a divide-by-zero.
- CSV mode → `get_cashflow` raises "not available in CSV mode"; tool surfaces cleanly.

### Caching
- **Cashflow: not cached.** One lightweight live `aggregates` call per invocation; window-parameterized, so a per-window cache would rarely hit and could grow unbounded. (The pre-existing unused `CacheTTLs.cashflow` slot stays unused for now.)
- **Balance side: reuses the cached `balance_history`** (the expensive 27K-snapshot fan-out already cached under `balance_history`, TTL = DAY).
- **Freshness envelope** reports the freshness of `balance_history` — the cached, staleness-prone input. (`refresh_cache` already covers `balance_history`; cashflow is always live so needs no refresh path.)

### Testing (offline/deterministic, mirrors balance-history)
- **Adapter:** stub transport returns canned `aggregates` JSON → correct `Cashflow` mapping; the **floored-rate case** (negative savings → Monarch `0.0`, our signed rate negative, `rate_is_floored=true`); `CsvAdapter.get_cashflow` raises. Extended `GET_ACCOUNTS_MIN` mapping (type/includeInNetWorth carried through).
- **Analytics (pure):** true signed rate (`savings ÷ income`, quantized to 4 decimal places with `Decimal`, e.g. live `0.2591919…` → `0.2592`); **zero-income guard** (null + reason); `net_worth_delta_cash_debt` sums only cash+debt+includeInNetWorth accounts; investment accounts excluded from sum but present in `investments_shown_separately`; null-delta account excluded + named in `excluded_accounts`; `reconciliation_gap` subtraction; **`id`-keying** (two accounts sharing a displayName are NOT merged; renamed account across window still one id).
- **Tool:** envelope shape, date validation, `methodology` present, `_guard` on expired session.
- No live calls in the suite.

---

## 5a. Shipped live-verification (2026-08-09, post-build, in Claude Desktop against the real synced org)

**PASSED — penny-perfect against the Monarch dashboard (Goal #4 ground truth).**

- **June 2026** ("What was my savings rate in June 2026?"): `savings_rate` **0.2592 (25.9%)**, `rate_is_floored: false`, methodology surfaced. Cross-checked against the Monarch Cash Flow dashboard for June 2026: income **$45,976.73** (dashboard $45,977), expense **−$34,059.93** ($34,060), total savings **$11,916.80** ($11,917), savings rate **25.9%** — exact match. Reconciliation gap ≈ **$3,566**, correctly framed as internal transfers / snapshot timing (not attributed to a single cause).
- **YTD 2026 Jan–Jun** ("...and where did it go?"): net-positive window, `savings_rate` **0.039 (3.9%)**, $9,880.64 saved on $251,337.83 take-home. The "where it went" reconciliation worked end-to-end: debt paydown itemized per account (mortgage −$12,703, SoFi −$1,859, Optima cleared −$3,746, HELOC +$28,959 draw), ~$34K of kids'/cash transfers correctly attributed to balance deltas (excluded from the cashflow rate), retirement (401k +$78,763) and real-estate (+$168,700) **shown separately, not summed**, gap ≈ **$15,481** named, and the 529s + Amex Gold correctly flagged in `excluded_accounts` for a missing January baseline (null-delta → listed, not silently dropped). **No duplicate-displayName account double-counted** — the id-keying migration is proven live.
- The floored-negative-rate path was not exercised in this run (the model chose a net-positive Jan–Jun window) but is covered by unit test `test_floored_negative_rate_flagged`; the §5.1 pre-build probe confirmed the live floored behavior directly.

## 5. Live verification (done 2026-08-09, before design lock)

Two probes were run against the real org.

### 5.1 Cashflow — `GET_CASHFLOW_SUMMARY` (confirmed)
```graphql
query Web_GetCashFlowPage($filters: TransactionFilterInput) {
  summary: aggregates(filters: $filters, fillEmptyValues: true) {
    summary { sumIncome sumExpense savings savingsRate __typename }
    __typename
  }
}
```
- **Operation name:** `Web_GetCashFlowPage` (must match the document, per the balance-history 400 lesson).
- **Variables:** `{"filters": {"search":"", "categories":[], "accounts":[], "tags":[], "startDate": "YYYY-MM-DD", "endDate": "YYYY-MM-DD"}}`.
- **JSON path:** `data.summary[0].summary` → `{sumIncome, sumExpense, savings, savingsRate}`.
- **Live results:** June 2026 → income `45976.73`, expense `−34059.93`, savings `11916.80`, savingsRate `0.2592` (math checks: savings = income + expense; rate = savings ÷ income). **2026 YTD → savings `−31086.46` but savingsRate `0.0`** — confirms Monarch floors negative rates at 0 (the reason we compute our own signed rate + `rate_is_floored`).
- This is **net (take-home)** income and is **transfer-aware** (Monarch excludes transfers) — authoritative; matches the Monarch dashboard, which is the accuracy-test ground truth (Goal #4).

### 5.2 Account types — `GET_ACCOUNTS.type/subtype/includeInNetWorth` (confirmed clean)
40 accounts classify cleanly: `depository` ×10, `credit` ×7, `loan` ×4 (→ cash+debt), `brokerage` ×18, `real_estate` ×1 (→ shown separately). `includeInNetWorth` cleanly flags the old/duplicate 529s and corporate cards as excluded. **Duplicate displayNames confirmed** (the 529 succession case) — the evidence behind the `id`-keying fix (§3.4).

---

## 6. Known boundaries & the `id`-keying migration

- **`id`-keying is a change to shipped code.** `BalanceSnapshot`, `balance_as_of`, `balance_delta`, their tests, and the adapter mapping all move from displayName to id (displayName retained as a label). This is the highest-care task; its reviewer must confirm the two shipped tools (`balance_as_of`, `balance_delta`) still return correct, now-id-keyed output and that no caller broke.
- **Reconciliation gap is arithmetically exact but conceptually bounded:** even scoped to cash+debt, the gap still contains internal transfers (excluded from cashflow) and snapshot-timing effects. The `methodology` note states this; the tool never attributes the gap to a single cause.
- **Savings rate is a point measurement over the given window.** Bonus/vest months spike it, big-purchase months crater it (live: June 26% vs. YTD floored-negative). Trend/rolling framing is the judgment layer's job, not this tool's.
- **Account succession/lineage** (NYSaves 529) deferred — see the `account-succession-mapping-future` memory and the `financial_context` resource plan.

---

## 7. What this enables next
- **HELOC glide-path checkpoint tracking** — `balance_as_of` (now id-keyed) at each checkpoint date.
- **Net-worth-trend tool** — the natural home for account-succession mapping.
- **The `financial_context` resource** — house-rules/targets/lineage that let Claude sustain TeamFrank-level guidance on top of these honest numbers.
