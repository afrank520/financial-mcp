# 529 provider-succession arc — a live natural experiment

This note documents a rare, controllable real-world scenario playing out across
one household's five 529 college-savings accounts. It is the reference case for
two planned Steward increments — the **I-6 follow-up #7 (frozen-value detector)**
and **#8 (dollars-aware trust gate)** — and a live-prove of the already-shipped
I-6 (stale feed) and I-9 (duplicate/superseded holding) detectors.

> **No real financial figures live in this file.** Balances, account ids, and the
> full findings capture are kept in the gitignored companion fixture
> `tests/fixtures/529-arc-baseline-2026-09-25.local.json` (matches the
> `*.local.json` rule, same as the net-worth golden). This doc is safe to commit.

## Why this is worth documenting

The same five real-world 529 accounts have migrated across **three data-provider
generations** inside Monarch. That gives us a naturally-labelled dataset: three
copies of the same holdings, each frozen at a different point in the connection
lifecycle. Provider re-link churn is not unique to this household or this
institution — it is a recurring failure mode for any aggregator-fed account, so
what we learn here generalizes.

## The three generations

| Generation | Aggregator | In net worth? | Data state | Role in the experiment |
|---|---|---|---|---|
| **Plaid Old** | Plaid | No (zeroed + excluded) | Balances went to $0; provider outage, no ETA; ~7 months stale | The "fully dead, correctly handled" control. Monarch excluded these and I-3/I-6/I-8 auto-resolved them. |
| **MX Old** | MX | **Yes** | Last real data pull froze ~11 days before capture; balances stale-**high** | The "stale-but-still-in-net-worth" distortion. This is what I-6 catches today and what #7 must keep catching after a re-auth. |
| **MX New** | MX | **Yes** | Freshest pull (~2 days); awaiting a current 2FA to fully refresh | The live successor. Its connection needs re-auth, but its data is current. |

Because **both MX Old and MX New count in net worth simultaneously**, the five
holdings are double-counted — a live, material net-worth overstatement equal to
the stale MX Old total. (Exact dollars in the fixture.)

## Two clocks, and why timestamps alone will eventually lie

`get_accounts` exposes two dates per account:

- **`snapshot_date`** — stamped **today** for every in-net-worth account, even a
  frozen one. Monarch carries the last known value forward and dates it today.
  This is the trap: a frozen balance *looks* current.
- **`last_updated`** — the real last data pull. This is what I-6's freshness
  window (`i6_stale_days`, default 7) tests.

Today, `last_updated` cleanly separates the generations (MX Old ~11 days = stale;
MX New ~2 days = fresh). **The blind spot arrives on re-auth:** when the 2FA is
refreshed, Monarch has been observed to bump `last_updated` *without* pulling new
balances. At that instant the timestamp looks fresh while the value is still
frozen — I-6 goes quiet and only a **value-flatline** signal (the balance itself
not moving despite a "fresh" timestamp) would still catch it. That is the reason
#7 exists.

## The lifecycle arc we intend to instrument

Each transition changes what the detectors should see. Capturing a baseline at
each state turns this into a repeatable test set.

| # | State | What changed | Expected detector behaviour |
|---|---|---|---|
| S0 | **BEFORE** (captured 2026-09-25) ✅ | MX Old frozen ~11d; MX New fresh but awaiting 2FA; both in NW | I-6 fires on all live 529s; I-9 flags all MX Old↔MX New pairs; `trust` = degraded |
| S1 | **After exclude** (captured 2026-09-25) ✅ | MX Old copies excluded from net worth (owner action in Monarch); balances/timestamps untouched | I-9 clears; net-worth overstatement removed; `trust` still degraded (MX New re-auth) |
| S2 | **After 2FA refresh** | MX New re-authed; `last_updated` bumps | I-6 clears on MX New; **#7 tests whether a bumped timestamp masks a still-frozen value** |
| S3 | **Provider expiry (later)** | MX 2FA lapses again (provider holds it only a few days) | Connection degrades again; the cycle repeats — the durable test of #7's value-flatline signal |

S0 and S1 are captured (fixtures `529-arc-baseline-2026-09-25.local.json` and
`529-arc-s1-exclude-2026-09-25.local.json`). S2–S3 are captured as the owner
drives the real accounts through each transition; each becomes a dated
`*.local.json` sibling of the baseline.

## S1 live-prove — the detect→fix→verify loop, end-to-end on real money (2026-09-25)

The owner flipped "Exclude account balance" ON for the five MX Old 529s (nothing
else — no Close, no Delete; balances and timestamps left frozen). The re-run
matched the prediction **exactly, at the finding-id level**:

- **10 findings auto-resolved** — precisely the five I-9 double-counts and the
  five MX-Old I-6 findings we predicted would clear. Both detectors gate on
  `include_in_net_worth`, so a single owner action cleared both classes at once.
- **The five MX New I-6 findings persisted** — correct: they still count in net
  worth and still await re-auth (`update_required`). Excluding the *old* copies
  does not un-break the *new* connection.
- **`trust` stayed `degraded`** — now held by the MX New re-auth need (plus some
  savings feeds and the $0 Apple Cash), **not** by any double-count. This is the
  honest read: the *distortion* is fixed, the *connection* is not. Two separate
  problems, tracked independently.
- **Ledger identity held** across the run (auto-resolve on condition-cleared,
  keyed per account) — validating the reconcile path on live data.

This is the first live-proof that the Steward closes the loop, not just detects:
a real ~$200K net-worth overstatement was surfaced, fixed by an owner action, and
verified gone on re-check — while the frozen source data was preserved for #7.

## Live-prove results at S0 (2026-09-25)

The shipped detectors were validated against this real state:

- **I-9 (duplicate/superseded holding):** fired on all five MX Old↔MX New pairs,
  high confidence, correctly naming each pair and the double-counted amount.
- **I-6 (stale feed):** fired on all ten live 529 accounts (both MX generations),
  high confidence — MX Old via staleness, MX New via connection-needs-reauth.
- **`trust` flag:** correctly `degraded` in the presence of the distortion.
- **Ledger identity:** the live accounts recorded as `new` rows; the retired
  Plaid Old and dead generations auto-`resolved`. Identity is stable across runs.

### Two refinements this surfaced (feed into #7 / #8)

1. **$0 double-count noise.** I-9 also fired on an Apple Cash pair where the
   double-counted amount is $0.00 — technically true, materially meaningless.
   This is the canonical case for **#8 (dollars-aware gate):** below a materiality
   threshold, a finding should not consume attention or degrade trust.
2. **"Frozen/broken feed" wording on fresh-but-unauthed data.** I-6 labelled the
   MX New accounts a "frozen/broken feed" even though their data is the freshest
   we have — they merely await re-auth. *Awaiting-reauth-but-still-fresh* is a
   gentler state than *stale-and-frozen* and deserves distinct severity/wording.

## Handling guidance for these accounts

- **Do not delete any generation.** Plaid Old is the deepest history and a perfect
  "fully dead" control; MX Old is the current source of truth until MX New proves
  it is pulling. Deletion also destroys the natural experiment.
- **Exclude, don't delete,** to fix the double-count: remove the stale copies from
  net worth (S1) rather than deleting the accounts.
- **Encode the succession mapping** (old→new account lineage per child) so
  net-worth-trend and history stay continuous across the migrations. This belongs
  in the future `financial_context` store; until then it lives here and in the
  account registry.
