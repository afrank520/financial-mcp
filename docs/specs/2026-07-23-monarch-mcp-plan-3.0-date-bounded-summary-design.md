# Monarch MCP — Plan 3.0: Date-Bounded Spending Summary (Design)

**Date:** 2026-07-23
**Status:** Design — pending user review
**Plan family:** Plan 3 (richer entities & advisor-grade analytics). This is the first Plan 3 deliverable.

---

## 1. Problem & Goal

**Problem (found by our first accuracy test, 2026-07-23):** When asked "how much did I spend at Amazon Jul 19–22," Claude called `search_transactions`, got correct rows, but then **summed them by hand** — and its bottom-line roll-up was wrong: it reported net $726.79 (should be $725.79, +$1.00), gross $785.65 (should be $784.65, +$1.00), and 22 transactions (should be 24). The *server data was penny-accurate*; the error was Claude's mental arithmetic over ~24 rows.

**Root cause:** The server has no tool that returns a **server-computed aggregate over a date window**. `merchant_summary`/`category_summary` compute exact totals but are *all-time only*. So any date-range question forces Claude to hand-sum rows.

**Goal:** Add a date-bounded spending summary tool that returns an exact, `Decimal`-computed total, count, and charge/refund breakdown for a date window (optionally filtered by merchant/category), so date-range questions never require Claude to do arithmetic.

**Success criterion (the first codified Goal #4 accuracy test):** For the Amazon Jul 19–22, 2026 fixture, the tool returns `total == Decimal("725.79")` and `count == 24` — matching the Monarch web UI to the penny.

---

## 2. Scope (this build)

### 2.1 New analytics function — `financial_mcp/analytics/spending.py`

```
date_range_summary(
    txns: list[Transaction],
    start: datetime.date,
    end: datetime.date,
    *,
    merchant: str | None = None,
    category: str | None = None,
) -> dict
```

**Behavior:**
- Filter to `start <= t.date <= end` (inclusive on **both** ends; the model stores `date` only, no timestamp, so a window is a pure date comparison — no timezone handling needed).
- Apply merchant/category substring filters by **reusing the existing `search()`** in the same module (DRY — identical matching semantics to `search_transactions`).
- Compute all values with `Decimal` (exact to the cent):

| Field | Definition | Reconciles to |
|---|---|---|
| `total` | **net** sum of all matching amounts | Monarch "Total spending" ($725.79) |
| `count` | count of all matching transactions | Monarch txn count (24) |
| `gross_charges` | sum of negative amounts, returned as a positive number | Claude's "gross spend" ($784.65) |
| `refunds` | sum of positive amounts | refund total ($58.86) |
| `charge_count` | count of negative-amount txns | 21 |
| `refund_count` | count of positive-amount txns | 3 |

- **Sign convention:** negative amount = charge/spend, positive = refund/credit (consistent with existing `monthly_spending`).
- Empty window (no matches) → all numeric fields `Decimal("0")`, counts `0`.

### 2.2 New MCP tool — `financial_mcp/mcp_server.py`

```
spending_summary(
    start_date: str,          # "YYYY-MM-DD", required
    end_date: str,            # "YYYY-MM-DD", required
    merchant: str | None = None,
    category: str | None = None,
) -> dict
```

**Behavior:**
- Parse `start_date`/`end_date` as ISO `YYYY-MM-DD`.
- **Validation:** on an unparseable date, return `{"error": "start_date must be YYYY-MM-DD"}` (or `end_date`). If `start > end`, return `{"error": "start_date must not be after end_date"}`. Error dicts are disjoint from the freshness-envelope keys and the auth-error keys, so there is never key ambiguity for Claude.
- Wrap the successful result in the **freshness envelope** used by every other data tool: `{as_of, age_hours, stale, count, data}`, with `data` = the `date_range_summary` dict (Decimal fields `_jsonify`'d to strings) and `data_type = "transactions"`.
- Wrap execution in `_guard` so `SessionExpiredError` / `CloudflareBlockedError` return the friendly re-login dict, exactly like the other tools.
- **Docstring** must steer Claude here for any "how much did I spend … between/from date X to Y" question, explicitly telling it to prefer this over hand-summing `search_transactions` rows, and noting `total` is net-of-refunds (matches Monarch) while `gross_charges` excludes refunds.

### 2.3 Testing (TDD)

- **Analytics unit tests** (`tests/test_spending.py`): net/gross/refund math; charge/refund counts; inclusive boundaries (a txn dated exactly on `start` and one exactly on `end` are both included; one the day before `start` and one the day after `end` are both excluded); merchant filter; category filter; empty window → zeros.
- **Tool tests** (`tests/test_mcp_tools.py`): envelope keys present; unparseable date → error dict; `start > end` → error dict; auth error → re-login dict.
- **Headline accuracy test:** a fixture reproducing the Amazon Jul 19–22 data (21 charges + 3 refunds across the four days) asserting `total == Decimal("725.79")` and `count == 24`. This is the first codified Goal #4 scenario and lives permanently in the suite as a regression guard.

### 2.4 Explicitly OUT of scope (YAGNI)

- No `min_amount`/`max_amount` filters (rare for date-range summaries; add later only if a real need appears).
- No relative windows ("last 7 days", "this month") — Claude resolves relative dates to absolute ones reliably before calling.
- No new cache type — reads the same cached transactions every other tool uses.
- No transfer/hidden-transaction exclusion logic — see Known Boundary (§4).

---

## 3. North Star Capability Map (vision — not built here)

This section captures where the tool is headed so every future Plan 3 entity is tied to a real advisory question. It is a **priority guide, not a build list.**

A world-class fee-only fiduciary reasons across four dimensions and asks questions in three *shapes*: **point-in-time**, **over-a-window**, and **derived/ratio**. The derived ratios are the differentiator — the hosted Monarch MCP handed Claude raw data and made Claude compute (the exact failure this plan fixes). A server that computes advisor-grade numbers with `Decimal` precision serves planning sessions in a way raw-data MCPs cannot.

| Advisor question | Data lens (shape) | Monarch source | Feasibility | Priority |
|---|---|---|---|---|
| Where does my money go over a period? | date-bounded spend summary (window) | transactions | ✅ **building now** | **P0 — this plan** |
| What's my savings rate? | income vs expense over window → ratio (derived) | cashflow API | strong | **P1 — differentiator** |
| Could I survive a job loss? | emergency-fund runway = liquid cash ÷ monthly essential spend (derived) | accounts + cashflow | strong | **P1 — differentiator** |
| How much is fixed vs. discretionary? | recurring/essential vs. rest over window (derived) | recurring + categories | medium | **P1 — differentiator** |
| Am I living on budget? | budget vs. actual variance (window) | budgets API | strong | P2 |
| What am I leaking on subscriptions? | recurring audit (point-in-time list) | recurring API | strong | P2 |
| Which way is net worth heading? | net-worth trend (window) | aggregate snapshots | strong | P2 |
| What high-rate debt should I attack? | debt by balance & rate (point-in-time) | accounts | rates weak in Monarch — **note limit** | P2 |
| Is my portfolio concentrated / cash-dragged? | allocation & concentration (point-in-time) | holdings API | strong | P3 |
| Category spend trend over time | repeated window aggregate (window) | transactions | ✅ enabled by this plan | P3 |

**Design implication carried forward:** the P1 "derived ratio" tools (savings rate, runway, fixed-vs-discretionary) should each be a **server-computed number**, not raw data for Claude to combine — same principle as this plan. `date_range_summary` is the reusable window primitive most of them build on.

### 3.1 Validation against a real worked example — the TeamFrank 12-Month Plan

We have a real artifact of "great": `TeamFrank_12Month_Plan_v3a.pdf` (July 2026, built with Claude + short-lived Monarch MCP + curated CSV data). Decomposing what that plan actually *needed* from data confirms this roadmap and adds one structural insight.

**Data lenses the plan leaned on (all map to have-now or Plan 3):**

| Plan mechanic | Data lens required | Status |
|---|---|---|
| HELOC balance & glide path (~$224.6K → ~$202K) | account balance + **balance history / net-worth trend** | accounts ✅ / trend = P2 |
| Weekly spending band ($1,500–2,500 normal) | **spend aggregated by week** | **← this plan's primitive, repeated weekly** |
| "Surprise > $5K = a decision" tripwire | **large-transaction detection over a window** | search + amount filter (small Plan 3 add) |
| "Checking < $6K = pause" tripwire | current balance (always-live) | accounts ✅ |
| Monthly scorecard, red/green months | **monthly net cashflow (income − expense)** | `monthly_spending` partial / cashflow = P1 |
| Reimbursement offset ($981.19 → groceries) | category spend including credits | category_summary ✅ (date-bounded via this plan) |
| "Every dollar off-glide is named" | variance = actual vs. expected band (derived) | P1 |

The weekly band and monthly scorecard — the heartbeat of that plan — are both just `date_range_summary` applied to a window. Building it first is correct.

**Structural insight — two kinds of "at the ready":** the plan's power came only partly from Monarch data. The *strategy* — five payroll levers, the refinance play, the house-rule tripwires, the glide-path targets, and the framing "transactions are noise; checkpoints are signal" — is **plan CONTEXT that does not exist in Monarch**. It lived in the document and the conversation, and was lost when the chat ended. For Claude Desktop to sustain this level of guidance, it needs BOTH:

1. **Financial DATA** — balances/transactions/spending → served by this MCP (the roadmap above).
2. **Plan CONTEXT** — rules, levers, targets, checkpoints → NOT in Monarch.

**Decision on context home (user, 2026-07-23):** *Now* — keep the TeamFrank plan + house rules + glide targets in the Claude Desktop **Project knowledge** (uploaded doc / custom instructions) so every chat sees them, zero code. *Later (Plan 3+)* — add an MCP **`financial_context` resource** so rules/levers/targets/checkpoints live alongside the data and travel to any client via the server. Deferred, not built here; recorded so it isn't lost.

---

## 4. Known Boundary — transfers & excluded transactions

Monarch's own reports **exclude internal transfers** (money moved between your own accounts) and any transaction flagged "hidden / exclude from reports." This tool does **not** implement that exclusion.

**Consequence:**
- **Merchant- or category-filtered windows** (e.g. "Amazon Jul 19–22") reconcile to Monarch exactly, because such filters naturally exclude transfers. *This is why the Amazon test hits $725.79 to the penny.*
- **Broad, unfiltered windows** ("all spending Jul 19–22") may **over-count** vs. the Monarch screen by including transfers/hidden items.

**Decision:** accept this boundary for now (our accuracy test is merchant-filtered and authoritative). Transfer/hidden exclusion is deferred to a later Plan 3 step that lands the real category hierarchy and a transfer flag on the transaction model. The tool docstring should note that unfiltered totals may include transfers.

---

## 5. Architecture & Consistency Notes

- **Follows existing patterns exactly:** analytics function in `spending.py` alongside its siblings; MCP tool in `mcp_server.py` using the established `_guard` + `_envelope` + `_jsonify` helpers.
- **Adapter seam untouched:** reads `svc.transactions()` like every other tool; no auth/adapter changes.
- **Files touched:**
  - `financial_mcp/analytics/spending.py` — add `date_range_summary`.
  - `financial_mcp/mcp_server.py` — add `spending_summary` tool.
  - `tests/test_spending.py` — analytics unit tests + Amazon accuracy fixture.
  - `tests/test_mcp_tools.py` — tool envelope/validation/auth tests.
- **No new dependencies. No schema/cache changes.**

---

## 6. Open Questions

None. All design decisions resolved during brainstorming:
- Headline `total` = net-of-refunds (matches Monarch), full breakdown returned alongside.
- Filters = merchant + category (reuse `search()`), no amount filters.
- Dates = validated ISO strings, inclusive both ends, friendly error dict on bad input.
- Vision captured as North Star map; build stays scoped to the date-bounded summary.
