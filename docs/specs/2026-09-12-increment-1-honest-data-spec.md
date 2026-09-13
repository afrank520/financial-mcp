# Increment 1 — "Honest Data" (implementation-ready spec)

_Branch: `spec/inc1-honest-data`. Authored 2026-09-12. Grounded against current
code (file:line references verified against `main` at `23c063d`)._

> **North-star context:** this is the first slice of the Monarch Steward roadmap
> (`docs/Monarch Claude Docs/MONARCH_STEWARD_SPEC.md`). Increment 1 does **not**
> build the Steward, the findings ledger, the policy layer, or any advisor. It
> makes the numbers the existing tools already report **stop lying**, because
> every downstream capability — detectors, context, advice — reasons over these
> numbers. **Trust before advice.**

---

## 0. The one hard boundary

This increment is **read-only against Monarch**. No tool added or changed here
mutates Monarch data. The only live network calls are read-only, and the only one
required is a **one-time recorded-fixture capture** (Task 3) that must run on
Adam's Mac against a valid session. All other development and every test run
against fixtures. No `Exclude`, `Hide`, or `Delete` is ever set. Nothing under
`~/.financial_mcp/` is touched except the cache DB the server already owns;
`session.json` is never read or written by this code.

Open-core placement: everything here **fixes or extends existing free-base
tools**. It does not cross into the reserved advisor layer.

---

## 1. Goal & acceptance

After this increment, a caller can trust four things that are currently wrong:

1. `get_accounts` reports **real balances** and carries `account_id`,
   `include_in_net_worth`, `is_carry_forward`, and a **true `snapshot_date`**.
2. A single **carry-forward-aware net-worth** function exists and reproduces
   Monarch's own number to **$0.00** on three independently verified dates.
3. Every tool result declares its **`source`** (`live`/`csv`) and never reports a
   **1970 (`epoch-0`) `as_of`** or a fabricated freshness.
4. `refresh_cache` **fails loudly** — it reports which types actually refreshed
   and which failed, and a failure never leaves a fabricated timestamp behind.

**Definition of done:** all four provable by tests; the three golden net-worth
dates pinned as regression tests; existing ~390 tests still green; `pytest`
clean via `.venv/bin/python -m pytest`.

---

## 2. Scope

**In (the six items):** honest `get_accounts` (D-1); a shared carry-forward-aware
net-worth function + a `net_worth` tool + fixing the `savings.py` carry-forward
omission (D-6); `source` in the envelope + epoch-0 elimination (D-3/D-4);
`health_check` degraded surface (D-4); honest `refresh_cache` (D-2); transaction
dedupe (D-5).

**Out (deferred to later increments, roadmap stub in §7):** the findings ledger,
ranker, `steward_run`, the `policy.yaml` engine, the narration skill (all
Increment 3 / advisor layer); `list_rules`/`delete_rule`/`regroup_category` and
notes/tags writes (Increment 2 — they need the GraphQL write spike and follow the
two-phase write pattern). The **automated** carry-forward transition monitor (I-3)
is Increment 3 because it needs the ledger to remember state across runs — but see
§6, this increment **enables the on-demand check now**.

---

## 3. Grounded defect → fix map

Each fix cites the current code and the trustworthy pattern to model on.

### D-1 — `get_accounts` reports `$0`, forges `as_of`, hides the trust fields

- **Now:** `mcp_server.py:92-100` → `svc.accounts()` → `CommunityAdapter._to_account`
  (`community_adapter.py:67-72`) builds `Decimal(str(d.get("currentBalance") or 0))`,
  ignoring `displayBalance` (both are fetched, `queries.py:33-34`), and drops the
  `id` + `includeInNetWorth` the query already returns (`queries.py:23,35`). `as_of`
  is stamped `self._today()` (`community_adapter.py:176`). The `Account` model has
  only `name, latest_balance, as_of` (`models.py:38-41`).
- **Trustworthy pattern:** `analytics/balances.py:17-48` (`balance_as_of`) already
  emits `account_id, display_name, type_name, include_in_net_worth, balance,
  snapshot_date`, and derives `is_carry_forward = chosen.date != target_date`
  (`balances.py:43-45`) from real per-snapshot data (`BalanceSnapshot`,
  `models.py:44-50`; fetched at `community_adapter.py:207-228`).
- **Fix (Decision 1, HYBRID — revised after review):** take the **full account
  list** + `id` + `include_in_net_worth` + signed balance (`displayBalance ??
  currentBalance`) from `GetAccounts` (`community_adapter.py:174-177`), then
  **enrich** each row's `is_carry_forward` / `snapshot_date` from the balance-history
  snapshots where a series exists, defaulting `is_carry_forward=False` /
  `snapshot_date=None` where it does not. Extend the `Account` model with
  `account_id: str`, `include_in_net_worth: bool`, `is_carry_forward: bool`,
  `snapshot_date: date | None`; source `latest_balance` from `displayBalance ??
  currentBalance` (fixes `$0`), keep `name` for back-compat.
  - **Why hybrid, not snapshot-only:** a snapshot-only rebuild silently **drops
    every account with no snapshot series** — `get_balance_history` emits a
    `BalanceSnapshot` only inside `for snap in data.get("snapshots") or []:`
    (`community_adapter.py:218`) and `balance_as_of` groups strictly by the
    account_ids present in snapshots (`balances.py:18-22`). Brand-new / manual /
    zero-activity accounts (Monarch routinely mints these — the MX 403b, the five
    529s, per status §4) would vanish from `get_accounts`, trading "shows `$0`
    wrongly" for "does not show at all" — the same class of dishonesty this
    increment exists to kill. The hybrid keeps `GetAccounts`' full coverage while
    adding honest carry-forward. **Task 1 must assert account-count parity vs
    `GetAccounts`.**
  - **Caveat to comment in code:** `is_carry_forward = (chosen.date != target_date)`
    (`balances.py:43-45`) is a proxy for "no fresh snapshot"; it is penny-verified
    on the three pinned dates but could misclassify an account that reports on a lag
    near "today". Note it; do not re-derive it here.
- **CSV path:** `CsvAdapter.get_accounts` (`csv_adapter.py:77-86`) must emit the
  same extended shape (`is_carry_forward=False`, `snapshot_date=b.date`,
  `include_in_net_worth=True`) so the model is uniform across sources.

### D-6 — no carry-forward-aware net-worth; `savings.py` omits the guard

- **Now:** `networth_trend` (`mcp_server.py:602-628`) reads Monarch's authoritative
  aggregate series (`analytics/networth.py:12-35`) — **correct, leave it**. The
  only local balance summation is `analytics/savings.py:43-67`, which skips
  `include_in_net_worth==false` (`savings.py:48`) but **never inspects
  `is_carry_forward`**, silently mixing stale carried snapshots into a delta.
- **Fix:**
  1. Add a pure function `net_worth_as_of(snapshots, target_date) -> dict` (new,
     in `analytics/networth.py`), defined exactly as:
     `net_worth = Σ balance` over `balance_as_of(target_date)` rows where
     `include_in_net_worth == true AND is_carry_forward == false`. Return the
     level **plus** the per-account breakdown and an `excluded` list tagging each
     excluded account with reason (`not_in_net_worth` | `carry_forward`).
  2. Expose it as a new read-only MCP tool `net_worth(as_of: str | None = None)`
     (default today), wrapped in the standard envelope. This is what powers the
     on-demand carry-forward check in §6.
  3. ~~Fix `savings.py:48` to also skip snapshots that are `is_carry_forward` at the
     window endpoint.~~ **DEFERRED during build (2026-09-12).** The Decision 3 gate
     requires pinning the known-good **live** June 2026 number before and after to
     prove zero drift; that number came from a live pull, and the synthetic
     `test_savings_analytics.py` fixtures do not reproduce it, so non-drift cannot be
     verified in a read-only build session without live data — which would couple this
     to Task 3's live pull and add regression risk to a live-proven output. It is
     explicitly **not** one of the four acceptance items, and the actual level-inflation
     defect ($436k carry-forward) is fully fixed by `net_worth_as_of` + the `net_worth`
     tool (items 1–2). Savings reconciliation is a delta-over-a-window — a different
     object — where a carry-forward endpoint's effect is bounded. Revisit alongside a
     future live pull if it proves to matter. See §6b.

### D-3 / D-4 — dishonest freshness; no `source`; epoch-0 leak

- **Now:** `_envelope` (`mcp_server.py:42-58`) and `_live_envelope`
  (`mcp_server.py:60-73`) emit `{as_of, age_hours, stale, count, data}` — **no
  `source`**. `_live_envelope` unconditionally stamps `as_of = now`,
  `age_hours = 0.0`, `stale = False`. Epoch-0 origin: `SqliteCache.invalidate` sets
  `last_refresh = 0` (`sqlite_cache.py:132-136`); if the refetch then fails,
  `DataService._get` serves stale without re-storing (`data_service.py:36-42`), so
  `freshness` computes `as_of = fromtimestamp(0) = "1970-01-01…"`
  (`sqlite_cache.py:117-122`).
- **Fix:**
  1. Thread the **effective adapter source** into `build_server` and add
     `"source"` to **both** envelope builders. Source must come from the adapter
     actually in use (add `adapter.source_name()` → `"live"`/`"csv"`), **not**
     `Config.source` — the whole 8/28–9/1 outage was `Config.source=live` while
     `build_adapter` had silently fallen back to CSV (`main.py:29-36`). Keying on
     `Config.source` would reproduce the exact dishonesty this fix targets.
  2. In `SqliteCache.freshness` (`sqlite_cache.py:105-130`), treat `last_refresh in
     (None, 0)` as **never successfully refreshed**: report `as_of=None`,
     `age_hours=None`, `stale=True` — never a 1970 timestamp. Same for
     `status()`/`cache_status` (`sqlite_cache.py:138-153`).

### D-4 — `health_check` always lies "ok"

- **Now:** a `health_check()` tool **already exists** (`mcp_server.py:87-90`) and
  unconditionally returns `{"status": "ok", ...}` — it can never report degraded.
  That always-"ok" health check is itself an "honest data" defect. Per-type
  freshness is separately inspectable via `cache_status` (`mcp_server.py:711-717`).
- **Fix (Decision 5):** **make the existing `health_check` honest** (do not add a
  second tool — names/tests would collide). Return `{status: "ok"|"degraded",
  source, reasons: [...]}`; `degraded` when the **effective** source (see D-3 fix,
  `adapter.source_name()`) is not `live`, or any type is stale / never-refreshed.
  Keep `cache_status` as-is. This is the single call a caller (or a later Steward
  preflight) makes to know whether to trust the session — the direct fix for the
  silent 8/28–9/1 CSV-fallback outage (`monarch-mcp-status-2026-09-01.md` §1).

### D-2 — `refresh_cache` lies on failure and leaves epoch-0

- **Now:** `mcp_server.py:719-730` calls `svc.refresh` (not wrapped in `_guard`)
  and always returns `{"refreshed": …}`; `svc.refresh` invalidates up-front
  (`data_service.py:89-90`) so a failed refetch leaves `last_refresh=0`.
- **Fix:** `svc.refresh` returns a per-type outcome (`refreshed: [...]`,
  `failed: [{type, error}]`); on a type's refetch failure, **restore its prior
  `last_refresh`** instead of leaving 0 (no fabricated 1970). `refresh_cache`
  wraps in `_guard` and returns `{"refreshed": [...], "failed": [...], "source"}`.
  Model the error discrimination on the `recategorize` `{changed/failed}` result
  shape (`mcp_server.py:759-764`).

### D-5 — no transaction dedupe

- **Now:** `CommunityAdapter.get_transactions` paginates with `out.extend(...)`
  and `offset += len(results)` (`community_adapter.py:185-205`) — an overlapping
  page repeats an `id`. Tool layer (`mcp_server.py:102-149`) never collapses. CSV
  `synth_id` (`models.py:24-29`, sha1 of `date|merchant|amount|original_statement`)
  collides two genuinely-distinct identical-looking txns.
- **Fix:** dedupe by `id` inside the pagination loop (track a `seen` set, skip
  repeats). For CSV (Decision 4): add a positional ordinal to `synth_id` so two
  identical-looking rows stay distinct rather than silently summing as one; low
  risk (CSV is offline/test mode; the live undo journal keys on Monarch ids, not
  synth ids).

---

## 4. Decisions locked (veto any at approval)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | `get_accounts` route | **HYBRID** — full list from `GetAccounts`, enrich carry-forward from snapshots | Snapshot-only silently drops zero-snapshot accounts (regression); hybrid keeps full coverage + honest carry-forward. **Assert account-count parity.** |
| 2 | Expose net worth as a tool | **Yes — new `net_worth` tool** | Powers the on-demand carry-forward check (§6); lets us cross-check Monarch's series |
| 3 | Fix `savings.py` carry-forward omission | **DEFERRED at build (2026-09-12)** | Gate needed the live June number pinned before/after; unverifiable in a read-only session, not an acceptance item, and the level-inflation defect is fixed by the `net_worth` tool. See D-6 item 3 / §6b |
| 4 | CSV `synth_id` collision | **Add positional ordinal, back-compat signature** | Prevents silently summing two real txns; must NOT break the live id fallback (`community_adapter.py:81`); low-priority offline-only |
| 5 | Health surface | **Make the existing `health_check` honest** (not a new tool) | `health_check` already exists and always returns "ok"; must key on **effective** adapter source |

---

## 5. Task breakdown (SDD)

1. **Account model + HYBRID `get_accounts`** (D-1). Extend `Account`; full list
   from `GetAccounts` with `displayBalance ?? currentBalance`, enriched with
   `is_carry_forward`/`snapshot_date` from snapshots where present; update CSV path;
   envelope against the **`"accounts"`** cache type (which the hybrid still stores,
   avoiding the Task-4 perpetual-stale interaction). Tests: real balance (not `$0`),
   all four fields present, `is_carry_forward` true for a stale account,
   `snapshot_date` real not today, a zero-snapshot account still appears, and
   **account-count parity vs `GetAccounts`**.
2. **`net_worth_as_of` + `net_worth` tool** (D-6). Pure function with breakdown +
   `excluded` reasons; new tool. Unit tests: the formula; a **synthetic in-net-worth
   carry-forward account** proving the exclusion independent of live capture; no-snapshot
   handling; the tool's envelope/count/date-validation. **The `savings.py` guard was
   deferred** (Decision 3 gate unverifiable without live data — see D-6 item 3 / §6b).
3. **Golden net-worth fixtures + regression tests** (D-6 verification). **Requires
   a one-time read-only live pull on Adam's Mac** to record snapshot fixtures for
   the three dates, then pin: 2025-09-01 = `$1,623,956.88`, 2026-03-01 =
   `$1,718,936.28`, 2026-09-01 = `$2,092,237.35`, each **$0.00** residual vs
   Monarch. The fixture **must include the four in-net-worth-but-carry-forward
   accounts named in status §2** (AgentSync, HELOC 8300, 403b …665Y71, old
   Savings), and the test must assert on `net_worth_as_of`'s **`excluded` set/count
   and the ≈$436,557.87 excluded magnitude** — not just the residual-to-Monarch
   total — so a future regression that drops the filter can't slip through. Fixtures
   as inline dict payloads / JSON under `tests/fixtures` per existing convention.
4. **`source` in envelope + epoch-0 elimination** (D-3/D-4). Add
   `adapter.source_name()`; thread **effective** source into both envelopes; fix
   `freshness`/`status` to treat `last_refresh in (None,0)` as never-refreshed;
   tests: `source` present on live + csv; a failed/invalidated type reports
   `as_of=None, stale=True`, never 1970.
5. **Make `health_check` honest** (D-4). Rework the existing tool (not a new one) to
   return `ok`/`degraded` + reasons keyed on effective source; tests for live-ok,
   csv-degraded, stale-degraded.
6. **Honest `refresh_cache`** (D-2). Per-type outcome; restore prior timestamp on
   failure; `_guard` wrap; tests: partial-failure returns `failed[]`, no epoch-0
   left behind, typed errors surface as `{error}`.
7. **Transaction dedupe** (D-5). `seen`-set dedupe in pagination (the load-bearing
   fix — the proven `1edb…c4d`-returned-5× bug); CSV `synth_id` ordinal with a
   **back-compatible signature** so the live id fallback (`community_adapter.py:81`)
   is untouched; tests: overlapping live page yields no dup id; two identical-looking
   CSV rows stay distinct.

Each task: red test → implement → green, following the inline `FakeAdapter` /
injectable-`transport` mocking already used in `tests/test_mcp_tools.py` and
`tests/test_community_adapter.py`. No `conftest.py`; flat `tests/test_<area>.py`.

---

## 6. What Increment 1 unlocks now (the live $200k 529 risk)

The automated carry-forward **monitor** (I-3) is Increment 3 (needs the ledger).
But honest `get_accounts` + the new `net_worth` tool make the **on-demand** check a
one-liner available the day this ships:

> "List any net-worth accounts currently `is_carry_forward: true`."

That surfaces the five 529 entities the moment they go stale — the documented
forward risk where Monarch would otherwise **silently drop `$200,704.89`** out of
net worth with no error (`monarch-mcp-status-2026-09-01.md` §4). Interim mitigation
until the monitor is automated.

---

## 6b. Knowingly deferred (named so they aren't forgotten)

- **`savings.py` carry-forward guard (D-6 item 3).** Deferred at build (2026-09-12).
  Decision 3 gated it behind pinning the live-proven June 2026 savings number
  before and after; that number is not reproduced by the synthetic
  `test_savings_analytics.py` fixtures, so zero-drift is unverifiable in a
  read-only build session. It is not an acceptance item, and the level-inflation
  defect it relates to (the $436k carry-forward) is fully fixed by the new
  `net_worth` tool. A savings reconciliation delta is a different object — a
  carry-forward endpoint's effect is bounded (a dead cash/debt feed carries the
  same value at both endpoints → delta ≈ 0). Revisit alongside a future live pull.
- **Double startup logging (status §5.4).** Deferred — observability, not
  data-trust. Flagged only because "logged twice" hints at possible double-init;
  if a second instance is confirmed it gets its own fix.
- **42 rows for ~26 real accounts (status §5.2).** D-1 adds `account_id` +
  `include_in_net_worth`, which makes the duplicates **resolvable**; actually
  collapsing them needs the account registry → **Increment 3**.
- **Budgets silently unavailable in CSV (status §5.3).** `CsvAdapter.get_budgets`
  raises a bare `RuntimeError` (`csv_adapter.py:100`) that `_guard` does not catch
  (`mcp_server.py:79` only catches the two auth errors) — so it surfaces as a raw
  tool error. The new honest `health_check` (source=csv → degraded) covers the
  *why*; routing CSV-unavailable tools through a clean degraded message is deferred.

## 7. Roadmap stub (not built here)

- **Increment 2 — "Editor's hands":** `list_rules` + `delete_rule` (rule discovery
  is currently a side channel), `regroup_category` (unblocks Camp Tuition,
  `$91,716`, the single largest distortion), notes/tags writes (unblocks safe
  Miss Sue's / Capital One consolidation). Each behind the existing two-phase
  (dry-run → `confirm_token` → execute) + undo-journal pattern (`writes.py`,
  `cache/undo_journal.py`). Gated by a GraphQL write spike per op.
- **Increment 3 — "The Steward" (advisor layer):** `policy.yaml` + account
  registry → findings ledger → detectors (incl. the automated carry-forward
  monitor I-3) → ranker → `steward_run` → narration skill.

---

## 8. Safety checklist (must hold at review)

- [ ] No tool added/changed mutates Monarch data.
- [ ] No `Exclude` / `Hide` / `Delete` anywhere.
- [ ] Only live calls are read-only; only Task 3 requires a live session (Adam's Mac).
- [ ] No reads/writes to `session.json`; no new files under `~/.financial_mcp/`
      beyond the existing cache DB.
- [ ] No credential/session values logged.
- [ ] Existing ~390 tests still green; new tests cover every fix above.
