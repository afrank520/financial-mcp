# Monarch MCP — Balance History source + as-of / delta tools (design)

**Date:** 2026-08-08
**Status:** Design approved, pending spec review
**Roadmap slot:** P2 "balance history / net-worth trend" (from Plan 3.0 North Star map). Foundation for the P1 savings-rate differentiator.

---

## 1. Scope

This plan builds the **balance-history data source** and its **two read tools**. It does **not** build savings rate.

### Why this exists / why now

The parked NEXT step was "cashflow → savings rate," with a "where the money went" breakdown. Brainstorming (2026-08-08) surfaced that the breakdown Adam actually wants is a **balance-delta reconciliation** — "the HELOC dropped $X, the brokerage rose $Y between date A and date B" — which requires **per-account balance history that the server does not currently pull.** Today we fetch only `currentBalance`: a single point-in-time number per account (`GET_ACCOUNTS`). There is no time series anywhere in the code.

So the honest dependency order is: **build and verify balance history first**, then compute savings rate + the where-it-went breakdown on top of solid data (a separate, later spec). Balance history is also independently valuable — it is exactly what HELOC glide-path checkpoint tracking ($224.6K → ~$202K) needs.

### In scope
- One new Monarch GraphQL query for **per-account** balance history (`GET_BALANCE_HISTORY`), **verified live before anything is built on it**.
- A `BalanceSnapshot` model + adapter method.
- A pure analytics module resolving **as-of a date** (carry-forward) and **delta between two dates** (server-computed).
- Two new MCP tools exposing those.

### Out of scope (YAGNI)
- Savings rate and the "where it went" bucketing (the next spec, built on this).
- Full/dense time series for charting the glide path.
- Aggregate net-worth rollups.
- Anything that writes (Plan 4).

---

## 2. Architecture

Slots into the existing one-directional layered pattern; no new architecture is invented.

```
Monarch GraphQL  →  Adapter  →  DataService (+ SQLite cache)  →  Analytics  →  MCP tools
   (new query)     (new method)      (new data_type)          (new module)   (2 new tools)
```

Four thin additions, each mirroring something that already exists:

1. **`queries.py`** — add `GET_BALANCE_HISTORY`, one GraphQL query string. The only piece touching Monarch and the **first thing verified live**. Nothing else is built until it returns real per-account `{date, balance}` history for Adam's org.
2. **`models.py`** — add `BalanceSnapshot` (`{account, date, balance}`). Distinct from the existing `Balance` (a current-snapshot derivation); this is one point in a historical series.
3. **Adapter** — add `get_balance_history()` to the `MonarchAdapter` interface + `CommunityAdapter` implementation (same `_run`/transport plumbing as `get_transactions`, paginated if needed). `CsvAdapter` returns `[]` — honest empty, keeps offline mode + tests green.
4. **`analytics/balances.py`** — a new **pure** module (no I/O), mirroring `analytics/spending.py`. Two functions: `balance_as_of` and `balance_delta`.

The two MCP tools in `mcp_server.py` are thin: fetch snapshots via `DataService`, call the pure analytics function, wrap in the standard `_envelope` — same shape as the `date_range_summary` tool.

**Risk concentration:** the only genuinely new/risky surface is the Monarch query (#1). Everything else is a pattern shipped three times already. Monarch feasibility is rated "strong" (the community library exposes account history / snapshots), but the exact query shape has **not** yet been written or run against Adam's org — see §5, live verification.

---

## 3. Components & data contracts

### Model — `BalanceSnapshot` (`models.py`)
```
BalanceSnapshot:
    account: str        # display name, matches Account.name
    date: datetime.date
    balance: Decimal    # signed as Monarch reports (liabilities negative)
```
One historical point. `Decimal` for penny-exactness, consistent with `Transaction.amount`.

### Adapter — `get_balance_history()`
- Added to the `MonarchAdapter` interface (the 5th read method).
- `CommunityAdapter`: fetches per-account history via `GET_BALANCE_HISTORY`, maps to `list[BalanceSnapshot]`, sorted by `(account, date)`.
- `CsvAdapter`: returns `[]` (no history in CSV exports).

### Analytics — `analytics/balances.py` (pure, no I/O)

`balance_as_of(snapshots, target_date) -> list[dict]` — for each account, the last snapshot **on-or-before** `target_date` (carry-forward):
```json
[{ "account": "HELOC", "balance": "-202143.55",
   "as_of_requested": "2026-06-30", "snapshot_date": "2026-06-28",
   "is_carry_forward": true },
 { "account": "Brokerage", "balance": null,
   "as_of_requested": "2026-06-30", "snapshot_date": null,
   "reason": "no snapshot on or before requested date" }]
```

`balance_delta(snapshots, start, end) -> list[dict]` — server subtracts, per account:
```json
[{ "account": "HELOC",
   "start": {"balance": "-224600.00", "snapshot_date": "2026-05-31", "is_carry_forward": true},
   "end":   {"balance": "-202143.55", "snapshot_date": "2026-06-28", "is_carry_forward": true},
   "delta": "22456.45" }]
```
If either endpoint is null → `delta: null` + `reason` (never a silently-wrong number).

### MCP tools (`mcp_server.py`) — both thin, `_guard`- and `_envelope`-wrapped
- `balance_as_of(date: str)` → `{data_type: "balance_as_of", data: [...], count}`
- `balance_delta(start: str, end: str)` → `{data_type: "balance_delta", data: [...], count}`

### Conventions (approved)
1. **As-of gap policy: carry-forward with provenance.** On a gap (weekend, or no snapshot that exact day), return the last balance on-or-before the requested date, plus the real `snapshot_date` it came from and `is_carry_forward`. If the date predates the account's earliest snapshot → `null` + `reason` for that account. Same transparency instinct as the freshness envelope; protects delta math from silently comparing a stale carry-forward against an exact hit.
2. **Delta sign = `end − start`.** HELOC paydown (−224.6K → −202.1K) shows as a **positive** delta ("progress"). Consistent math: a growing brokerage is positive, growing debt is negative.
3. **Balance sign = as Monarch reports it** — liabilities negative, not flipped. Analytics is sign-agnostic.

---

## 4. Error handling, caching, testing

### Error handling (reuses existing typed-error plumbing)
- `SessionExpiredError` / `CloudflareBlockedError` from the shared transport propagate through `_guard` — expired session yields the same "re-run `financial-mcp-login`" message as other tools.
- Empty history (CSV mode, or account with no snapshots) → empty list / null-with-reason, never a crash.
- Bad date string → validation error surfaced through `_guard`, consistent with other tools.

### Caching
- Balance history is a new `data_type` in the SQLite cache with its own TTL, like `transactions`/`accounts`. History changes slowly (append-mostly), so a longer TTL is reasonable — default to the accounts TTL, tune later.
- `refresh_cache` and the freshness envelope pick it up automatically (both are `data_type`-driven).

### Testing (mirrors the `date_range_summary` approach; all offline/deterministic)
- **Analytics (pure, the bulk):**
  - `balance_as_of`: exact hit; gap/weekend carry-forward; date-before-first-snapshot → null+reason; multi-account.
  - `balance_delta`: normal delta; one endpoint null → null+reason; sign correctness (HELOC paydown = positive).
- **Adapter:** stub transport returning canned history JSON → correct `BalanceSnapshot` mapping + sort; CSV adapter returns `[]`.
- **Tools:** envelope shape, count, guard behavior on expired session.
- No live calls in the suite.

---

## 5. Live verification (done FIRST, outside the test suite)

Before building anything on top, run `GET_BALANCE_HISTORY` against Adam's real org and confirm it returns real per-account `{date, balance}` history — **including the HELOC**. If Monarch's shape differs from the community-lib assumption, adjust the query then, not after building four layers on a guess.

### 5.1 Confirmed query shape (Task 1, live-verified 2026-08-08)

Two candidate endpoints were probed against the real org. **Winner: `snapshotsForAccount` — it carries explicit dates.**

- **Rejected — `recentBalances(startDate:)`:** returns a *bare float array with no dates* (dates only implied by array position); array lengths vary per account (189 / 158 / 31 / 0); would require fragile back-counting from today + staleness heuristics. Compact bulk format, wrong grain for honest dating.
- **WINNER — `snapshotsForAccount(accountId:)`** (extracted from the library's `get_account_history`). Returns explicit dated pairs:

  ```graphql
  query SnapshotsForAccount($id: UUID!) {
    snapshots: snapshotsForAccount(accountId: $id) {
      date
      signedBalance
      __typename
    }
  }
  ```
  - **Operation name:** `SnapshotsForAccount` (MUST match the document's named operation, or Monarch returns a generic HTTP 400 — this was the root cause of the first probe's failures).
  - **Variables:** `{"id": "<account UUID string>"}` — the account `id` from `GetAccounts` (a numeric-looking string; passed as the GraphQL `UUID` scalar).
  - **JSON path to points:** `data.snapshots[*]` → each `{date: "YYYY-MM-DD", signedBalance: <number>}`.
  - **Balance field + sign:** `signedBalance` — already signed as Monarch reports (liabilities negative). CONFIRMED against the HELOC: −$179,366 (2024-02-14) → −$235,297 (2026-08-08).
  - **Date field:** explicit ISO `date` per snapshot; daily grain; ~907 days (~2.5 yrs) of history for established accounts. No derivation needed.
  - **Fan-out:** one call per account. `get_balance_history()` first fetches the account list (id + displayName) via the existing `GET_ACCOUNTS`, then issues one `SnapshotsForAccount` call per account, tagging each snapshot with the account's `displayName`. Accounts returning an empty/absent `snapshots` list contribute nothing (honest empty).

---

## 6. What this enables next
- **Savings-rate + "where it went" breakdown spec** — balance-delta reconciliation on top of verified history (the original P1 goal).
- **HELOC glide-path checkpoint tracking** — `balance_as_of` at each checkpoint date.
