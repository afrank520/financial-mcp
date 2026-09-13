> **Snapshot as of 2026-09-02. Background only — NOT a task list for Claude Code.**
> The open items below are Adam's to execute in the Monarch UI or via live MCP
> writes. Read this for context on *why* a rule exists. Current build
> instructions live in `docs/STEWARD_SPEC.md`; where this file conflicts with the
> spec, the spec wins.

# Monarch Data Maintenance — Prioritization Framework & Worklist
_Created 2026-09-02. Companion to `monarch-mcp-status-2026-09-01.md`._

## The prioritization metric

Ranking cleanup targets by dollars alone **actively misleads**. The largest-dollar
defects in this dataset ($865,575 across 250 transactions of generic-named
transfers) are already categorized correctly — renaming them fixes nothing.

```
Priority = Distortion × Dollars-at-stake ÷ Actions-to-fix
```

- **Distortion** (binary): does it land in the wrong bucket and change a number
  you'd actually read? If no, priority is zero regardless of dollar size.
- **Dollars-at-stake (V)**: total absolute value of affected transactions.
- **Actions-to-fix**: 1 for a category/merchant rule (fixes all N at once); N for
  per-transaction edits.

**Average transaction value (V/N) is a verification filter, not a priority
filter.** Use `avg ≥ $500` to decide *how carefully to eyeball a change*. In this
dataset the highest-average items ($3–5k transfers) were the ones already correct.
Secondary flag: `avg < $20` with `N > 100` = noise; rule-worthy at best.

## Baseline (2026-09-02)
3,223 merchants / 27,652 transactions; 2,949 uncurated. 871 merchants flagged as
generic artifacts or raw statement strings (1,874 txns, $1,143,059) — **verified
correctly categorized**, so cosmetic only. 107 categories; the **"Other" group is
a junk drawer** holding $63,495 across 968 txns spanning six real groups.

---

## ✅ COMPLETED 2026-09-02

| Fix | Detail | Batch |
|---|---|---|
| **E*TRADE → ESPP Sale Income** | $83,205.96 / 6 txns. Six 2018–19 Salesforce ESPP ACH transfers under **four different merchant names** (`E Trade Des` ×3, two raw `E*TRADE DES:…` strings, one mislabeled `Salesforce`), all in a catch-all. | `64e94a93` |
| **LIPA → Electric** | $1,413.40. July twin was Electric, August was Water — same merchant, same statement. | `ff971516` |
| **Morgan Stanley → RSU Stock Sale Income** | $11,678.60. `ACH CREDIT MSPBNA` — RSU sale proceeds sitting in `Transfer`. | `42fa5711` |
| **P J Nail → Personal** | $96.67. Barber already went to Personal. | `58f060d6` |
| **Dick's ×3 → Clothing** | $387.70 + $173.95 + $173.95 refund. Kids' back-to-school sneakers/hoodies. | `95349d12` |
| **LIPA merchant → PSEG** | 2 txns / $3,053.10 merged into existing PSEG (`171104321109114326`). | `350275f5` |
| **Merchant+category rule** | `253767790568029920`: contains `LIPA` → rename PSEG + set Electric. | — |

Verified: Miscellaneous 50 txns/**+$81,827.60** → 44 txns/**−$1,378.36**;
ESPP Sale Income 7/$154,904.62 → 13/**$238,110.58** ($154,904.62 + $83,205.96 =
$238,110.58 ✓). PSEG now 108 txns/$75,238.55, matching the Electric category.
Electric now reads as a clean seasonal curve: May $658 → Jun $922 → Jul $1,640 →
Aug $1,413.

**Root cause of the LIPA split — the recurring pattern in this dataset.** The
utility didn't change; its **ACH descriptor** did. Monarch minted a new merchant
and attached a hand-scoped rule (`250814342483557513`) that matched on **amount**
— fatal for a variable bill, which is why July categorized and August didn't. It
also matched on original-statement text, so renaming the merchant would **not**
have stopped it firing (the statement still reads `LIPA DES:DIRECTPAY`). User
deleted it and folded the category assignment into the new rule.

Same mechanism produced the Voya Finicity→MX split and the 529 migration.
**Provider-side descriptor changes are the #1 source of duplicates here.**

---

## ⏳ TIER 1 REMAINING — user action, Monarch UI

**Camp Tuition is in group "Other", belongs in "Children"** — $91,716 / 61 txns,
avg $1,504. Largest single distortion left: Camp Tuition alone ($91,716) is
nearly the size of the whole Children group ($91,852), so **every group-level
rollup is wrong**. Settings → Categories → Camp Tuition → group = Children.

## TIER 2 — real distortion, ~10 actions, ~$76k, 1,262 txns

Veterinary → Health & Wellness ($19,298/70) · "Kids Sports Equi**m**ent" typo
($14,664/133, **actively receiving new transactions**) · Subscriptions ×4 merge
($8,823/284) · Music → Entertainment ($8,663/314) · Home Insurance → Housing
($8,282/15, avg $552) · Business Services · Movies & DVDs · Books & Supplies →
Education · Laundry · Investments → Savings & Investments · Car Wash → Auto ·
"School Technology " trailing space · "Executive  Coaching" double space.

Most are blocked on the missing regroup tool; the three renames are MCP-doable.

## MERCHANT DUPLICATES — split by whether variants carry information

**SAFE to merge (~$197,900, 92 txns, 9 actions) — pure formatting noise:**
E*TRADE family → `E*TRADE` ($154,639/9) · `Transfer to Optima Platinum Card` →
`Transfer to Optima® Platinum Card®` ($24,836/8, split solely by the ® char) ·
`Mortgage Services` → `Mortgage Serv Ct` ($11,092/2) · `APPLE.COM/BILL…` →
`Apple` ($2,633/48) · `New York 529` → `New York's 529 College Savings Program`
($2,200/10) · Amex Payment, NY State DMV, Citi Field, `The Home Depot` ($2,606/15).

> `The Home Depot` took a **new** transaction 2026-08-29 — historical merges need
> a paired `create_merchant_rule` or they re-fragment.

**DO NOT MERGE — variants are load-bearing ($214,537):**
- **Miss Sue's, 46 records, $139,192/98.** Names encode child, month, school year,
  and payment type: `Miss Sues Tuition Elaina`, `Elaina Miss Sues Tuition`,
  `Miss Sues Deposit 2022-2023`, `Miss Sues Tuition November`, `Miss Sues Tips`.
  `notes` and `tags` are **empty**, so the merchant string is the only record.
- **Capital One, 11 records, $193,687/95.** The `CAPITAL ONE N.A. DES:CAPITALONE
  ID:XXXXX…02971 / 49062 / 49697 / 49437 / 48533` suffixes are the kids' savings
  account numbers. User merged these 2026-08-23 (`f5d736a2`) and **undid it** —
  correctly.

**Merchant names in this file double as a memo field.** Bulk merging is unsafe
until the detail can be preserved. A **notes/tags write tool** is the missing
piece that would make this class of cleanup possible.

**False positives from aggressive normalization — do not act on:** "Citi" swept
in 8 *Citi Field* ballpark charges; "New York" merged the 529 plan, the tax
department, and a veterinary specialist.

---

## OPEN DECISIONS
- **Barbeque World $81.56 — propane refills.** Not Home Services (that's
  contractors; propane is a product). Recommend **Natural Gas** so household fuel
  sits together, or a new **Propane** category under Bills & Utilities.
- **Walmart $343.33** — genuinely clothes + school supplies + snacks in one
  receipt. MCP can't split; Monarch UI can. Single-bucket fallback:
  **Kids School Supplies**.
- **Home Depot $29.11** — tracker batteries, currently Home Improvement →
  **Electronics**.

## OPEN QUESTION — not yet investigated
**Transfers group nets +$414,070**, should be ≈$0. Transfer +$1,917,784 (1,082) ·
Credit Card Payment −$1,536,121 (509) · HELOC Drawdown +$339,983 (24) · HELOC
Repayment −$275,114 (14) · Insurance Claim −$24,943 (77) · HELOC Monthly −$7,517.
Roughly **+$381,663 of unmatched inbound "Transfer"** remains. The Morgan Stanley
RSU fix above is the **first confirmed instance** of income misfiled as transfer —
check whether prior RSU/ESPP vests landed the same way.

---

## SERVICE GAPS PROVEN THIS SESSION

1. **No rule enumeration. No rule deletion.** `update_transaction_rule` requires a
   `rule_id` with no way to discover one. Rule `250814342483557513` was found only
   by accident — `create_category_rule` refused with *"a rule for this already
   exists"* and leaked the ID. That is a side channel, not an API. **Add
   `list_rules` and `delete_rule`.**
2. **`update_transaction_rule` is too narrow.** It refuses any rule matching on
   amount, statement text, account, or category — i.e. exactly the malformed
   rules most worth fixing. Correct to fail closed, but it leaves no path.
3. **No `regroup_category`.** Blocks the single highest-value category fix
   (Camp Tuition) and most of Tier 2.
4. **No notes/tags write.** Blocks safe consolidation of Miss Sue's and Capital
   One, where merchant names carry the only per-child attribution.
5. **No transaction split.** Mixed receipts (Walmart) can't be apportioned.
6. **Device timeouts on concurrent calls.** Parallel writes to the same device
   return "did not respond within 60s" **while still succeeding server-side** —
   three recategorizations reported timeout and all three had landed. Always
   check `undo_history` before retrying; blind retry double-applies.
