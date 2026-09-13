> **Snapshot as of 2026-09-01/02. Background only — NOT a task list.**
> Read for context on *why* a rule exists. Current instructions live in
> `docs/STEWARD_SPEC.md`; where this file conflicts with the spec, the spec wins.

# Monarch MCP — Outage Post-Mortem, Net-Worth Semantics & Account Audit
_Last updated 2026-09-02_

**Status: RESOLVED.** Live mode restored 2026-09-01 20:08. All seven cache types
`source: live` and fresh. Accounts 36 → 42, transactions 26,885 → 27,652,
net worth series 909 → 931 points. Net worth 2026-09-01: **$2,092,237.35**.

---

## 1. OUTAGE ROOT CAUSE (fixed)
`~/.financial_mcp/session.json` did not exist. `config.yaml` sets `source: live`
with the default file credential store at that path; with no session file every
startup logged `no Monarch session — run financial-mcp-login; falling back to CSV`.

- through **2026-08-27 09:30** — `starting (source=live)`
- **2026-08-28 10:41** → **2026-09-01 19:58** — `source=csv` on every start
- **2026-09-01 20:08** — `starting (source=live)` after re-login + restart

Ordinary session expiry, coinciding with the v1.1.0 packaging work of 8/27.

**Fix:** `./.venv/bin/financial-mcp-login` (email + password — Monarch's bot
detection blocks the Google button), then **restart the MCP server**. `source` is
evaluated once at startup; a running process will not pick up a new session.

---

## 2. THE CENTRAL FINDING: how Monarch actually computes net worth

```
net_worth = Σ( balance )  for accounts where
              include_in_net_worth == true
              AND NOT is_carry_forward
```

`is_carry_forward: true` means the account has no fresh snapshot and Monarch is
repeating its last known balance. **Those balances are excluded from net worth.**

Verified to the penny at three independent dates:

| Date | Σ(include ∧ ¬carry-forward) | Monarch reported | Residual |
|---|---:|---:|---:|
| 2026-03-01 | $1,718,936.28 | $1,718,936.28 | $0.00 |
| 2026-09-01 | $2,092,237.35 | $2,092,237.35 | $0.00 |
| 2025-09-01 | $1,623,956.88 | $1,623,956.88 | $0.00 |

Naively summing `include_in_net_worth` alone overstated the 2026-09-01 figure by
**$436,557.87** across four carry-forward accounts (AgentSync $581,399.26 stuck
2025-07-21; HELOC 8300 −$235,297.14 stuck 2026-08-10; 403b …665Y71 $89,800.07
stuck 2026-01-15; old Savings $655.68 stuck 2026-08-03).

**This is the single most important rule for the replacement service.** Monarch's
own engine already handles dead accounts correctly and automatically — an account
stops counting the day it stops reporting. No cleanup is required to make net
worth correct.

---

## 3. ⚠️ "Exclude account balance" IS RETROACTIVE — DO NOT USE IT

Monarch's per-account **Exclude account balance** toggle is not date-scoped. It
removes the account from the **entire historical net-worth series**, silently
restating the past.

Measured live during this session (toggling Exclude on HELOC …8300, then reverting):

| Date | Before | With Exclude ON | Δ | After revert |
|---|---:|---:|---:|---:|
| 2025-09-01 | $1,623,956.88 | $1,788,832.95 | **+$164,876.07** | $1,623,956.88 ✓ |
| 2026-03-01 | $1,718,936.28 | $1,908,770.55 | **+$189,834.27** | $1,718,936.28 ✓ |
| 2026-09-01 | $2,092,237.35 | $2,092,237.35 | $0.00 | $2,092,237.35 ✓ |

Each delta equals that account's balance on that date. Today's number is
unaffected (the account was already carry-forward), which is exactly what makes
the damage easy to miss.

**Operating rules:**
- **Hide account** — cosmetic only, removes it from the Accounts page. Safe.
- **Exclude account balance** — rewrites history. Use ONLY for an account that
  should never have counted at all (a true duplicate that double-counted from
  day one). Never for an account that once held real money or real debt.
- **Hide transactions** — pulls historical transactions out of cash flow and
  budgets. Leave off.
- **Delete** — destroys balance history. Never.

**Corrected guidance (supersedes the earlier "cleanup list" in this doc):**
no Monarch UI cleanup is needed or wanted. AgentSync, HELOC …8300 and
403b …665Y71 should all keep `Exclude` **OFF**. The flag problem is a defect in
the MCP's net-worth math, to be fixed in code.

---

## 4. Account-level findings

**Tracey Student Loan — resolved, not a missing debt.** MOHELA sold its book back
to SoFi, which became servicer. Transaction trail shows exactly one payment
stream, never two: MOHELA **$568.13/mo** ("INDN: FRANK, TRACEY") through 2025-01,
then SoFi **$544.76/mo** ("INDN: TRACEY FRANK") from 2025-02 through 2026-08 —
payment dropped **$23.37/mo** ($280/yr). The "Tracey Student Loan" entity is a
stale duplicate of `SoFi Student Loan Refinance (...7625)`; already
`include_in_net_worth: false`. Correctly handled.

**HELOC restructure.** Old `15 Branwood HELOC (8300)` closed 2026-08-11 (carry-
forward from 08-10), paid off by new `Home Equity Line of Credit (...2800)`,
$350k limit, drawn to $250,000. Net new secured debt ≈ **$25k**, not $250k. The
round number is deliberate — a temporary cash floor against unplanned July/August
expenses through the Aug→Sep transition.

**Syosset 403b (Voya) — same account, two entities.** Data provider switched
**Finicity → MX**. `…665Y71` (Finicity) holds the only history from 2025-01-01
($74,001.07) through 2026-01-15 ($89,800.07); `…5Y71` (MX) has no snapshot before
that and is live at $109,097.40. Excluding the old one would blow a $74–90k hole
in 2025 history. **Leave Exclude OFF.**

**529 plans — chronic aggregator problem, leave alone.** On ~2026-03-03 Monarch
migrated all five to new entities (ids `2373137*`) and set the old copies
(`1694858*`) to `include_in_net_worth: false`. `Alexis 529 (02)` → `Lexi 529 (02)`
was part of that migration — a rename, not a duplicate. Because the old entities
are excluded and the new ones have no history before 2026-03-04, roughly **$181k
of 529 value is missing from net worth for all of 2025 and early 2026** — this is
the source of the unexplained **+$156,061.82 jump on 2026-03-04**.

That migration was itself the fix for an earlier 529 provider switch, and as of
2026-09 the current 529 aggregator shows **"Connection unavailable"**.
**Decision: leave it as-is.** Reconfiguring on top of a broken connection risks
compounding the problem.

> **⚠️ FORWARD RISK.** The five new 529 entities are currently live
> (`is_carry_forward: false`, snapshot 2026-09-01, **$200,704.89** combined). If
> the aggregator outage persists, they will go carry-forward — and by the rule in
> §2, Monarch will **silently drop $200,705 out of net worth** with no error and
> no notification. Expect an unexplained ~$200k drop if the connection stays
> down. This is the strongest argument for a carry-forward monitor (§5.1).

**Other already-excluded accounts:** `Corporate Card (...1002)` (dead since
2024-06-20) and `(...1009)` — both `include_in_net_worth: false`. No action.

---

## 5. Service defects to fix in the rebuild

**5.1 Net-worth math + carry-forward monitoring (highest value)**
- Implement the §2 formula. Never sum `include_in_net_worth` alone.
- **Alert when any account transitions to `is_carry_forward: true`** — that event
  silently changes net worth and is invisible in every current tool. It is how
  $436k of ghosts accumulated unnoticed, and how the 529s will vanish if the
  outage persists.

**5.2 `get_accounts` is unreliable — highest-severity bug**
- Reports `$0` for accounts holding real balances (Coinbase read $0 against a
  real $4,510.98; several stale accounts read $0 against real carry-forward
  balances). It produced a wrong balance sheet mid-session.
- Forges `as_of` per row — stamps the current date regardless of the true
  `snapshot_date`.
- Exposes no `account_id`, no `include_in_net_worth`, no `is_carry_forward`,
  making duplicates impossible to resolve.
- Returns 42 rows for ~26 real accounts.
- **`balance_delta` / `balance_as_of` are the trustworthy endpoints** — they carry
  `account_id`, `include_in_net_worth`, `snapshot_date`, `is_carry_forward`.
  Model `get_accounts` on them.

**5.3 Silent failure handling**
- `refresh_cache` returns `{"refreshed": "<type>"}` on success *and* failure,
  logging only a WARNING. Surface failures to the caller.
- On failure it writes **epoch-0 timestamps** — networth reported
  `as_of: 1970-01-01T00:00:00Z` (age ~57 years).
- Freshness metadata times the cache write, not the data: `stale: false,
  age_hours: 0.0` over balances dated 2026-06-15.
- CSV fallback silently drops budgets entirely (`budgets are not available in
  CSV mode`). Fail loudly instead of degrading in silence.

**5.4 Data handling**
- No transaction dedupe on id — `1edb1611a72c62138c75c2ec26affd4c9ab77c4d`
  (New York / Charity / −$220) returned 5×.
- Startup logged twice on nearly every launch — double-init or two instances.

**5.5 Housekeeping**
- `financial-mcp-doctor` missing from `.venv/bin` (entry points registered at
  v0.1.0, before `edbab2c` added it). Fix with `pip install -e .`.
- Stray directory `Monarch\ MCP` (literal backslash) in `~/projects/Personal`.

---

## 6. ENVIRONMENT NOTE (do not re-diagnose)
`device_bash` runs in a **Linux VM** with the user's folders mounted, not on
macOS. macOS paths (`/opt/homebrew/...`) do not resolve and the venv's
`python3.14` symlink appears dangling. **Sandbox artifact, not a broken venv.**
The agent can read/edit project files but cannot execute the macOS venv or the
login command. The install is *editable*, so live v1.1.0 source runs.

---

## 7. Balance sheet — 2026-09-01 (live)
**Net worth $2,092,237.35** · +$24,044 since 6/15 · +$468,280 over 12 months

| Group | Total |
|---|---:|
| Real estate (15 Branwood Drive) | $2,234,300.00 |
| Retirement (SF 401k $727,175.87 + 403b …5Y71 $109,097.40) | $836,273.27 |
| 529s (Colton, Lexi, Braden, Elaina, Dylan) | $200,704.89 |
| Cash (9 accounts) | $81,922.81 |
| Investments (Coinbase $4,510.98 + Robinhood $85.81) | $4,596.79 |
| **Liabilities** | **−$1,265,560.41** |

Liabilities: mortgage −$940,542.39 · HELOC …2800 −$250,000.00 ·
SoFi −$57,014.79 · Amex Gold −$17,916.24 · AT&T −$86.99.
(Corporate Card …1009 −$579.80 excluded from net worth.)

**Amex Gold** ($6,777 → $17,916) is expected, not revolving debt: deliberately
not paid incrementally during the month; autopay fires ~4–5 days before the due
date. Treat a high mid-month balance as normal.
