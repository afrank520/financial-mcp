# Monarch Steward — Build Spec & Handoff

_Author: Claude (Cowork session, 2026-09-11) · Owner: Adam · Target: the `financial-mcp` repo (`~/projects/Personal/Monarch MCP`)_
_Status: approved direction — build it. Suggested repo location: `docs/STEWARD_SPEC.md`, referenced from `CLAUDE.md`._

---

## 0. Instructions to Claude Code (read first)

You are extending an existing, working MCP server. This document carries the operational context that lives outside the repo: live findings from Cowork sessions, data semantics we proved against Monarch, known defects, and the agreed design. **Treat this doc as the source of truth for *what* to build and *why*. Treat the repo as the source of truth for *how the code currently works*.** Where they disagree about current behavior, trust the code and note the discrepancy in your PR / summary.

Working agreement:

1. **Orient first (Phase 0).** Map the repo to §3 (tool inventory). Produce a short table: tool → module → matches spec? → notes. Don't refactor yet.
2. **Execute phases in order (§11).** Each phase has acceptance criteria. Commit per phase with tests green.
3. **Never mutate live Monarch data from Claude Code.** All development and tests run against fixtures/recorded responses. The only live calls allowed are read-only (to record fixtures) — and only if Adam's session is valid. If a phase appears to need a live write to verify, stop and list the exact call for Adam to run.
4. **Don't block on open decisions.** §12 gives a default for each. Use the default, flag it, move on.
5. **Adam's working style:** conclusions first, terse, show raw evidence. Verification-first: dry-run → confirm affected count → commit → verify. He is the decision-maker on every Monarch change; the software flags and executes, it doesn't gatekeep.
6. **Don't edit anything in `~/.financial_mcp/` except the new files this spec creates** (`findings.db`, `policy.yaml`). Never touch `session.json`.

### The core idea in one paragraph
Anyone can operate the MCP tools. The value is an **editor**: something that (a) refuses to advise on untrustworthy data, (b) ranks findings by whether they'd change a decision, (c) remembers what Adam already decided so it never nags, and (d) knows his standing rules and financial calendar. **Detectors live in code (deterministic, testable, cheap). Judgment and narration live in a skill.** One MCP entrypoint (`steward_run`) returns ranked, de-duplicated, suppression-filtered findings; the skill turns them into a ≤5-item brief with one-click actions.

---

## 1. Environment facts (do not re-diagnose)

| Item | Value |
|---|---|
| Repo | `~/projects/Personal/Monarch MCP` — package `financial-mcp`, **v1.1.0**, editable install (`pip install -e .`) in `./.venv` |
| Auth | Monarch session (session_id + csrftoken) at `~/.financial_mcp/session.json`; created by `./.venv/bin/financial-mcp-login` (email + password — Monarch bot detection blocks the Google button) |
| Config | `config.yaml` → `source: live`, default file credential store |
| Startup | `source` is evaluated **once at startup**. After re-login the server must be **restarted**. |
| Failure mode | Missing/expired session → logs `no Monarch session — run financial-mcp-login; falling back to CSV` and silently serves CSV. Outage 2026-08-28 → 2026-09-01 was exactly this. |
| Cache types (7) | transactions, balances, accounts, balance_history, networth, categories, holdings |
| Scale (2026-09-01) | 42 account entities (~26 real), 27,652 transactions, 3,223 merchants, 107 categories, 931 net-worth points |
| Consumers | Claude Desktop (local) and Cowork via the remote-devices bridge (tools surface as `monarch__<tool>`). Cowork scheduled tasks reach the server only while the laptop is awake and online. |
| Write modes | Mutating tools support `safe` (two-phase: preview → `confirm_token` → execute) and `full` (direct). Keep `safe` as default. |
| Sandbox note | Cowork's `device_bash` runs in a Linux VM with folders mounted; macOS venv paths look dangling there. Not a broken venv. |
| Housekeeping debt | `financial-mcp-doctor` missing from `.venv/bin` (fix: `pip install -e .`); stray dir `Monarch\ MCP` (literal backslash) in `~/projects/Personal`; startup logged twice on nearly every launch (double-init or two instances). |

---

## 2. Data semantics — proven rules the code must obey

These were verified to the penny against Monarch. Encode them; write tests that pin them.

### 2.1 Net worth
```
net_worth(date) = Σ balance(account, date)
                  for accounts where include_in_net_worth == true
                  AND is_carry_forward == false
```
- `is_carry_forward: true` = no fresh snapshot; Monarch repeats the last balance **and excludes it from net worth**.
- Verified with $0.00 residual on 2025-09-01 ($1,623,956.88), 2026-03-01 ($1,718,936.28), 2026-09-01 ($2,092,237.35).
- Summing `include_in_net_worth` alone overstated 2026-09-01 by **$436,557.87** (four carry-forward accounts, §2.4).
- **Consequence:** an account going carry-forward silently removes its balance from net worth with no error. This is the #1 integrity signal (detector I-3).

### 2.2 Account toggles (never automate these)
- **Hide account** — cosmetic. Safe.
- **Exclude account balance** — **retroactive**: removes the account from the *entire* historical series. Measured: toggling it on HELOC …8300 restated 2025-09-01 by +$164,876.07 and 2026-03-01 by +$189,834.27 while leaving today unchanged. Only for a true duplicate that double-counted from day one.
- **Hide transactions** — pulls history out of cashflow/budgets. Leave off.
- **Delete** — destroys balance history. Never.

No tool in this project may set Exclude, Hide-transactions, or Delete on accounts.

### 2.3 Tool semantics that trap analysis
- **`monthly_spending` counts every negative amount, including transfers and card payments.** Aug 2026: `monthly_spending` = −$34,024.78 vs `budget_status.totals.expense.actual` = $28,621.49. **Never use `monthly_spending` as "spending."** Use expense-typed category totals.
- **`savings_summary`**: Monarch floors negative savings rates at 0 (`rate_is_floored`). Use the signed rate. Rate is on take-home; pre-tax retirement is excluded.
- **`recurring_summary`**: amounts/frequencies are Monarch's own predictions and can be stale (see M-5).
- **`budget_status`**: `remaining` ≠ `budgeted − actual` for rollover categories. Don't recompute; read it.
- **Freshness metadata** currently times the *cache write*, not the *data* (defect D-3). Until fixed, derive freshness from `snapshot_date`.
- **`get_accounts` is unreliable** (defect D-1). Use `balance_as_of` / `balance_delta` — they carry `account_id`, `include_in_net_worth`, `snapshot_date`, `is_carry_forward`.
- `search_transactions`: `merchant_ids` filter silently returns the full dataset; `category_id` filter is unreliable. Use date-bounded pulls + client-side filtering.

### 2.4 Known account state (seed for the registry — Appendix C has ids)
| Account | State | Treatment |
|---|---|---|
| AgentSync 401(k) (…0768) | carry-forward since 2025-07-21, $581,399.26 | expected; suppress |
| 15 Branwood HELOC (8300) | closed 2026-08-11, carry-forward from 08-10, −$235,297.14 | expected; suppress |
| Syosset 403b (…665Y71, Finicity) | carry-forward since 2026-01-15, $89,800.07; holds all 2025 history | expected; **never Exclude** |
| Savings (Apple, old entity) | carry-forward since 2026-08-03, $655.68; a new `Savings` entity appeared | expected-pending: confirm re-link |
| Syosset 403b (…5Y71, MX) | live | normal |
| Home Equity Line of Credit (…2800) | new 2026-08-11, $350k limit, drawn $250,000 (deliberate round-number cash floor) | normal; watch first interest posting |
| 529s ×5 (new entities `2373137*`) | live, $200,704.89 combined; **aggregator shows "Connection unavailable"** | **WATCH** — if they go carry-forward, ~$200k silently leaves net worth |
| 529s ×5 (old entities `1694858*`) | include_in_net_worth=false | leave alone (decision: don't reconfigure on a broken connection) |
| Tracey Student Loan | stale duplicate of SoFi Student Loan Refinance (…7625); excluded | leave |
| Corporate Card …1002 / …1009 | excluded | leave |
| Amex Gold | mid-month balance is a running tab; autopay ~4–5 days before due | suppress mid-cycle balance alerts |

### 2.5 Categorization conventions (Adam's)
- Categorize by **nature of purchase**, not merchant type.
- Refunds = negative expense in the original category, not income.
- Aggregator misroutes inbound credits (refunds, RSU/ESPP proceeds) to **Transfer** — recurring pattern.
- **Merchant names double as a memo field.** Do-not-merge families: **Miss Sue's** (46 merchant records, names encode child/month/school year/payment type) and **Capital One** (suffixes identify each child's savings account; a prior merge was correctly undone). `notes`/`tags` are empty — the merchant string is the only record.
- **Normalization false positives to guard against:** "Citi" swept in *Citi Field*; "New York" merged the 529 plan, the tax department, and a vet specialist. Generic tokens must never be a merge key.
- **Provider-side descriptor changes are the #1 source of duplicates** (LIPA→PSEG split, Voya Finicity→MX, 529 migration). A variable bill matched by an **amount**-scoped rule is fatal (July categorized, August didn't).
- MCP-created rules default to **exact match** — Adam fixes to **Contains** + apply retroactively in the UI after creation.

---

## 3. Current tool inventory (as exposed today)

Verify against the repo in Phase 0.

**Health/cache:** `health_check`, `cache_status`, `refresh_cache(data_type?)`
**Accounts/balances:** `get_accounts` ⚠, `balance_as_of(date)`, `balance_delta(start,end)`, `networth_trend`, `holdings_summary`
**Transactions:** `get_transactions(limit≤500)`, `search_transactions` (merchant/category substring, amount range, dates; `min_amount` needed for high-volume merchants)
**Analytics:** `spending_summary`, `category_summary`, `merchant_summary` (complete totals), `monthly_spending(year)` ⚠, `budget_status(start,end)`, `savings_summary(start,end)`, `recurring_summary(start,end)`, `merchant_cleanup_suggestions(include_curated, limit)` (read-only; separates Retail Sync merchants)
**Writes (two-phase in safe mode, undoable):** `recategorize_transaction`, `recategorize_matching` (200-txn hard cap, refuses over cap; always date-bound repeat merchants), `rename_merchant_matching`, `create_merchant_rule`, `create_category_rule(contains, set_category)` (future-only), `update_transaction_rule` (narrow — refuses rules matching on amount/statement text/account/category; unreliable on rules made by `create_merchant_rule`), `create_category`, `rename_category`, `delete_category`
**Undo:** `undo_last`, `undo_history` — **global stack**: `undo_last` pops the most recent batch regardless of session.

**Missing (blocking real fixes):** `list_rules`, `delete_rule`, `regroup_category`, transaction `notes`/`tags` write, transaction split, budget write, category-group create, category variability (fixed/flexible) set.

**Concurrency hazard:** parallel writes through the device bridge can report "did not respond within 60s" **while succeeding server-side**. Blind retry double-applies. Always check `undo_history` before retrying.

---

## 4. Known defects (fix before building on top)

| ID | Defect | Fix | Phase |
|---|---|---|---|
| D-1 | `get_accounts` returns $0 for real balances (Coinbase $0 vs $4,510.98), forges `as_of` per row, omits `account_id`/`include_in_net_worth`/`is_carry_forward`, returns 42 rows for ~26 accounts | Rebuild on the `balance_as_of` code path; expose those fields + real `snapshot_date` | 1 |
| D-2 | `refresh_cache` returns `{"refreshed": "<type>"}` on success **and** failure (WARNING log only) | Return `{ok:false, error}`; non-zero status to caller | 1 |
| D-3 | On failure writes **epoch-0** timestamps (`as_of: 1970-01-01`); freshness times the cache write not the data (`stale:false, age_hours:0` over balances dated 2026-06-15) | Freshness = max(`snapshot_date`) of live accounts / latest txn date; never epoch-0 | 1 |
| D-4 | CSV fallback silently drops budgets; server degrades silently | Surface `source` in every envelope; `health_check` returns `degraded` + reason when not live | 1 |
| D-5 | No transaction dedupe on id (one txn returned 5×) | Dedupe by id at cache load | 1 |
| D-6 | Any tool computing net worth by summing `include_in_net_worth` | Implement §2.1 in one shared function; all callers use it | 1 |
| D-7 | Startup logged twice | Find double-init | 0 |
| D-8 | `financial-mcp-doctor` entry point missing | Reinstall editable; add to CI check | 0 |

---

## 5. Target architecture

```
                         ~/.financial_mcp/policy.yaml   (rules, known-state, goals, calendar)
                                     │
Monarch ──► cache (7 types) ──► DETECTORS (pure functions) ──► findings ──► RANKER ──► SUPPRESSION ──► steward_run()
                                 integrity / maintenance /        │                     (findings.db)       │
                                 patterns / policy                ▼                                          ▼
                                                           findings.db (ledger)                    SKILL: monarch-steward
                                                                                                   (narrate ≤5 items,
                                                                                                    propose actions,
                                                                                                    execute on approval
                                                                                                    via existing 2-phase tools)
```

### 5.1 New MCP tools
| Tool | Type | Purpose |
|---|---|---|
| `steward_run(mode: "daily"\|"weekly"\|"monthly", as_of?: date, max_items=5)` | read + ledger write | Runs the detector set for the mode, ranks, applies suppression, upserts ledger, returns `{integrity, items[], backlog_count, needs_input[], methodology}`. **Primary entrypoint — the skill should need one call per run.** |
| `integrity_check()` | read | Integrity detectors only (I-*). Cheap. Used by the morning brief. |
| `findings_list(status?, detector?, limit)` | read | Browse ledger. |
| `findings_update(id, status, reason?, snooze_until?)` | ledger write | approve / dismiss (reason required) / snooze / resolve. Dismissal with reason creates a suppression rule. |
| `policy_get()` / `policy_validate()` | read | Load and validate `policy.yaml`; report missing goals once. |
| `list_rules()` / `delete_rule(rule_id)` | Monarch read / write (2-phase, undoable) | Rule enumeration & deletion. |
| `regroup_category(category, group)` | Monarch write (2-phase, undoable) | Unblocks Camp Tuition → Children and most Tier-2 fixes. |
| `set_transaction_notes(ids, notes)` / `set_transaction_tags(ids, tags)` | Monarch write (2-phase, undoable) | Unblocks safe merchant consolidation (preserve memo detail before merging). |

Phase 5 must first confirm each missing capability exists in Monarch's GraphQL API (inspect the web app's operations). If an operation doesn't exist, record that and skip — don't fake it with UI automation.

### 5.2 Ledger (`~/.financial_mcp/findings.db`, SQLite)
- Finding identity: `id = sha1(detector_id + subject_key)` where `subject_key` is stable (e.g., normalized merchant root, account_id, category). Same issue across runs = same id.
- Statuses: `new → open → approved → resolved` · `dismissed` (reason required) · `snoozed` (until date) · `needs_input`.
- **Suppression:** dismissed findings stay suppressed unless evidence materially changes (dollars_at_stake +50% and ≥ $250, or a new detector-specific trigger). Snoozed reappear after `snooze_until`.
- **Auto-resolve:** if a detector no longer produces a finding (e.g., the rule was created and txns recategorized), mark `resolved` with `resolved_by: "data"`.
- Keep `first_seen`, `last_seen`, `times_shown`. Anything shown 3× without action gets demoted to backlog (Adam has implicitly deprioritized it) — surfaced monthly in a "still open" count, not in the top 5.

### 5.3 Finding schema (Appendix B has JSON Schema)
`id, detector, layer (integrity|fix|notice|decide), title, summary, evidence{txn_ids[], account_ids[], merchants[], categories[], dates[], figures{}}, dollars_at_stake, distortion (bool), actions_to_fix (int), priority, confidence (high|medium|needs_input), question?, proposed_action{tool, args, dry_run_first:true, reversible:true}?, first_seen, last_seen, status`

### 5.4 Ranking (reuse Adam's metric)
```
priority = distortion × dollars_at_stake ÷ actions_to_fix
```
- `distortion` = does it land in the wrong bucket and change a number Adam would read? If false, priority = 0 (goes to backlog, cosmetic).
- Average transaction value (V/N) is a **verification** filter, not priority: `avg ≥ $500` → mark `confidence: medium` and require eyeballing. `avg < $20 and N > 100` → noise; rule-worthy at best.
- `decide`-layer items rank by estimated annual $ impact.
- **Integrity overrides everything:** if any integrity finding is `high`, the run returns integrity items first and sets `trust: "degraded"`; the skill must state that the numbers below are unverified and must not produce `decide` advice.
- At most **1 `needs_input` question per run.**

---

## 6. Detector catalog

Each detector: pure function `(cache, policy, ledger_ctx, as_of) → [Finding]`. Thresholds live in `policy.yaml` with the defaults below. **Every detector ships with a fixture test built from the 2026-09-11 examples cited.**

### 6.1 Integrity (daily)
| ID | Detects | Logic / default | Example (live) |
|---|---|---|---|
| I-1 | Not live | `source != live` or session missing → high | 2026-08-28 → 09-01 outage |
| I-2 | Stale data | Any live-linked, non-registry-expected account with `snapshot_date` older than 3 days; latest txn date older than 3 days | balances dated 06-15 reported fresh |
| I-3 | **Carry-forward transition** | Account flips `is_carry_forward` false→true since last run and isn't registry-expected. Report the $ that just left net worth. Special watch list: the five 529s. | would have caught $436k of ghosts; 529s at risk ($200,704.89) |
| I-4 | Net worth mismatch | §2.1 recompute vs Monarch-reported networth ≠ $0.00 (tolerance $1) | residual must be $0 on the three verified dates |
| I-5 | Budgets missing | `budget_status` empty/unavailable | CSV mode drops budgets |
| I-6 | Refresh failure | any refresh error or epoch-0 timestamp | D-2/D-3 |
| I-7 | Duplicate txns | repeated txn ids in cache | D-5 |
| I-8 | New account entity | an account id not in registry appears (often a provider re-link) → needs_input: "same account as X?" | new `Savings` entity; 403b Finicity→MX; 529 migration |

### 6.2 Maintenance (weekly)
| ID | Detects | Logic / default | Example (live 2026-09-11) |
|---|---|---|---|
| M-1 | **Descriptor drift** | Group merchants by conservative normalized root (strip `DES:`/`ID:`/`INDN:`/`CO ID`/`PPD`, punctuation, ®/™, trailing store numbers; **never** merge on a lone generic token from a stoplist: New York, Citi, Amazon Mktp variants need ≥2 shared tokens). If variants carry **different categories** → distortion=true. Same category → cosmetic. Respect do-not-merge families. Proposed action: `rename_merchant_matching` + `create_merchant_rule` (paired, or it re-fragments). | `Jiobit.com` → *School Technology* vs `Jiobit` → *Miscellaneous*; `KMF DES:KMFUSA.com…` vs `Kia Motors Finance` ($475 each, 1 day apart); `Plainview Pediatric De` vs `Plainview Pediatric Dentistry`; `New York 529` vs `New York's 529 College Savings Program` |
| M-2 | Split categorization on one merchant | Merchant with ≥2 categories in trailing 90d where one holds ≥75% of txns → recategorize minority + category rule | LIPA July=Electric, Aug=Water (fixed 09-02) |
| M-3 | Catch-all creep | New txns in Uncategorized / Miscellaneous / Transfer since last run; **inbound credit ≥ $500 in Transfer = likely income** (confidence medium) | Morgan Stanley RSU $11,678.60 sat in Transfer; ~+$381,663 unmatched inbound Transfer remains (open question) |
| M-4 | **Payment convention mismatch** | A budgeted category with actual $0 for ≥2 consecutive months while a recurring stream of similar amount (±15%) lands in another category (often Transfer) | Student Loans budget $545 / actual $0 while SoFi stream is categorized **Transfer**; Mortgage, by contrast, is an expense ($8,362.13) |
| M-5 | Recurring drift & duplicates | (a) Recurring predicted amount differs from median of last 3 actuals by >5%; (b) two streams, same normalized lender, same amount, ≤3 days apart → needs_input "two obligations or one double-counted?" | SoFi predicted **$568.13** (old MOHELA amount) vs actual **$544.76**; Kia/KMF $475 ×2 |
| M-6 | **Budget calibration / cancel-out** | Flexible categories where \|actual−budget\| > max($100, 50%) in ≥3 of last 4 months → propose trailing-12 median (outliers trimmed). Also report **gross over vs gross under vs net** for the month; flag when net looks on-target only because errors cancel. Note: no budget-write tool → emit values for Adam to apply. | Aug: expense $28,621.49 vs $28,506 budget (net +$115.49) = gross +$7,500.05 over / −$7,384.56 under, masking Parking $20→$270, Pharmacy $15→$238.08, Auto Maintenance $50→$647.40, Monthly Subscriptions $75→$332.71, offset by Electric −$845.60, HELOC Interest −$1,400, Student Loans −$545 |
| M-7 | Unexpected zero on fixed item | Fixed/budgeted item with $0 actual where an obligation is known (registry/liability) → "where is it landing?" | HELOC Interest Expense $1,400 budget, $0 actual — new HELOC …2800 interest not yet seen; verify category when it posts |
| M-8 | New merchant, no rule | Merchant first seen ≤60d ago with ≥2 txns and no covering rule | — |
| M-9 | Rule hygiene (needs `list_rules`) | Rules matching on **amount** for variable-amount merchants; rules created via MCP still exact-match | LIPA rule `250814342483557513` (deleted by Adam) |
| M-10 | Taxonomy defects | Static list in policy: wrong group, typos, whitespace | **Camp Tuition in "Other" → Children** ($91,716/61 txns — nearly the whole Children group, so every group rollup is wrong); "Kids Sports Equi**m**ent" typo (actively receiving txns); "School Technology␠" trailing space; "Executive␠␠Coaching"; Veterinary, Home Insurance, Music, Movies & DVDs, Books & Supplies, Laundry, Investments, Car Wash sitting in "Other" |
| M-11 | Income classification | Recurring income stream categorized outside Paychecks without a policy note → needs_input once, then remembered | Salesforce semimonthly **$5,737.45 → "Bonus"**; `Deposit` $2.05 biweekly into Braden Savings → "B'Nai Gift" (likely interest) |

### 6.3 Patterns (weekly light, monthly full)
| ID | Detects | Logic / default |
|---|---|---|
| P-1 | Weekly spend band | Discretionary expense per week vs trailing-26-week median + IQR. **Actionable only after 2 consecutive weeks above band with no nameable reason** (Adam's standing rule). Single-week spikes → never surfaced. |
| P-2 | Seasonality | Compare month to **same month prior year(s)**, not prior month. Surface categories with YoY Δ > max($250, 25%). Summer camp, SS-cap step-up, back-to-school must not read as anomalies. |
| P-3 | Recurring creep | New recurring streams in last 90d; price increases on existing streams; committed monthly total vs 12 months ago. |
| P-4 | Savings rate | Signed 3-month rolling rate from `savings_summary`; flag negative with context. Live: Jun–Aug 2026 income $83,652.36, expense $99,760.60, **−$16,108.24 (−19.26%)**; Monarch shows 0% (floored). |
| P-5 | Idle / low-yield cash | Per-account annualized yield ≈ interest credits ÷ avg balance. Live: kids' savings ≈ **$40,966** combined at 8/31; Lexi's account ~1.6% annualized, Braden's <1% (rough — small deposits mixed in). Output a "check rates" item, never a product recommendation. |

### 6.4 Policy & advice (monthly + calendar-triggered)
Driven entirely by `policy.yaml`. If a required goal is missing, emit **one** `needs_input` ("add your HELOC paydown target to policy.yaml") and skip that check — don't guess.

| ID | Rule / trigger | Logic |
|---|---|---|
| A-1 | **Park-and-deploy** windfalls | Inbound RSU / ESPP / bonus lump ≥ `windfall_min` ($2,000) to checking without an outbound transfer to a `parking_institutions` account within 2 business days → notice |
| A-2 | **Name every HELOC dollar** | Each HELOC drawdown must have a named purpose (notes/tag once writable; until then list unnamed draws) |
| A-3 | Amex float | Suppress mid-cycle balance. Alert only if autopay fails to clear the full statement or checking < `cash_floor` outside due-date week |
| A-4 | B'Nai money exclusion | Kids' accounts and B'Nai categories excluded from household cashflow, savings rate and P-* calcs (historical and forward) |
| A-5 | SS-cap step-up (Sep–Dec) | Before/at first step-up paycheck: "~$1,187/mo arriving Sep–Dec — assign it now" (pre-name the dollars). January: warn of ~$1,850/mo step-down. |
| A-6 | NY 529 deduction headroom | YTD contributions to NY 529 vs `ny529_deduction_cap` ($10,000 MFJ) × `ny_marginal_rate`. Live: recurring $220 + $191.25 = $411.25/mo ≈ $4,935/yr → ~$5,065 headroom ≈ ~$347 state tax. **Must sum YTD actual contributions (incl. lump sums) before surfacing.** Deadline Dec 31. Surface from Oct 1. |
| A-7 | W-4 calibration (October) | RSU supplemental withholding at 37% vs effective marginal → prompt to calibrate using Morgan Stanley/E*Trade withholding actuals |
| A-8 | FSA sizing (open enrollment, Nov) | Sum predictable medical/dental/ortho recurring (live: Plainview ×2 + Pillar = **$650/mo** predicted) → "size a health FSA?" as a question, not an assertion |
| A-9 | RSU vest days | From `rsu_vest_dates`: day-of reminder of the sell-at-vest rule (~10am ET, full position) and A-1 follow-through |
| A-10 | Goals progress | HELOC balance vs paydown target/date; cash vs floor; per-kid 529 vs targets; report pace (ahead/behind, $/mo needed) |

---

## 7. The skill: `monarch-steward`

Build `skills/monarch-steward/SKILL.md` in the repo. Adam installs it in Cowork (it runs there, calling the MCP through the device bridge). Requirements:

**Triggers:** "run the steward", "Monarch maintenance", "money brief", `/steward`, and the scheduled runs. A single question ("what did we spend on groceries?") is *not* a trigger — answer it directly.

**Flow (one tool call in the common case):**
1. `steward_run(mode)`.
2. If `trust == "degraded"` → lead with the integrity items, state "numbers below are unverified," omit `decide` items, stop.
3. Render ≤5 items in three sections — **Fix** (approve to apply) · **Notice** · **Decide** — each with: one-line claim, $ impact, confidence, the evidence (txn/merchant/account names and figures), and exactly one action.
4. Ask at most one `needs_input` question.
5. Footer: backlog count, last run, data as-of.

**On approval:** call the proposed tool **without** `confirm_token` (dry-run) → show affected count vs expected → on match, execute with the token → `undo_history` to confirm → `findings_update(id, "resolved")`. On timeout: check `undo_history` before any retry. On dismiss: require a short reason → `findings_update(id, "dismissed", reason)`.

**Never:** set Exclude/Hide-transactions/Delete on accounts; bulk-merge do-not-merge families; use `monthly_spending` as spending; recommend specific financial products; restate a dismissed finding.

**Voice:** the best financial advisor Adam could hire — conclusions first, a specific recommendation, pragmatic optimism with a dose of reality, and an explicit "data is inconclusive, here's what would decide it" when that's true.

**Delivery (Cowork side):** weekly/monthly → a styled artifact page with per-item Approve / Dismiss / Snooze; daily → only integrity items, injected into Adam's existing 8am morning brief and silent when clean.

---

## 8. Scheduling (configured in Cowork, not Claude Code)

| Run | When (ET) | Cron (UTC) | Mode |
|---|---|---|---|
| Integrity gate | Weekdays 8:00 — inside the existing morning brief | (existing brief task) | `integrity_check()` |
| Weekly maintenance | Mon 7:30 | `30 11 * * 1` | `weekly` |
| Monthly advisor | 3rd of month 8:00 (month-end postings settled) | `0 12 3 * *` | `monthly` |

Scheduled tasks need the laptop awake with the desktop app running. **Reality check:** runs will miss nights/weekends/travel. The durable fix is the planned self-hosted Docker deployment (Phase 7) on an always-on box — also keeps personal finance off the corporate laptop. Until then, `steward_run` must be idempotent and catch up (use `last_run` from the ledger to widen the window).

---

## 9. Output example (what "done" looks like, from 2026-09-11 data)

> **Monarch Steward — weekly · data as of 2026-09-10 · trust: OK**
>
> **Fix**
> 1. **Student loan payments land in Transfer** — Student Loans budget reads $0 vs $545 every month; SoFi stream is Transfer, while Mortgage is treated as expense. → *Approve: category rule `SoFi` → Student Loans + recategorize trailing 12 mo (dry-run first).* · confidence high
> 2. **Jiobit split across two categories** — `Jiobit.com` → School Technology, `Jiobit` → Miscellaneous. → *Approve: merchant rule + recategorize.* · high
> 3. **Camp Tuition sits in "Other"** — $91,716 / 61 txns; every Children rollup is wrong. → *Approve: regroup to Children.* · high
>
> **Notice**
> 4. **August budget "on target" only because errors cancel** — net +$115.49, but that's +$7,500.05 of gross overspend offset by −$7,384.56 of gross underspend (incl. a −$1,194.05 dentist credit, $1,400 HELOC interest not posted, $545 student loan mis-filed). → *Review: reset flexible budgets to trailing-12 medians (values attached).*
>
> **Decide**
> 5. **NY 529 deduction ~half used** — ~$5,065 headroom ≈ ~$347 NY tax; deadline Dec 31. → *Confirm YTD lump sums, then decide top-up.* · needs YTD check
>
> _Question:_ Kia $475 × 2 on consecutive days — two vehicles, or one payment double-counted in the forecast?
> _Backlog: 14 open · Last run 2026-09-04_

(Item 4 figures computed from `budget_status` 2026-08-01→08-31 expense categories: budgeted $28,506.00, actual $28,621.49. Use as a fixture.)

---

## 10. Safety & write discipline (hard requirements)

- Default `safe` mode. Every write: preview → count check → commit → `undo_history` verify.
- Paired historical merge + forward rule, or the merchant re-fragments (Home Depot took a new txn 2026-08-29 after analysis).
- Honor the 200-txn cap; split by date window, never truncate.
- No parallel writes through the bridge.
- Newly created rules: surface a reminder that MCP-created rules are exact-match until fixed to Contains in the UI (or fix it in code if the API allows setting the operator — investigate in Phase 5).
- Credentials: never log session values; `policy.yaml` and `findings.db` contain no secrets.

---

## 11. Phases & acceptance criteria

**Phase 0 — Orient & housekeeping**
- Inventory table (tool → module → matches §3 → notes). Fix D-7, D-8. Remove the stray `Monarch\ MCP` directory **only after confirming it's empty/duplicate** — otherwise list its contents for Adam.
- ✅ Accept: inventory in the summary; `financial-mcp-doctor` runs; single startup log line.

**Phase 1 — Trust defects (D-1…D-6)**
- ✅ Accept: fixture tests pin §2.1 on the three verified dates with $0.00 residual (record `balance_as_of` for those dates as fixtures); `get_accounts` returns `account_id`, `include_in_net_worth`, `is_carry_forward`, true `snapshot_date` and never $0 for a carry-forward balance; forced refresh failure returns `ok:false` and no epoch-0 anywhere; every envelope carries `source`; duplicate txn ids removed.

**Phase 2 — Ledger, policy, integrity**
- `findings.db` schema + migrations; `policy.yaml` loader/validator seeded from Appendix A; account registry (Appendix C); I-1…I-8; `integrity_check`, `findings_list`, `findings_update`, `policy_get/validate`.
- ✅ Accept: simulated carry-forward flip on a 529 fixture produces one high I-3 with the correct $ removed; registry-expected carry-forwards produce nothing; re-running yields the same finding id; dismiss-with-reason suppresses on the next run.

**Phase 3 — Maintenance detectors (M-1…M-11) + ranker + `steward_run`**
- ✅ Accept: fixtures reproduce every "Example (live)" in §6.2; M-1 does **not** merge Citi/Citi Field or New York 529/NY tax dept, and does **not** propose merging Miss Sue's or Capital One; `steward_run("weekly")` returns ≤5 items ranked by §5.4 with the rest counted in backlog.

**Phase 4 — Patterns & policy (P-*, A-*)**
- ✅ Accept: P-1 fires only on 2 consecutive weeks; P-2 compares YoY; P-4 reports the signed −19.26% on the Jun–Aug fixture; A-6 refuses to surface without a YTD sum; missing goals yield exactly one `needs_input`; B'Nai exclusion verified in P-4/A-10 math.

**Phase 5 — Missing write tools**
- Investigate Monarch GraphQL for: list/delete rules, regroup category, notes/tags, rule match-operator. Implement what exists, two-phase + undo, with tests against recorded responses.
- ✅ Accept: capability matrix (exists / implemented / not available) in the summary; no UI automation.

**Phase 6 — Skill**
- `skills/monarch-steward/SKILL.md` per §7, plus `skills/monarch-steward/README.md` with install + the three Cowork schedules in §8.
- ✅ Accept: a dry transcript against fixtures shows the §9 shape; degraded-trust path verified.

**Phase 7 — Self-host (separate track, already planned)**
- Docker image, rolling `:stable` + versioned tags, Watchtower, GitHub Actions. Session file mounted as a secret volume; loud failure when the session expires (push a notification, don't silently degrade).

**Hand back to Adam at the end:** (1) what shipped per phase, (2) the capability matrix, (3) the SKILL.md text to install in Cowork, (4) any live calls he needs to run himself, (5) defaults you chose in §12.

---

## 12. Open decisions — defaults to use

| Decision | Default | Why |
|---|---|---|
| Student loan payments: expense or transfer? | **Expense (Student Loans)**, matching Mortgage | Budget line reads true; consistent liability treatment. Adam can flip in policy. |
| Salesforce semimonthly deposits in "Bonus" | Leave as-is; ask once (M-11) | May be intentional. |
| Kia/KMF duplicate stream | Ask once (M-5b) | Could be two vehicles. |
| Ledger location | SQLite at `~/.financial_mcp/findings.db` | Local, survives restarts, travels with the Docker volume. |
| Max items per brief | 5 | Attention budget. |
| Thresholds | As listed in §6 | Tunable in `policy.yaml`. |
| Budget writes | Emit values for manual entry | No budget-write API known. |
| `ny_marginal_rate` | 0.0685, flagged "verify" | Rate config, not code. |

---

## Appendix A — `policy.yaml` seed

```yaml
version: 1
household:
  filing_status: married_joint
  state: NY
  ny_marginal_rate: 0.0685        # verify annually
cash:
  cash_floor: null                # REQUIRED for A-3/A-10 — Adam to fill
  parking_institutions: ["Capital One", "Apple"]   # windfall parking, separate from BofA checking
  windfall_min: 2000
debt:
  heloc:
    account_id: "251845388366920938"
    limit: 350000
    paydown_target: null          # REQUIRED for A-10 — Adam to fill (amount + date)
    apr: null                     # not in Monarch; needed for cash-drag math
    draws_must_be_named: true
  amex_float:
    account_id: "233056032900660408"
    suppress_midcycle_balance: true
    payment_cadence: "single sweep ~16th; autopay backstop"
income:
  rsu:
    sell_at_vest: true
    vest_dates: []                # Adam to fill
    planning_anchor: "share_count x current_price"   # never the $97K offer value
  ss_cap_stepup: { months: [9,10,11,12], approx_monthly: 1187, january_stepdown: 1850 }
exclusions:
  household_cashflow_excludes:
    categories: ["B’Nai Mitzvah", "B’Nai Gift"]
    accounts_by_name_prefix: ["Braden Savings", "Alexis Savings", "Colton Savings", "Dylan Savings", "Elaina Savings"]
education:
  ny529_deduction_cap: 10000
  per_child_targets: {}           # optional
categorization:
  liability_payments_as_expense: true
  do_not_merge_merchant_families: ["Miss Sue", "Capital One"]
  normalization_stoplist: ["New York", "Citi", "NY", "Transfer", "Payment", "Deposit"]
  taxonomy_fixes:
    - { category: "Camp Tuition", move_to_group: "Children" }
    - { category: "Veterinary", move_to_group: "Health & Wellness" }
    - { category: "Home Insurance", move_to_group: "Housing" }
    - { category: "Music", move_to_group: "Travel & Lifestyle" }   # worklist said "Entertainment"; no such group exists today — confirm with Adam
    - { category: "Books & Supplies", move_to_group: "Education" }
    - { category: "Car Wash", move_to_group: "Auto & Transport" }
    - { category: "Investments", move_to_group: "Savings & Investments" }
    - { rename: "Kids Sports Equiment", to: "Kids Sports Equipment" }
    - { rename: "School Technology ", to: "School Technology" }
    - { rename: "Executive  Coaching", to: "Executive Coaching" }
thresholds:
  stale_days: 3
  weekly_band_window_weeks: 26
  weekly_band_consecutive: 2
  yoy_min_dollars: 250
  yoy_min_pct: 0.25
  budget_miscal_min_dollars: 100
  budget_miscal_min_pct: 0.5
  recurring_drift_pct: 0.05
  high_avg_verify: 500
calendar:
  - { month: 10, id: "A-7", title: "W-4 calibration for RSU over-withholding" }
  - { month: 11, id: "A-8", title: "Open enrollment — FSA sizing" }
  - { month: 10, day: 1, until: "12-31", id: "A-6", title: "NY 529 deduction headroom" }
  - { month: 1, id: "A-5", title: "SS restart — take-home steps down" }
  - { month: 9, id: "A-5", title: "SS cap step-up — assign the dollars" }
```

## Appendix B — Finding JSON Schema (abridged)

```json
{
  "type": "object",
  "required": ["id","detector","layer","title","dollars_at_stake","distortion","actions_to_fix","priority","confidence","status"],
  "properties": {
    "id": {"type":"string"},
    "detector": {"type":"string", "pattern":"^(I|M|P|A)-\\d+[a-z]?$"},
    "layer": {"enum":["integrity","fix","notice","decide"]},
    "title": {"type":"string"},
    "summary": {"type":"string"},
    "evidence": {"type":"object","properties":{
      "txn_ids":{"type":"array","items":{"type":"string"}},
      "account_ids":{"type":"array","items":{"type":"string"}},
      "merchants":{"type":"array","items":{"type":"string"}},
      "categories":{"type":"array","items":{"type":"string"}},
      "figures":{"type":"object"}}},
    "dollars_at_stake": {"type":"number"},
    "distortion": {"type":"boolean"},
    "actions_to_fix": {"type":"integer","minimum":1},
    "priority": {"type":"number"},
    "confidence": {"enum":["high","medium","needs_input"]},
    "question": {"type":["string","null"]},
    "proposed_action": {"type":["object","null"],"properties":{
      "tool":{"type":"string"},"args":{"type":"object"},
      "dry_run_first":{"const":true},"reversible":{"type":"boolean"}}},
    "first_seen": {"type":"string","format":"date-time"},
    "last_seen": {"type":"string","format":"date-time"},
    "status": {"enum":["new","open","approved","resolved","dismissed","snoozed","needs_input"]}
  }
}
```

## Appendix C — Account registry seed (Monarch account ids)

| id | name | registry state |
|---|---|---|
| 183541300701576552 | AgentSync 401(k) Plan (…0768) | expected_carry_forward |
| 169483278367997389 | 15 Branwood HELOC (8300) | expected_carry_forward (closed 2026-08-11) |
| 169487170068692772 | Syosset 403b (…665Y71, Finicity) | expected_carry_forward; never_exclude |
| 233056643597614366 | Syosset 403b (…5Y71, MX) | live |
| 171945926909115454 | Savings (Apple, old entity) | expected_carry_forward_pending_confirm |
| 253413397873179968 | Savings (new entity) | new — confirm re-link (I-8) |
| 251845388366920938 | Home Equity Line of Credit (…2800) | live |
| 233056032900660408 | American Express Gold Card (…1002) | live; amex_float |
| 206920423728627477 | SoFi Student Loan Refinance (…7625) | live |
| 169484667006631843 | Tracey Student Loan | excluded_duplicate |
| 237313796790435071 | Braden 529 (01) | live; **watch_carry_forward** |
| 237313797006441728 | Lexi 529 (02) | live; **watch_carry_forward** |
| 237313797074599169 | Colton 529 (03) | live; **watch_carry_forward** |
| 237313797134368002 | Dylan 529 (04) | live; **watch_carry_forward** |
| 237313797209865475 | Elaina 529 (05) | live; **watch_carry_forward** |
| 169485859791191359 … 169485860105764163 | 529 old entities (01–05) | excluded_legacy |
| 169483620903173697 / 233056033218378937 | Corporate Card (…1002) / (…1009) | excluded |

---

## Appendix D — Kickoff prompt for Claude Code

> Read `docs/STEWARD_SPEC.md` end to end. It's the approved design and the operational context from my Cowork sessions. Start with Phase 0 and work through the phases in order, committing per phase with tests green. Use the §12 defaults instead of asking me; flag them in your summary. Never write to live Monarch — fixtures only. When you finish (or hit something only I can do), give me the §11 hand-back list.
