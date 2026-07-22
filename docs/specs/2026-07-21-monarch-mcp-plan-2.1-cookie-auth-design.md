# Monarch MCP — Plan 2.1 Design: Cookie-Based Auth

**Date:** 2026-07-21
**Author:** Adam Frank (with Claude)
**Status:** Approved design, pending implementation plan
**Supersedes:** the auth mechanism in `docs/specs/2026-07-17-monarch-mcp-plan-2-auth-design.md`
(token auth). Everything else in Plan 2 — the adapter seam, cache, DataService,
tools, `source: csv|live` config, three-entity scope — stands unchanged.
**Parent spec:** `docs/specs/2026-07-15-monarch-mcp-server-design.md`

---

## 1. Why this exists

Plan 2 shipped token-based auth via `monarchmoney==0.1.15`. It never worked live:
**Monarch decommissioned the API domain the library targets.** Confirmed by probe
on 2026-07-21:

- `POST https://api.monarchmoney.com/auth/login/` → **405 Method Not Allowed** (route gone).
- `POST https://api.monarch.com/auth/login/` → **403, `{"You":"Shall Not Pass"}`, `Server: cloudflare`** —
  Cloudflare blocks credential-free login attempts before they reach the API.

Monarch's web app is **cookie-session based** (no `Authorization: Token` header exists
for this account; the user signs in via Google SSO), and Monarch has paused their own
first-party MCP pending a "data portability agreement" — they are tightening, not
opening, third-party access. Token auth is therefore a dead end.

## 2. What the feasibility spike proved (2026-07-21)

A throwaway spike replayed a real browser GraphQL request from a plain Python `aiohttp`
client on the user's Mac. Results (all secret-free, cookies since deleted):

| Test | Result |
|------|--------|
| Replay full browser request from Python | **HTTP 200, real authenticated `me` data** — passes Cloudflare |
| Minimum cookies needed | **`session_id` + `csrftoken` only** |
| `cf_clearance` required? | **No** — dropped, still 200 |
| `__cf_bm` required? | **No** — dropped, still 200 |
| `x-csrftoken` header required? | **Yes** — omit → 403 `CSRF Failed: CSRF token missing` |
| `origin: https://app.monarch.com` header required? | **Yes** — omit → 403 `CSRF Failed: Referer checking failed` |
| Bot User-Agent (library default) accepted? | **Yes** — even the library UA got 200 |
| `session_id` cookie alone (no csrftoken) | 403 `CSRF cookie not set` |

**Conclusion:** cookie auth is viable and *simpler than feared* — no fighting Cloudflare's
rotating `cf_clearance`. Two cookies + two headers, pointed at `api.monarch.com`, is the
whole contract. This removes the last blocker to live use.

## 3. Design decisions (locked with user)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Transport | **Own the GraphQL wire path** — a small `aiohttp` POST helper inside the adapter. No library auth/transport. |
| 2 | `monarchmoney` dependency | **Drop entirely.** Copy the two query strings we need into our own `queries.py` (credited, MIT). |
| 3 | Stored secrets | **`session_id` + `csrftoken`**, as one JSON blob under the existing single Keychain key. |
| 4 | Login UX (this plan) | **Paste "Copy as cURL"** from DevTools Network tab; we parse out the two cookies. Interim, dev-grade. |
| 5 | Login UX (committed follow-up, Plan 2.2) | **Browser-driven login** — `financial-mcp-login` opens a real browser, user signs in with Google as normal, we read the resulting session cookies. The "OAuth look and feel." See §10. |
| 6 | Entity scope | Unchanged from Plan 2 — **accounts, transactions, balances only.** Richer entities → Plan 3. |
| 7 | Error distinction | **Expired session** (403 + JSON `detail`) and **Cloudflare block** (403 + `"Shall Not Pass"`) surface as *distinct* messages. |

**On OAuth:** true OAuth is not available — Monarch runs no public authorization server
for third parties. Cookie-session auth is the closest legitimate mechanism; the browser-driven
login (Plan 2.2) is the closest achievable approximation of an OAuth *feel*. This is recorded
so the goal isn't mistaken for a gap in this design.

## 4. Architecture

The adapter seam does its job: the only files that change are the auth/transport layer plus
the login CLI and pyproject. Models, cache, `DataService`, analytics, and all MCP tools are
untouched.

### Files

```
financial_mcp/
├── monarch/
│   ├── auth.py               ← MODIFY: store session dict (session_id + csrftoken), not a token
│   ├── queries.py            ← NEW: GraphQL query strings (GetAccounts, GetTransactionsList)
│   └── community_adapter.py  ← MODIFY: aiohttp GraphQL transport w/ cookie auth; mappers unchanged
├── login_cli.py              ← MODIFY: cURL-paste flow; parse + validate + save
├── main.py                   ← MODIFY: load session dict instead of token
tests/
├── test_auth.py              ← MODIFY: session dict round-trip
├── test_community_adapter.py ← MODIFY: stub aiohttp transport; mapping/pagination/bridge/errors
└── test_login_cli.py         ← NEW: cURL-blob parser unit tests (secret-free synthetic blob)
pyproject.toml                ← MODIFY: remove monarchmoney; add aiohttp as direct dep
```

### `auth.py` — session storage

Replace the token API with a session-dict API. Store as JSON under the existing service +
username key so nothing else in config changes:

- `save_session(session_id: str, csrftoken: str, service_name=DEFAULT_SERVICE) -> None`
- `load_session(service_name=DEFAULT_SERVICE) -> dict | None` — `{"session_id","csrftoken"}` or `None`.
- `delete_session(service_name=DEFAULT_SERVICE) -> None` — for logout; no-op if absent.

Never logs values. Tests use an in-memory fake keyring backend. `TOKEN_USERNAME` becomes
`SESSION_USERNAME = "monarch_session"` (a distinct key; a stale Plan-2 token entry is simply
ignored and can be cleared with `--logout`).

### `queries.py` — the Monarch contract, owned by us

Two module-level string constants copied verbatim from `monarchmoney` 0.1.15 (MIT, hammem),
with a header comment crediting the source:

- `GET_ACCOUNTS` — `query GetAccounts { accounts { ...AccountFields } ... }` (fragment includes
  `displayName`, `currentBalance`, `type`, `subtype`, etc.).
- `GET_TRANSACTIONS` — `query GetTransactionsList($offset,$limit,$filters,$orderBy) { allTransactions { totalCount results { ...TransactionOverviewFields } } }`
  (fragment includes `id`, `amount`, `date`, `plaidName`, `notes`, `merchant.name`,
  `category.name`, `account.displayName`, `tags[].name`).

**Verified against the live fragments:** every field our existing `_to_account` / `_to_transaction`
mappers read is present, so the mappers are unchanged. `owner` is absent from the transaction
fragment — the current `d.get("owner") or ""` fallback already handles that.

### `community_adapter.py` — cookie transport

Constructor takes the session dict instead of a token:

```
CommunityAdapter(
    session: dict,                       # {"session_id","csrftoken"}
    service_name: str = auth.DEFAULT_SERVICE,
    transport: Callable[..., Awaitable[dict]] | None = None,  # injectable for tests
    today: Callable[[], datetime.date] = datetime.date.today,
    page_size: int = 100,
)
```

A private async helper `_gql(operation, query, variables) -> dict` performs the POST via a
single shared `aiohttp.ClientSession`:

- URL: `https://api.monarch.com/graphql` (module constant `GRAPHQL_URL`).
- Cookies: `session_id`, `csrftoken`.
- Headers: `x-csrftoken` (= csrftoken value), `origin: https://app.monarch.com`,
  `client-platform: web`, `content-type: application/json`, `accept: */*`, and a
  browser-like `user-agent` (module constant — a real Chrome UA string, not the library's
  bot UA, to stay clear of any future Cloudflare UA rule; the spike showed either works today).
- Body: `{"operationName", "variables", "query"}`.

Response handling:
- **HTTP 200 + `data`** → return `data`. If `data` is null and `errors` present → raise a
  plain `RuntimeError` with the GraphQL error (propagates to DataService stale-serve path).
- **HTTP 403 + Cloudflare `"Shall Not Pass"` body** → raise `CloudflareBlockedError` (new,
  distinct) — "Monarch's Cloudflare blocked the request; your session may need refreshing
  via `financial-mcp-login`."
- **HTTP 401/403 otherwise** (JSON `detail`, CSRF/auth failures) → raise `SessionExpiredError`
  — "Monarch session expired — re-run `financial-mcp-login`."
- Other non-200 → plain `RuntimeError` (stale-serve path).

`SessionExpiredError(RuntimeError)` stays; `CloudflareBlockedError(RuntimeError)` is added.
The loop-safe `_run` bridge (thread-offload when a loop is already running — the Plan-2
critical fix) is **retained verbatim**; FastMCP still calls sync tools on its loop. Pagination
and the mappers are retained. The `transport` seam replaces the old `client_factory` seam so
tests inject canned responses without a real `aiohttp` client.

- `source_name() -> "live"` (unchanged).
- `get_accounts()` → `_gql("GetAccounts", GET_ACCOUNTS, {})` → `data["accounts"]` → mappers.
- `get_balances()` → derived from `get_accounts()` (unchanged).
- `get_transactions()` → paginate `_gql("GetTransactionsList", GET_TRANSACTIONS, {offset,limit,orderBy:"date",filters:{...}})`,
  reading `data["allTransactions"]["results"]` / `totalCount` (same loop as Plan 2, now with a
  `totalCount`-absent guard — a deferred Plan-2 minor, folded in here).

### `login_cli.py` — cURL-paste flow

Menu collapses to one primary path plus `--logout`:

1. Prompt: "Paste the 'Copy as cURL' of any api.monarch.com/graphql request from DevTools
   Network tab, then press Ctrl-D:". Read the full blob from **stdin until EOF** (not a single
   line — the blob is multi-line).
2. Parse it with `parse_curl_cookies(blob) -> dict` (the spike's proven parser, promoted to a
   tested function): extract `session_id` + `csrftoken` from the `-b`/`--cookie` arg or a
   `Cookie:` header. Error clearly if either is missing.
3. Validate: one live `me` query through the adapter's transport. On success, `auth.save_session(...)`
   and print confirmation. On failure, print a clear, secret-free message and exit non-zero.
4. `--logout` → `auth.delete_session()`.

The blob is never echoed; only `session_id` + `csrftoken` are extracted, everything else
(tracking cookies, etc.) is discarded and never stored.

### `main.py` — adapter selection

```
if cfg.source == "live":
    session = auth.load_session(cfg.keyring_service_name)
    if session:
        adapter = CommunityAdapter(session, cfg.keyring_service_name)
    else:
        logger.warning("no Monarch session — run `financial-mcp-login`; falling back to CSV")
        adapter = _csv_or_exit(cfg)
else:
    adapter = _csv_or_exit(cfg)
```

Everything downstream is identical to Plan 1/2.

## 5. Data flow

```
financial-mcp-login (once, terminal)  ──save {session_id,csrftoken}──▶  macOS Keychain
                                                                             │
Claude Desktop ──stdio──▶ main.py ──load session──────────────────────────────┘
                             │
                             ▼
                    CommunityAdapter(session)
                             │ aiohttp POST (cookies + x-csrftoken + origin)
                             ▼
                    api.monarch.com/graphql
                             │  data → models
                             ▼
        DataService ⇄ SqliteCache (TTL, stale-on-failure)  ──▶  MCP tools ──▶ Claude
```

## 6. Error handling

- **Expired/invalid session (401/403 + JSON detail):** `SessionExpiredError`. DataService's
  stale-on-failure path serves last-synced cache with age; the tool layer maps the typed error
  (when no cache) to "your Monarch session expired — re-run `financial-mcp-login`."
- **Cloudflare block (403 + "Shall Not Pass"):** `CloudflareBlockedError`, distinct message.
  Should not occur with a valid session, but named honestly so a future Monarch tightening is
  diagnosable rather than mystifying.
- **Network failure / outage / 429 / 5xx:** plain exception → stale-serve path. Empty cache +
  unreachable → clear "couldn't reach Monarch and no cached data yet."

## 7. Session longevity

`session_id` lasts as long as Monarch's web session (reportedly months). No auto-refresh
(would require storing Google credentials — out of scope, defeats MFA). When it dies, re-run
`financial-mcp-login`. Stale cache keeps answering in the meantime. Plan 2.2's browser login
makes the re-auth painless.

## 8. Testing

- `test_auth.py` — fake in-memory keyring: save/load/delete session-dict round-trip; `None`
  when never stored; no OS Keychain or real secret touched.
- `test_community_adapter.py` — injected stub `transport` returning canned GraphQL dicts:
  (a) correct mapping into `Account`/`Balance`/`Transaction`; (b) async→sync bridge returns
  real lists **including from within a running event loop** (the Plan-2 regression test,
  retained); (c) pagination stitches multiple pages and terminates on `totalCount`;
  (d) 401/403-detail → `SessionExpiredError`; (e) Cloudflare body → `CloudflareBlockedError`.
- `test_login_cli.py` — `parse_curl_cookies` against a **synthetic** cURL blob (fake cookie
  values): extracts both cookies; raises a clear error when either is missing; handles both
  `-b` arg and `Cookie:` header forms.
- **Manual smoke test (once, documented):** run `financial-mcp-login` with a real cURL paste,
  set `source: live`, confirm `get_accounts()`/`get_transactions()` return real data through
  Claude Desktop. This is the connectivity proof the whole plan exists to reach.
- Coverage ≥90% on `auth`, adapter mapping/bridge, and the parser.

## 9. Security & git discipline

- Secrets (`session_id`, `csrftoken`) live **only** in macOS Keychain via `keyring`. Never
  logged. cURL blob read from stdin, never echoed; only the two needed cookies extracted, the
  rest discarded.
- No real cookies, `config.yaml`, or DB committed (all already git-ignored). The spike's
  `.cookie-probe/` is git-ignored and deleted.
- Clean commit per task.

## 10. Definition of Done (this plan + the committed follow-up)

**Plan 2.1 (this plan) is done when** Adam can: run `financial-mcp-login`, paste a Copy-as-cURL
blob, have only `session_id` + `csrftoken` stored in Keychain; set `source: live`; launch the
server and have **his personal Claude Desktop answer account/transaction/balance questions from
live Monarch data**; get stale-with-age answers when Monarch is down; and get a clear re-login
instruction when the session dies — all locally, covered by secret-free tests plus one manual
smoke test that now actually succeeds.

**Plan 2.2 (committed next) — browser-driven login.** `financial-mcp-login` (or a new flag)
opens a real browser via Playwright; Adam signs in through Google exactly as he normally does
(MFA included); on landing in Monarch, the script reads `session_id` + `csrftoken` from that
browser session and saves them to Keychain — **no DevTools, no cURL, no copying.** This is the
"OAuth look and feel" goal. It reuses this plan's Keychain storage, adapter, and transport
unchanged — it is purely a friendlier way to *fill* the Keychain. It adds a Playwright
dependency and therefore a security review per Adam's skill-installation protocol, which is why
it is a separate plan rather than folded in here. **The auth arc is not considered complete
until 2.2 ships.**

## 11. Out of scope

Richer entities and their models/tools/analytics; historical balance sync; writes (Plan 4);
the Playwright browser-login itself (Plan 2.2). This plan is the cookie foundation and the
interim login only.
