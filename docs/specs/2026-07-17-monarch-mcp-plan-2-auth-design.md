# Monarch MCP — Plan 2 Design: Live Auth & Sync

**Date:** 2026-07-17
**Author:** Adam Frank (with Claude)
**Status:** Approved design, pending implementation plan
**Parent spec:** `docs/specs/2026-07-15-monarch-mcp-server-design.md` (§4 adapter, §5 auth, §9 error handling)
**Builds on:** Plan 1 (`docs/plans/2026-07-15-monarch-mcp-plan-1-foundation.md`) — merged, 39 tests passing

---

## 1. Goal

Prove the credential + live-sync path end-to-end for the three entities Plan 1 already
models — **accounts, transactions, balances** — with Monarch credentials stored only in the
macOS Keychain and never exposed to Claude. This is the smallest surface that exercises real
auth and real network sync, kept in its own tightly-reviewable plan so the credential risk is
isolated from analytics work.

**Explicit roadmap note:** Plan 2 intentionally syncs only the three Plan-1 entities. Once the
live connection is confirmed working against the real account, **Plan 3 fans out** to pull much
more — budgets, cashflow, net worth, holdings, categories, merchants, recurring transactions,
subscriptions, forecasting — and all of it works live automatically because `CommunityAdapter`
already exists. Plan 3 adds models + analytics + tools + cache slots; it does not re-touch auth.

## 2. Corrections to the parent spec (discovered during research)

Research into the actual community library (github.com/hammem/monarchmoney, PyPI `monarchmoney`
v0.1.15, MIT, last commit Nov 2025) corrected three assumptions the 2026-07-15 spec baked in:

1. **No separate "MonarchMoneyCommunity" package exists.** The spec named a "fork with full MFA
   support." In reality the base `monarchmoney` library already provides MFA; the various
   `MonarchMoneyCommunity`-named GitHub forks track hammem's repo without a distinct PyPI
   release. → We pin **`monarchmoney==0.1.15`**.
2. **The persisted session is a token *only*** — not "token + device-uuid" as the spec assumed.
   The library pickles `{"token": ...}`. → `auth.py` stores exactly one secret in Keychain.
3. **The primary auth input is an auth *token*, not a raw `cookie:` header.** You copy the value
   from the `Authorization: Token XXXX` request header in browser DevTools. There is **no
   `trusted_device` flag**; sessions last "months" but Monarch has recently tightened long-lived
   sessions (occasional 403s). → We drop the spec's "reject short-lived tokens" language as
   unimplementable and instead handle expiry gracefully (§7).

Confirmed as specified: the library is **async** (validates the async→sync bridge decision),
token injection via `MonarchMoney(token=...)` / `mm.set_token(...)`, `get_transactions(limit,
offset, start_date, end_date, ...)` supports pagination (default 100/page), and
`get_account_history(account_id)` returns balance history.

## 3. Design decisions (locked with user)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Sync scope | **Accounts / transactions / balances only.** Richer entities → Plan 3. |
| 2 | Library | **Pin `monarchmoney==0.1.15`,** fully wrapped behind `CommunityAdapter`. |
| 3 | Adapter selection | **Explicit `source: csv \| live` config key,** default `csv`; live mode falls back to CSV with a **visible logged warning** when no valid session. |
| 4 | Async bridge | **Hidden inside the adapter via `asyncio.run()`** per call; `MonarchAdapter` interface stays synchronous; Plan 1 stack unchanged. |
| 5 | Testing | **Mocked library client + fake keyring backend** for the suite; plus a documented one-time **manual smoke test** against the real account for wire-shape confidence. |

## 4. Architecture

All new code is additive. The only Plan 1 files that change are `config.py` (two new keys),
`config.example.yaml`, `main.py` (adapter selection), and `.gitignore` (ignore the `.mm/`
session pickle). Models, cache, `DataService`, analytics, and all existing MCP tools are
untouched — the adapter interface is the seam that makes this possible.

### New files

```
financial_mcp/
├── monarch/
│   ├── auth.py               ← Keychain-backed token store (keyring lib)
│   └── community_adapter.py  ← CommunityAdapter(MonarchAdapter); wraps monarchmoney
├── login_cli.py              ← one-time terminal login (token paste | email+pw+MFA)
tests/
├── test_auth.py              ← fake keyring backend
└── test_community_adapter.py ← stub monarchmoney client (canned GraphQL dicts)
```

### `auth.py` — token storage

Thin wrapper over `keyring`, isolating all secret handling in one place:

- `save_token(token: str, service_name: str = DEFAULT_SERVICE) -> None`
- `load_token(service_name: str = DEFAULT_SERVICE) -> str | None` — `None` if no session stored.
- `delete_token(service_name: str = DEFAULT_SERVICE) -> None` — for logout / rotation.

Stores under a fixed username key (e.g. `"monarch_token"`) in the named keyring service. Never
logs the token value (masked via Plan 1's `mask()` if ever surfaced). Tests set an in-memory
fake keyring backend (`keyring.set_keyring(...)`), so no OS Keychain and no real secret touch
the suite.

### `community_adapter.py` — live adapter

`CommunityAdapter(token: str, service_name: str = DEFAULT_SERVICE)` implements the Plan-1
`MonarchAdapter` contract:

- `source_name() -> "live"`
- `get_accounts() -> list[Account]`
- `get_transactions() -> list[Transaction]` — paginates the library's `get_transactions`
  (100/page via `limit`/`offset`) until exhausted, so the full history is returned to match the
  CSV adapter's behavior. Maps each library transaction dict → our `Transaction` model
  (synthesizing `id` from the library's stable transaction id, or `Transaction.synth_id` if the
  library id is absent).
- `get_balances() -> list[Balance]` — derives balance rows from `get_accounts()` (current
  balance per account, stamped with today's date). Historical `get_account_history` is deferred
  to Plan 3; Plan 1's `Balance` shape (date, balance, account) is satisfied by current balances.

**Async bridge:** each sync method builds a `MonarchMoney(token=...)` client and runs its async
calls through a private `_run(coro)` helper that wraps `asyncio.run(...)`. Async-ness never
escapes the adapter.

**Mapping isolation:** all GraphQL-dict → model translation lives in small private
`_to_transaction` / `_to_account` / `_to_balance` functions, unit-tested against canned dicts.

**Typed errors:** a `SessionExpiredError(RuntimeError)` is raised when the library reports
401/403 (expired/invalid token). Any other library exception propagates unchanged so
`DataService`'s existing stale-on-failure path catches it.

### `login_cli.py` — one-time terminal login

Entry point already declared in `pyproject.toml` as `financial-mcp-login`. Interactive, run once
by the user in a terminal — never by Claude, never headless.

- **Path 1 — token paste (primary, recommended).** Prompt the user to paste the token from the
  `Authorization: Token XXXX` header in Monarch DevTools. Validate it with a single live
  `get_accounts()` call. On success, `auth.save_token(...)` and print confirmation. On failure,
  print a clear "that token didn't work" message and re-offer.
- **Path 2 — email + password + MFA (fallback).** Read email, password (`getpass`, never echoed,
  never stored). Call `mm.login(...)`; on `RequireMFAException`, prompt for a one-time MFA code
  and call `mm.multi_factor_authenticate(...)`. Extract the resulting session token and
  `auth.save_token(...)`. **The TOTP seed is never requested or stored** (spec §5 — co-locating
  the second factor defeats MFA).
- A `--logout` flag calls `auth.delete_token()`.

The CLI is thin orchestration over `auth` + `monarchmoney`; its logic is covered indirectly by
`auth` and adapter tests plus the manual smoke test (interactive prompts aren't unit-tested).

### Config additions

`config.py` gains two fields (with Plan-1-safe defaults):

- `source: Literal["csv", "live"] = "csv"` — default keeps Plan 1 behavior until explicitly
  flipped.
- `keyring_service_name: str = "financial_mcp_monarch"`.

`config.example.yaml` documents both, with a comment pointing to `financial-mcp-login`.

### `main.py` — adapter selection (decision 3, visible fallback)

```
if cfg.source == "live":
    token = auth.load_token(cfg.keyring_service_name)
    if token:
        adapter = CommunityAdapter(token, cfg.keyring_service_name)
    else:
        logger.warning("no Monarch session — run `financial-mcp-login`; falling back to CSV")
        adapter = _csv_or_exit(cfg)          # CsvAdapter if paths set, else SystemExit w/ instruction
else:  # "csv"
    adapter = _csv_or_exit(cfg)              # Plan 1 behavior, unchanged
```

Everything downstream (`SqliteCache`, `DataService`, `build_server`) is identical to Plan 1.

## 5. Data flow

```
financial-mcp-login (once, terminal)  ──save token──▶  macOS Keychain
                                                             │
Claude Desktop ──stdio──▶ main.py ──load token──────────────┘
                             │
                             ▼
                    CommunityAdapter(token)
                             │ asyncio.run(monarchmoney calls)
                             ▼
                    Monarch API (api.monarch.com)
                             │  dicts → models
                             ▼
        DataService ⇄ SqliteCache (TTL, stale-on-failure)  ──▶  MCP tools ──▶ Claude
```

## 6. Error handling (spec §9)

- **Expired/invalid session (401/403):** adapter raises `SessionExpiredError`. `DataService`'s
  Plan-1 stale-on-failure path serves the last-synced cache with its age; the tool layer maps
  the typed error (when no cache exists) to a plain-language "your Monarch session expired —
  re-run `financial-mcp-login`" message. No raw stack traces to Claude.
- **Network failure / Monarch outage / rate-limit (429) / Cloudflare (525):** any adapter
  exception → same stale-serve path. Fresh setup with empty cache + unreachable Monarch → the
  tool returns a clear "couldn't reach Monarch and no cached data yet" message.
- **No session on startup in live mode:** visible logged fallback to CSV (§4).

## 7. Session longevity

No `trusted_device` flag exists. Tokens last "months" per the library authors but Monarch has
tightened long-lived sessions (occasional 403). We do not attempt auto-refresh in Plan 2
(storing a TOTP seed for silent re-login weakens MFA and is explicitly out of scope). When a
token dies, the user re-runs `financial-mcp-login`. The stale cache keeps answering read
questions in the meantime.

## 8. Testing (decision 5)

- `test_auth.py` — fake in-memory keyring backend: save/load/delete round-trip, `None` when
  never stored, no real secret or OS Keychain touched.
- `test_community_adapter.py` — stub `monarchmoney` client returning canned GraphQL-shaped
  dicts: asserts (a) correct mapping into `Transaction`/`Balance`/`Account`, (b) the async→sync
  bridge returns real lists, (c) pagination stitches multiple pages, (d) 401/403 →
  `SessionExpiredError`. Injected so no network and no real client are constructed.
- **Manual smoke test (once, documented, never committed):** run `financial-mcp-login` with the
  real account, flip `config.yaml` to `source: live`, and confirm `get_accounts()` /
  `get_transactions()` return the real data — the one thing units can't cover (real wire shape
  matching our stubs). Written up in `docs/INSTALL.md` (Plan 2 section), mirroring Plan 1's
  Task-11 smoke step.
- Coverage target ≥90% on `auth` and adapter mapping/bridge logic (per spec §12).

## 9. Security & git discipline

- Credentials live **only** in the macOS Keychain via `keyring`. Password is read with `getpass`
  and never stored, never logged. Token is never logged in cleartext.
- The `monarchmoney` library's default session pickle path (`.mm/mm_session.pickle`) is
  **git-ignored** (add `.mm/` to `.gitignore`). We prefer the Keychain over the pickle, but
  ignore the pickle defensively in case the library writes it.
- No real CSVs, no `config.yaml`, no DB committed (already ignored in Plan 1).
- Clean commit per task, standalone history in `Monarch MCP/`.

## 10. Definition of Done

Adam can: run `financial-mcp-login` once (token paste or email+pw+MFA), have only the session
token stored in Keychain; set `source: live` in `config.yaml`; launch the server and have Claude
answer account/transaction/balance questions from **live Monarch data** cached locally; keep
getting stale-with-age answers when Monarch is down or the token expires; and get a clear
re-login instruction when the session dies — all locally, with the credential path fully
isolated behind the adapter and covered by secret-free tests plus one documented manual smoke
test.

## 11. Out of scope (Plan 3+)

All richer entities (budgets, cashflow, net worth, holdings, categories, merchants, recurring,
subscriptions, forecasting) and their models/tools/analytics; historical balance sync via
`get_account_history`; the `transport.py` remote abstraction; and all write operations
(Plan 4).
